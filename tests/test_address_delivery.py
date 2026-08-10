"""Unit tests for Address/Courier Delivery flow and callbacks."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.ai.schemas import ParsedRecipientInfo
from src.bot.keyboards import AddressConfirmCallback, WaybillActionCallback
from src.bot.handlers import (
    PENDING_SESSIONS,
    USER_ACTIVE_SESSIONS,
    router,
)
from src.config import Settings
from src.nova_poshta.models import (
    CityInfo,
    StreetInfo,
    AddressSaveResult,
    CounterpartyRecipientResult,
    WaybillCreateResult,
)
from src.storage import UserCustomSettings, UserSettingsManager
from src.bot.handlers import register_handlers


def _get_handler(name: str):
    for h in reversed(router.callback_query.handlers):
        if name in str(h.callback):
            return h.callback
    raise RuntimeError(f"Handler {name} not found in router")


@pytest.fixture(autouse=True)
def setup_test_handlers(tmp_path):
    storage_file = str(tmp_path / "user_settings.json")
    drafts_file = str(tmp_path / "user_drafts.json")
    scansheets_file = str(tmp_path / "user_scansheets.json")
    manager = UserSettingsManager(
        filepath=storage_file,
        drafts_filepath=drafts_file,
        scansheets_filepath=scansheets_file,
    )
    register_handlers(
        settings=Settings(TELEGRAM_BOT_TOKEN="dummy"),
        ai_extractor=MagicMock(),
        np_client=MagicMock(),
        storage_manager=manager,
    )
    return manager


@pytest.mark.asyncio
async def test_process_address_confirm_callback_uses_user_id(setup_test_handlers):
    manager = setup_test_handlers
    user_id = 12345678
    session_id = "test-sess-addr"

    # Configure user in storage
    manager.update_user_settings(
        user_id,
        nova_poshta_api_key="test_np_key",
        ai_api_key="test_ai_key",
    )

    parsed_info = ParsedRecipientInfo(
        first_name="Арсеній",
        last_name="Жупник",
        middle_name="Олександрович",
        phone="+380502850704",
        city_name="Сміла",
        region_name="Черкаська",
        street_name="Віри Гордієнко",
        building_number="99",
        is_address_delivery=False,
        has_address_suspicion=True,
        is_recipient_info=True,
    )

    PENDING_SESSIONS[session_id] = {
        "parsed_info": parsed_info,
        "user_id": user_id,
    }
    USER_ACTIVE_SESSIONS[user_id] = session_id

    # Mock Telegram callback where callback.from_user is user, callback.message.from_user is bot (id 999999)
    callback = MagicMock()
    callback.from_user.id = user_id
    callback.message.from_user.id = 999999
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()

    callback_data = AddressConfirmCallback(action="choice", choice="courier", session_id=session_id)

    with patch("src.nova_poshta.client.NovaPoshtaClient.search_city", new_callable=AsyncMock) as mock_search_city, \
         patch("src.nova_poshta.client.NovaPoshtaClient.search_street", new_callable=AsyncMock) as mock_search_street:

        mock_search_city.return_value = [
            CityInfo(Ref="city-smila-ref", Description="Сміла", AreaDescription="Черкаська", RegionsDescription="")
        ]
        mock_search_street.return_value = [
            StreetInfo(Ref="street-vg-ref", Description="Віри Гордієнко", StreetsType="вул.", CityRef="city-smila-ref")
        ]

        handler = _get_handler("process_address_confirm_callback")
        await handler(callback, callback_data)

        assert PENDING_SESSIONS[session_id]["is_address_delivery"] is True
        assert PENDING_SESSIONS[session_id]["street_ref"] == "street-vg-ref"
        assert callback.message.edit_text.called
        call_args = callback.message.edit_text.call_args[0][0]
        assert "🏡 Адресна доставка" in call_args
        assert "вул. Віри Гордієнко" in call_args
        assert "буд. 99" in call_args


@pytest.mark.asyncio
async def test_confirm_waybill_address_delivery_creates_doors_waybill(setup_test_handlers):
    manager = setup_test_handlers
    user_id = 12345678
    session_id = "test-sess-confirm"

    manager.update_user_settings(
        user_id,
        nova_poshta_api_key="test_np_key",
        ai_api_key="test_ai_key",
    )

    parsed_info = ParsedRecipientInfo(
        first_name="Арсеній",
        last_name="Жупник",
        middle_name="Олександрович",
        phone="+380502850704",
        city_name="Сміла",
        street_name="Віри Гордієнко",
        building_number="99",
        flat_number="12",
        is_address_delivery=True,
        is_recipient_info=True,
    )

    city = CityInfo(Ref="city-smila-ref", Description="Сміла", AreaDescription="Черкаська", RegionsDescription="")

    PENDING_SESSIONS[session_id] = {
        "parsed_info": parsed_info,
        "city": city,
        "warehouse": None,
        "is_address_delivery": True,
        "street_name": "Віри Гордієнко",
        "street_ref": "street-vg-ref",
        "building_number": "99",
        "flat_number": "12",
        "destination_description": "🏡 Адресна доставка: вул. Віри Гордієнко, буд. 99, кв. 12",
        "payer_type": "Recipient",
        "cargo_type": "Parcel",
        "declared_value": 500.0,
        "cargo_description": "Посилка",
        "cod_amount": 0.0,
        "cod_payment_type": "cash",
        "user_id": user_id,
    }
    USER_ACTIVE_SESSIONS[user_id] = session_id

    callback = MagicMock()
    callback.from_user.id = user_id
    callback.message.from_user.id = 999999
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()

    callback_data = WaybillActionCallback(
        action="confirm",
        session_id=session_id,
    )

    with patch("src.nova_poshta.client.NovaPoshtaClient.create_recipient_counterparty", new_callable=AsyncMock) as mock_create_cp, \
         patch("src.nova_poshta.client.NovaPoshtaClient.create_counterparty_address", new_callable=AsyncMock) as mock_create_addr, \
         patch("src.nova_poshta.client.NovaPoshtaClient.create_waybill", new_callable=AsyncMock) as mock_create_wb:

        mock_create_cp.return_value = CounterpartyRecipientResult(
            counterparty_ref="cp-ref-123",
            contact_person_ref="contact-ref-123",
        )
        mock_create_addr.return_value = AddressSaveResult(
            Ref="addr-guid-created",
            Description="вул. Віри Гордієнко, 99, кв. 12",
        )
        mock_create_wb.return_value = WaybillCreateResult(
            int_doc_number="20450999999999",
            ref="doc-ref-created",
            cost=125.0,
            estimated_delivery_date="12.08.2026",
        )

        handler = _get_handler("process_waybill_callback")
        await handler(callback, callback_data)

        assert mock_create_addr.called
        assert mock_create_addr.call_args[1]["counterparty_ref"] == "cp-ref-123"
        assert mock_create_addr.call_args[1]["street_ref"] == "street-vg-ref"
        assert mock_create_addr.call_args[1]["building_number"] == "99"
        assert mock_create_addr.call_args[1]["flat"] == "12"

        assert mock_create_wb.called
        assert mock_create_wb.call_args[1]["recipient_warehouse_ref"] == "addr-guid-created"
        assert mock_create_wb.call_args[1]["service_type"] == "WarehouseDoors"

        assert callback.message.edit_text.called
        success_card = callback.message.edit_text.call_args[0][0]
        assert "20450999999999" in success_card
        assert "Адресна доставка" in success_card
        assert mock_create_wb.call_args[1]["service_type"] == "WarehouseDoors"

        assert callback.message.edit_text.called
        success_card = callback.message.edit_text.call_args[0][0]
        assert "20450999999999" in success_card
        assert "Адресна доставка" in success_card
