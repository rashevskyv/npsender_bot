"""Unit tests for Cash On Delivery (COD) tracking, monthly limits, and dashboard."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.config import Settings
from src.nova_poshta.client import NovaPoshtaClient
from src.nova_poshta.models import CODItemInfo, CODMonthlyStats
from src.storage import UserSettingsManager, UserCustomSettings
from src.bot.handlers import (
    _render_progress_bar,
    format_cod_dashboard,
    format_cod_shipments_page,
)


@pytest.fixture
def mock_settings():
    return Settings(
        TELEGRAM_BOT_TOKEN="test_token",
        NOVA_POSHTA_API_KEY="test_np_key",
        AI_API_KEY="test_ai_key",
        sender_phone="380991112233",
        sender_counterparty_ref="sender-cp-123",
    )


@pytest.mark.asyncio
async def test_get_monthly_cod_stats_parsing(mock_settings):
    client = NovaPoshtaClient(mock_settings)

    mock_docs = [
        # 1. Received COD shipment (cash)
        {
            "IntDocNumber": "20450000000001",
            "Ref": "ref-1",
            "DateTime": "2026-08-05 10:00:00",
            "StateId": "9",
            "StateName": "Отримано",
            "RecipientDescription": "Петренко Петро",
            "CityRecipientDescription": "Львів",
            "Description": "Одяг",
            "BackwardDeliveryData": [
                {
                    "CargoType": "Money",
                    "RedeliveryString": "1500",
                }
            ],
            "Sender": "sender-cp-123",
        },
        # 2. In-transit COD shipment (card)
        {
            "IntDocNumber": "20450000000002",
            "Ref": "ref-2",
            "DateTime": "2026-08-10 12:00:00",
            "StateId": "4",
            "StateName": "Прямує до міста",
            "RecipientDescription": "Іванов Іван",
            "CityRecipientDescription": "Київ",
            "Description": "Взуття",
            "BackwardDeliveryData": [
                {
                    "CargoType": "Money",
                    "RedeliveryString": "2500",
                    "RedeliveryPaymentCard": "414932******1234",
                }
            ],
            "Sender": "sender-cp-123",
        },
        # 3. Refused COD shipment
        {
            "IntDocNumber": "20450000000003",
            "Ref": "ref-3",
            "DateTime": "2026-08-12 14:00:00",
            "StateId": "103",
            "StateName": "Відмова від отримання",
            "RecipientDescription": "Сидоров Сидір",
            "CityRecipientDescription": "Одеса",
            "Description": "Книга",
            "AfterpaymentOnGoodsCost": "500",
            "Sender": "sender-cp-123",
        },
        # 4. Draft COD shipment
        {
            "IntDocNumber": "20450000000004",
            "Ref": "ref-4",
            "DateTime": "2026-08-15 16:00:00",
            "StateId": "1",
            "StateName": "Створено чернетку",
            "RecipientDescription": "Коваленко Олена",
            "CityRecipientDescription": "Харків",
            "Description": "Косметика",
            "RedeliveryString": "800",
            "Sender": "sender-cp-123",
        },
        # 5. Non-COD shipment (should be skipped)
        {
            "IntDocNumber": "20450000000005",
            "Ref": "ref-5",
            "DateTime": "2026-08-16 18:00:00",
            "StateId": "9",
            "StateName": "Отримано",
            "RecipientDescription": "Мельник Тарас",
            "CityRecipientDescription": "Дніпро",
            "Description": "Подарунок",
            "Sender": "sender-cp-123",
        },
        # 6. Deleted shipment (should be skipped)
        {
            "IntDocNumber": "20450000000006",
            "Ref": "ref-6",
            "DateTime": "2026-08-17 19:00:00",
            "StateId": "2",
            "StateName": "Видалено",
            "DeletionMark": True,
            "RedeliveryString": "3000",
            "Sender": "sender-cp-123",
        },
    ]

    with patch.object(client, "_post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {"success": True, "data": mock_docs}

        stats = await client.get_monthly_cod_stats(year=2026, month=8)

        assert stats.year == 2026
        assert stats.month == 8
        assert "Серпень 2026" in stats.month_name
        assert stats.from_date == "01.08.2026"
        assert stats.to_date == "31.08.2026"

        # 4 COD shipments total (1 skipped non-COD, 1 skipped deleted)
        assert stats.total_count == 4
        assert stats.total_sum == 1500.0 + 2500.0 + 500.0 + 800.0  # 5300.0

        # Received
        assert stats.received_count == 1
        assert stats.received_sum == 1500.0

        # In transit
        assert stats.in_transit_count == 1
        assert stats.in_transit_sum == 2500.0

        # Refused
        assert stats.refused_count == 1
        assert stats.refused_sum == 500.0

        # Drafts
        assert stats.drafts_count == 1
        assert stats.drafts_sum == 800.0

        # Check items list
        assert len(stats.items) == 4
        assert stats.items[0].int_doc_number == "20450000000001"
        assert stats.items[0].is_received is True
        assert stats.items[0].cod_payment_type == "cash"

        assert stats.items[1].int_doc_number == "20450000000002"
        assert stats.items[1].is_in_transit is True
        assert stats.items[1].cod_payment_type == "card"


def test_progress_bar_rendering():
    # 0%
    bar_0 = _render_progress_bar(0, 30000)
    assert "0%" in bar_0
    assert "⬜" in bar_0

    # 50%
    bar_50 = _render_progress_bar(15000, 30000)
    assert "50%" in bar_50
    assert "🟩" in bar_50

    # 85% (near limit -> warning icons)
    bar_85 = _render_progress_bar(25500, 30000)
    assert "85%" in bar_85
    assert "🟨" in bar_85

    # 100%+ (exceeded -> red, shows actual percentage)
    bar_100 = _render_progress_bar(32000, 30000)
    assert "106%" in bar_100
    assert "🟥" in bar_100


    # No limit
    bar_none = _render_progress_bar(10000, None)
    assert bar_none == "Без ліміту"


def test_extract_float_amount():
    from src.nova_poshta.client import _extract_float_amount
    assert _extract_float_amount(1500) == 1500.0
    assert _extract_float_amount(2500.50) == 2500.50
    assert _extract_float_amount("3000") == 3000.0
    assert _extract_float_amount("3 500,00 грн") == 3500.00
    assert _extract_float_amount("12.500") == 12.5
    assert _extract_float_amount(None) == 0.0
    assert _extract_float_amount("") == 0.0


def test_format_cod_dashboard():
    stats = CODMonthlyStats(
        year=2026,
        month=8,
        month_name="Серпень 2026",
        from_date="01.08.2026",
        to_date="31.08.2026",
        total_count=6,
        total_sum=18500.0,
        received_count=4,
        received_sum=12000.0,
        in_transit_count=2,
        in_transit_sum=6500.0,
        refused_count=0,
        refused_sum=0.0,
        drafts_count=0,
        drafts_sum=0.0,
    )
    user_settings = UserCustomSettings(
        cod_monthly_limit_sum=30000.0,
        cod_monthly_limit_count=10,
    )

    report = format_cod_dashboard(stats, user_settings)
    assert "Звіт накладеного платежу за Серпень 2026" in report
    assert "18500 грн" in report
    assert "29999 грн" in report
    assert "6 шт" in report
    assert "10 шт" in report
    assert "Залишок до безпечного ліміту" in report
    assert "11499 грн" in report
    assert "01.09.2026" in report


def test_format_cod_dashboard_exceeded():
    stats = CODMonthlyStats(
        year=2026,
        month=8,
        month_name="Серпень 2026",
        from_date="01.08.2026",
        to_date="31.08.2026",
        total_count=12,
        total_sum=35000.0,
        received_count=10,
        received_sum=30000.0,
        in_transit_count=2,
        in_transit_sum=5000.0,
    )
    user_settings = UserCustomSettings(
        cod_monthly_limit_sum=30000.0,
        cod_monthly_limit_count=10,
    )

    report = format_cod_dashboard(stats, user_settings)
    assert "ПЕРЕВИЩЕНО на 5001 грн" in report
    assert "ПЕРЕВИЩЕНО на 2 шт" in report



def test_format_cod_shipments_pagination():
    items = [
        CODItemInfo(
            int_doc_number=f"2045000000000{i}",
            date_created="2026-08-10",
            cod_amount=1000.0 * i,
            cod_payment_type="card" if i % 2 == 0 else "cash",
            state_id="9" if i == 1 else "4",
            state_name="Отримано" if i == 1 else "У дорозі",
            recipient_name=f"Отримувач {i}",
            city_recipient="Київ",
            is_received=i == 1,
            is_in_transit=i > 1,
        )
        for i in range(1, 8)
    ]
    stats = CODMonthlyStats(
        year=2026,
        month=8,
        month_name="Серпень 2026",
        from_date="01.08.2026",
        to_date="31.08.2026",
        total_count=len(items),
        total_sum=sum(item.cod_amount for item in items),
        items=items,
    )

    # Page 1 (items 1 to 5)
    page_1 = format_cod_shipments_page(stats, page=0, page_size=5)
    assert "Показано 1-5 із 7" in page_1
    assert "20450000000001" in page_1
    assert "20450000000005" in page_1
    assert "20450000000006" not in page_1

    # Page 2 (items 6 to 7)
    page_2 = format_cod_shipments_page(stats, page=1, page_size=5)
    assert "Показано 6-7 із 7" in page_2
    assert "20450000000006" in page_2
    assert "20450000000007" in page_2


def test_user_settings_manager_cod_limits(tmp_path):
    storage_file = tmp_path / "user_settings.json"
    drafts_file = tmp_path / "user_drafts.json"
    manager = UserSettingsManager(filepath=str(storage_file), drafts_filepath=str(drafts_file))

    # Default limits
    cfg = manager.get_user_settings(111)
    assert cfg.cod_monthly_limit_sum == 30000.0
    assert cfg.cod_monthly_limit_count == 10
    assert cfg.cod_warning_enabled is True

    # Update sum limit and disable warnings
    manager.update_user_settings(111, cod_monthly_limit_sum=50000.0, cod_warning_enabled=False)
    updated = manager.get_user_settings(111)
    assert updated.cod_monthly_limit_sum == 50000.0
    assert updated.cod_monthly_limit_count == 10
    assert updated.cod_warning_enabled is False

    # Reload from disk
    new_manager = UserSettingsManager(filepath=str(storage_file), drafts_filepath=str(drafts_file))
    reloaded = new_manager.get_user_settings(111)
    assert reloaded.cod_monthly_limit_sum == 50000.0
    assert reloaded.cod_warning_enabled is False
