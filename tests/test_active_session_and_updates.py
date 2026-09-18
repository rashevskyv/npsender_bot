"""Unit tests for active parcel session management, 15-minute TTL, city disambiguation fix and field updates."""

import asyncio
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.config import Settings
from src.ai.schemas import ParsedRecipientInfo
from src.ai.extractor import AIExtractor
from src.bot.keyboards import (
    CitySelectCallback,
    WaybillActionCallback,
)
from src.bot.handlers import (
    PENDING_SESSIONS,
    USER_ACTIVE_SESSIONS,
    USER_LAST_PARSED_INFO,
    SESSION_TIMEOUT_SECONDS,
    _cleanup_expired_sessions,
    get_user_active_session_id,
    clear_user_active_session,
    register_handlers,
    router,
)
from src.nova_poshta.models import (
    CityInfo,
    WarehouseInfo,
)
from src.storage import UserSettingsManager


def _get_callback_handler(name: str):
    for h in reversed(router.callback_query.handlers):
        if name in str(h.callback):
            return h.callback
    raise RuntimeError(f"Handler {name} not found in router")


def _get_message_handler(name: str):
    for h in reversed(router.message.handlers):
        if name in str(h.callback):
            return h.callback
    raise RuntimeError(f"Handler {name} not found in router")


@pytest.fixture(autouse=True)
def setup_handlers(tmp_path):
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
    PENDING_SESSIONS.clear()
    USER_ACTIVE_SESSIONS.clear()
    USER_LAST_PARSED_INFO.clear()
    return manager


def test_heal_parsed_recipient_info_declared_value_and_cargo_desc():
    """Test regex healing of declared value, cargo description, COD, payer and cargo type."""
    text = "Оцінка 50000 грн, в посилці steam deck OLED 512 та Odin 2 portal"
    empty = ParsedRecipientInfo(is_recipient_info=False)
    healed = AIExtractor.heal_parsed_recipient_info(text, empty)

    assert healed.is_recipient_info is True
    assert healed.declared_value == 50000.0
    assert healed.cargo_description == "steam deck OLED 512 та Odin 2 portal"

    # COD test
    text_cod = "наложка 15000 на картку"
    healed_cod = AIExtractor.heal_parsed_recipient_info(text_cod, ParsedRecipientInfo(is_recipient_info=False))
    assert healed_cod.cod_amount == 15000.0
    assert healed_cod.cod_payment_type == "card"

    # COD removal test
    text_no_cod = "без наложки"
    healed_no_cod = AIExtractor.heal_parsed_recipient_info(text_no_cod, ParsedRecipientInfo(is_recipient_info=False))
    assert healed_no_cod.cod_amount == 0.0

    # Payer test
    text_payer = "платник відправник"
    healed_payer = AIExtractor.heal_parsed_recipient_info(text_payer, ParsedRecipientInfo(is_recipient_info=False))
    assert healed_payer.payer_type == "Sender"

    # Cargo type test
    text_cargo = "документи"
    healed_cargo = AIExtractor.heal_parsed_recipient_info(text_cargo, ParsedRecipientInfo(is_recipient_info=False))
    assert healed_cargo.cargo_type == "Documents"


def test_session_15_minute_timeout_and_expiration():
    """Test 15-minute session expiration TTL and auto-cleanup."""
    user_id = 998877
    session_id = "sess-1234"

    now = time.time()
    PENDING_SESSIONS[session_id] = {
        "user_id": user_id,
        "updated_at": now - 100,  # 100 seconds ago (< 900s)
    }
    USER_ACTIVE_SESSIONS[user_id] = session_id

    # Should remain active
    active = get_user_active_session_id(user_id)
    assert active == session_id
    assert PENDING_SESSIONS[session_id]["updated_at"] >= now

    # Now simulate 16 minutes old session (> 900s)
    PENDING_SESSIONS[session_id]["updated_at"] = now - 950
    _cleanup_expired_sessions()

    assert session_id not in PENDING_SESSIONS
    assert user_id not in USER_ACTIVE_SESSIONS
    assert get_user_active_session_id(user_id) is None


@pytest.mark.asyncio
async def test_city_selection_and_follow_up_update_retains_chosen_city(setup_handlers):
    """Test that choosing a city in disambiguation and sending a follow-up text update
    preserves the chosen city/warehouse and does not re-trigger city disambiguation.
    """
    manager = setup_handlers
    user_id = 11223344
    session_id = "sess-lozova"

    manager.update_user_settings(
        user_id,
        nova_poshta_api_key="test_np_key",
        ai_api_key="test_ai_key",
    )

    # Initial parsed info with ambiguous city
    parsed_info = ParsedRecipientInfo(
        first_name="Олександр",
        last_name="Макаров",
        phone="0684782752",
        city_name="Лозова",
        warehouse_number=1,
        is_postomat=False,
    )

    city_lozova_kharkiv = CityInfo(
        Ref="ref-kharkiv-lozova",
        Description="Лозова",
        AreaDescription="Харківська",
        RegionsDescription="",
    )
    city_lozova_ternopil = CityInfo(
        Ref="ref-ternopil-lozova",
        Description="Лозова",
        AreaDescription="Тернопільська",
        RegionsDescription="",
    )
    wh_kharkiv = WarehouseInfo(
        Ref="wh-loz-1",
        Description="Відділення №1: вул. Транспортна, 1",
        Number="1",
        TypeOfWarehouse="Branch",
        CityRef="ref-kharkiv-lozova",
    )
    wh_ternopil = WarehouseInfo(
        Ref="wh-loz-tern-1",
        Description="Відділення №1: вул. Центральна, 5",
        Number="1",
        TypeOfWarehouse="Branch",
        CityRef="ref-ternopil-lozova",
    )

    candidates = [
        (city_lozova_kharkiv, wh_kharkiv),
        (city_lozova_ternopil, wh_ternopil),
    ]

    PENDING_SESSIONS[session_id] = {
        "parsed_info": parsed_info,
        "candidates": candidates,
        "user_id": user_id,
        "updated_at": time.time(),
    }
    USER_ACTIVE_SESSIONS[user_id] = session_id

    # 1. User clicks CitySelectCallback for Kharkiv region
    city_callback_handler = _get_callback_handler("process_city_select_callback")
    mock_cb = MagicMock()
    mock_cb.from_user.id = user_id
    mock_cb.message.edit_text = AsyncMock()
    mock_cb.answer = AsyncMock()

    cb_data = CitySelectCallback(city_ref="ref-kharkiv-lozova", session_id=session_id)
    await city_callback_handler(mock_cb, cb_data)

    # Verify session now has chosen Kharkiv city and warehouse
    session = PENDING_SESSIONS[session_id]
    assert session["city"].ref == "ref-kharkiv-lozova"
    assert session["warehouse"].ref == "wh-loz-1"
    assert "candidates" not in session

    # 2. User sends follow-up text with declared value and item description
    followup_text = "Оцінка 50000 грн, в посилці steam deck OLED 512 та Odin 2 portal"

    # Mock AI extractor returning the updated parcel info
    mock_ai_parsed = ParsedRecipientInfo(
        first_name="Олександр",
        last_name="Макаров",
        phone="0684782752",
        city_name="Лозова",
        warehouse_number=1,
        is_postomat=False,
        declared_value=50000.0,
        cargo_description="steam deck OLED 512 та Odin 2 portal",
    )

    mock_msg = MagicMock()
    mock_msg.from_user.id = user_id
    mock_msg.text = followup_text
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    mock_msg.answer = AsyncMock(return_value=status_msg)

    with patch("src.ai.extractor.AIExtractor.parse_text", new_callable=AsyncMock) as mock_parse, \
         patch("src.nova_poshta.client.NovaPoshtaClient.search_city", new_callable=AsyncMock) as mock_search_city:

        mock_parse.return_value = mock_ai_parsed

        text_handler = _get_message_handler("process_text_message")
        await text_handler(mock_msg)
        await asyncio.sleep(1.1)

        # search_city should NOT be called again because city/warehouse are preserved!
        mock_search_city.assert_not_called()

        # Check session is updated with 50000 value and description, while retaining Kharkiv city
        updated_sess = PENDING_SESSIONS[session_id]
        assert updated_sess["city"].ref == "ref-kharkiv-lozova"
        assert updated_sess["warehouse"].ref == "wh-loz-1"
        assert updated_sess["declared_value"] == 50000.0
        assert updated_sess["cargo_description"] == "steam deck OLED 512 та Odin 2 portal"

        # Check that confirmation card text was updated with new value and description
        card_text_call = status_msg.edit_text.call_args[0][0]
        assert "50000 грн" in card_text_call
        assert "steam deck OLED 512 та Odin 2 portal" in card_text_call
        assert "Лозова" in card_text_call
        assert "Відділення №1: вул. Транспортна, 1" in card_text_call


@pytest.mark.asyncio
async def test_waybill_action_toggle_cod_type_and_cycles_no_unbound_local_error():
    """Regression test: clicking toggle_cod_type, cycle_cod, cycle_value must not raise UnboundLocalError."""
    from src.nova_poshta.models import CODMonthlyStats

    user_id = 999
    session_id = "test-session-cod"

    city = CityInfo(Ref="city-ref-1", Description="Львів")
    wh = WarehouseInfo(
        Ref="wh-ref-1",
        Description="Відділення №1: вул. Городоцька, 1",
        Number="1",
        TypeOfWarehouse="Warehouse",
        CityRef="city-ref-1",
    )
    parsed_info = ParsedRecipientInfo(
        first_name="Костянтин",
        last_name="Черняков",
        phone="380989946045",
        city_name="Львів",
        warehouse_number=1,
        is_postomat=False,
        declared_value=14800.0,
        cargo_description="планшет",
        cod_amount=14800.0,
        cod_payment_type="cash",
    )

    PENDING_SESSIONS[session_id] = {
        "parsed_info": parsed_info,
        "city": city,
        "warehouse": wh,
        "destination_description": "Відділення №1: вул. Городоцька, 1",
        "payer_type": "Recipient",
        "cargo_type": "Parcel",
        "declared_value": 14800.0,
        "cargo_description": "планшет",
        "cod_amount": 14800.0,
        "cod_payment_type": "cash",
        "user_id": user_id,
        "updated_at": time.time(),
    }
    USER_ACTIVE_SESSIONS[user_id] = session_id

    waybill_callback_handler = _get_callback_handler("process_waybill_callback")

    mock_cb = MagicMock()
    mock_cb.from_user.id = user_id
    mock_cb.message.edit_text = AsyncMock()
    mock_cb.message.edit_reply_markup = AsyncMock()
    mock_cb.answer = AsyncMock()

    # 1. Test toggle_cod_type (switch from cash to card)
    cb_toggle = WaybillActionCallback(action="toggle_cod_type", session_id=session_id)
    with patch("src.nova_poshta.client.NovaPoshtaClient.get_monthly_cod_stats", new_callable=AsyncMock) as mock_stats:
        mock_stats.return_value = CODMonthlyStats(
            year=2026, month=9, month_name="Вересень 2026", from_date="01.09.2026", to_date="30.09.2026",
            total_count=1, total_sum=1000.0, items=[]
        )
        await waybill_callback_handler(mock_cb, cb_toggle)
        assert PENDING_SESSIONS[session_id]["cod_payment_type"] == "card"
        mock_cb.message.edit_text.assert_called()

    # 2. Test cycle_cod
    cb_cod = WaybillActionCallback(action="cycle_cod", session_id=session_id)
    with patch("src.nova_poshta.client.NovaPoshtaClient.get_monthly_cod_stats", new_callable=AsyncMock) as mock_stats:
        mock_stats.return_value = CODMonthlyStats(
            year=2026, month=9, month_name="Вересень 2026", from_date="01.09.2026", to_date="30.09.2026",
            total_count=1, total_sum=1000.0, items=[]
        )
        await waybill_callback_handler(mock_cb, cb_cod)
        mock_cb.message.edit_text.assert_called()

    # 3. Test cycle_value
    cb_val = WaybillActionCallback(action="cycle_value", session_id=session_id)
    with patch("src.nova_poshta.client.NovaPoshtaClient.get_monthly_cod_stats", new_callable=AsyncMock) as mock_stats:
        mock_stats.return_value = CODMonthlyStats(
            year=2026, month=9, month_name="Вересень 2026", from_date="01.09.2026", to_date="30.09.2026",
            total_count=1, total_sum=1000.0, items=[]
        )
        await waybill_callback_handler(mock_cb, cb_val)
        mock_cb.message.edit_text.assert_called()


@pytest.mark.asyncio
async def test_waybill_confirm_intercepts_when_limit_exceeded(setup_handlers):
    from src.nova_poshta.models import CODMonthlyStats

    manager = setup_handlers
    user_id = 1001
    session_id = "test-session-exceed"
    manager.update_user_settings(user_id, cod_monthly_limit_sum=30000.0)

    city = CityInfo(Ref="city-ref-1", Description="Київ")
    wh = WarehouseInfo(Ref="wh-ref-1", Description="Відділення №1", Number="1", TypeOfWarehouse="Warehouse", CityRef="city-ref-1")
    parsed_info = ParsedRecipientInfo(
        first_name="Олександр", last_name="Тест", phone="380991234567",
        city_name="Київ", warehouse_number=1, declared_value=15000.0,
        cargo_description="ноутбук", cod_amount=15000.0, cod_payment_type="card",
    )

    PENDING_SESSIONS[session_id] = {
        "parsed_info": parsed_info,
        "city": city,
        "warehouse": wh,
        "destination_description": "Відділення №1",
        "payer_type": "Recipient",
        "cargo_type": "Parcel",
        "declared_value": 15000.0,
        "cargo_description": "ноутбук",
        "cod_amount": 15000.0,
        "cod_payment_type": "card",
        "user_id": user_id,
        "updated_at": time.time(),
    }
    USER_ACTIVE_SESSIONS[user_id] = session_id

    waybill_callback_handler = _get_callback_handler("process_waybill_callback")

    mock_cb = MagicMock()
    mock_cb.from_user.id = user_id
    mock_cb.message.edit_text = AsyncMock()
    mock_cb.answer = AsyncMock()

    mock_stats = CODMonthlyStats(
        year=2026, month=9, month_name="Вересень 2026", from_date="01.09.2026", to_date="30.09.2026",
        total_count=4, total_sum=20000.0, items=[]
    )

    cb_confirm = WaybillActionCallback(action="confirm", session_id=session_id)
    with patch("src.nova_poshta.client.NovaPoshtaClient.get_monthly_cod_stats", new_callable=AsyncMock) as m_stats, \
         patch("src.nova_poshta.client.NovaPoshtaClient.create_waybill", new_callable=AsyncMock) as m_create:
        m_stats.return_value = mock_stats
        await waybill_callback_handler(mock_cb, cb_confirm)

        # create_waybill should NOT be called!
        m_create.assert_not_called()
        # edit_text should display warning screen
        mock_cb.message.edit_text.assert_called_once()
        args, kwargs = mock_cb.message.edit_text.call_args
        assert "перетне встановлену межу післяплати" in args[0]
        assert "35000 грн" in args[0]
        # alert must be shown
        mock_cb.answer.assert_called_with("🚨 Увага: ліміт післяплати буде перевищено!", show_alert=True)


@pytest.mark.asyncio
async def test_waybill_force_confirm_creates_waybill_and_shows_cod_limit(setup_handlers):
    from src.nova_poshta.models import CODMonthlyStats, WaybillCreateResult, CounterpartyRecipientResult

    manager = setup_handlers
    user_id = 1002
    session_id = "test-session-force"
    manager.update_user_settings(user_id, cod_monthly_limit_sum=30000.0)

    city = CityInfo(Ref="city-ref-1", Description="Київ")
    wh = WarehouseInfo(Ref="wh-ref-1", Description="Відділення №1", Number="1", TypeOfWarehouse="Warehouse", CityRef="city-ref-1")
    parsed_info = ParsedRecipientInfo(
        first_name="Олександр", last_name="Тест", phone="380991234567",
        city_name="Київ", warehouse_number=1, declared_value=15000.0,
        cargo_description="ноутбук", cod_amount=15000.0, cod_payment_type="card",
    )

    PENDING_SESSIONS[session_id] = {
        "parsed_info": parsed_info,
        "city": city,
        "warehouse": wh,
        "destination_description": "Відділення №1",
        "payer_type": "Recipient",
        "cargo_type": "Parcel",
        "declared_value": 15000.0,
        "cargo_description": "ноутбук",
        "cod_amount": 15000.0,
        "cod_payment_type": "card",
        "user_id": user_id,
        "updated_at": time.time(),
    }
    USER_ACTIVE_SESSIONS[user_id] = session_id

    waybill_callback_handler = _get_callback_handler("process_waybill_callback")

    mock_cb = MagicMock()
    mock_cb.from_user.id = user_id
    mock_cb.message.edit_text = AsyncMock()
    mock_cb.answer = AsyncMock()

    mock_stats = CODMonthlyStats(
        year=2026, month=9, month_name="Вересень 2026", from_date="01.09.2026", to_date="30.09.2026",
        total_count=4, total_sum=20000.0, items=[]
    )
    mock_recipient = CounterpartyRecipientResult(
        counterparty_ref="cp-recip-1",
        contact_person_ref="cp-contact-1",
    )
    mock_created_wb = WaybillCreateResult(
        int_doc_number="20450011223344",
        ref="wb-ref-created-1",
        cost=95.0,
        estimated_delivery_date="15.09.2026",
    )

    cb_force = WaybillActionCallback(action="force_confirm", session_id=session_id)
    with patch("src.nova_poshta.client.NovaPoshtaClient.get_monthly_cod_stats", new_callable=AsyncMock) as m_stats, \
         patch("src.nova_poshta.client.NovaPoshtaClient.create_recipient_counterparty", new_callable=AsyncMock) as m_recip, \
         patch("src.nova_poshta.client.NovaPoshtaClient.create_waybill", new_callable=AsyncMock) as m_create:
        m_stats.return_value = mock_stats
        m_recip.return_value = mock_recipient
        m_create.return_value = mock_created_wb

        await waybill_callback_handler(mock_cb, cb_force)

        # create_waybill was called!
        m_create.assert_called_once()
        # Final success card has COD limit line
        assert mock_cb.message.edit_text.call_count >= 2
        last_call_args = mock_cb.message.edit_text.call_args[0][0]
        assert "Express-накладну успішно створено" in last_call_args
        assert "Місячний обсяг післяплати:" in last_call_args
        assert "35000 грн" in last_call_args


@pytest.mark.asyncio
async def test_waybill_back_to_card(setup_handlers):
    from src.nova_poshta.models import CODMonthlyStats

    manager = setup_handlers
    user_id = 1003
    session_id = "test-session-back"

    city = CityInfo(Ref="city-ref-1", Description="Київ")
    wh = WarehouseInfo(Ref="wh-ref-1", Description="Відділення №1", Number="1", TypeOfWarehouse="Warehouse", CityRef="city-ref-1")
    parsed_info = ParsedRecipientInfo(
        first_name="Іван", last_name="Іванов", phone="380991112233",
        city_name="Київ", warehouse_number=1, declared_value=5000.0,
        cargo_description="одяг", cod_amount=2000.0, cod_payment_type="cash",
    )

    PENDING_SESSIONS[session_id] = {
        "parsed_info": parsed_info,
        "city": city,
        "warehouse": wh,
        "destination_description": "Відділення №1",
        "payer_type": "Recipient",
        "cargo_type": "Parcel",
        "declared_value": 5000.0,
        "cargo_description": "одяг",
        "cod_amount": 2000.0,
        "cod_payment_type": "cash",
        "user_id": user_id,
        "updated_at": time.time(),
    }
    USER_ACTIVE_SESSIONS[user_id] = session_id

    waybill_callback_handler = _get_callback_handler("process_waybill_callback")

    mock_cb = MagicMock()
    mock_cb.from_user.id = user_id
    mock_cb.message.edit_text = AsyncMock()
    mock_cb.answer = AsyncMock()

    mock_stats = CODMonthlyStats(
        year=2026, month=9, month_name="Вересень 2026", from_date="01.09.2026", to_date="30.09.2026",
        total_count=1, total_sum=5000.0, items=[]
    )

    cb_back = WaybillActionCallback(action="back_to_card", session_id=session_id)
    with patch("src.nova_poshta.client.NovaPoshtaClient.get_monthly_cod_stats", new_callable=AsyncMock) as m_stats:
        m_stats.return_value = mock_stats
        await waybill_callback_handler(mock_cb, cb_back)

        mock_cb.message.edit_text.assert_called_once()
        card_text = mock_cb.message.edit_text.call_args[0][0]
        assert "Розпарсені дані отримувача для перевірки:" in card_text
        assert "Контроль місячного ліміту післяплати:" in card_text


@pytest.mark.asyncio
async def test_auto_disambiguate_settlement_by_address_in_text(setup_handlers):
    """Verify that when 2 settlements match, but text has the warehouse street, Candidate 1 is auto-selected."""
    user_id = 99887766
    c1 = CityInfo(Ref="c1_ref", Description="Берегомет", AreaDescription="Чернівецька")
    w1 = WarehouseInfo(
        Ref="w1_ref",
        Description="Відділення №1: вул. Героїв Майдану, 237",
        Number="1",
        TypeOfWarehouse="branch",
        CityRef="c1_ref",
    )

    c2 = CityInfo(Ref="c2_ref", Description="Берегомет (Кіцманський р-н)", AreaDescription="Чернівецька")
    w2 = WarehouseInfo(
        Ref="w2_ref",
        Description="Пункт приймання-видачі (до 30 кг): вул. Головна, 13а",
        Number="1",
        TypeOfWarehouse="branch",
        CityRef="c2_ref",
    )

    parsed = ParsedRecipientInfo(
        is_recipient_info=True,
        first_name="Олександр",
        last_name="Данелюк",
        phone="0990723343",
        city_name="Берегомет",
        region_name="Чернівецька",
        warehouse_number=1,
    )

    mock_msg = MagicMock()
    mock_msg.from_user.id = user_id
    mock_msg.chat.id = user_id
    mock_msg.text = "0990723343 Данелюк Олександр Чернівецька обл. Смт. Берегомет 1 відділення, героїв Майдану 237."

    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    mock_msg.answer = AsyncMock(return_value=status_msg)

    manager = setup_handlers
    manager.update_user_settings(
        user_id,
        nova_poshta_api_key="test_np_key",
        ai_api_key="test_ai_key",
    )

    with patch("src.ai.extractor.AIExtractor.parse_text", new_callable=AsyncMock) as m_parse, \
         patch("src.nova_poshta.client.NovaPoshtaClient.search_city", new_callable=AsyncMock) as m_city, \
         patch("src.nova_poshta.client.NovaPoshtaClient.get_warehouse", new_callable=AsyncMock) as m_wh:

        m_parse.return_value = parsed
        m_city.return_value = [c1, c2]

        async def _fake_get_wh(city_ref, warehouse_number, is_postomat=False):
            if city_ref == "c1_ref":
                return w1
            elif city_ref == "c2_ref":
                return w2
            return None

        m_wh.side_effect = _fake_get_wh

        await router._handle_combined_text_message(mock_msg, mock_msg.text, user_id=user_id)

        # Check status_msg.edit_text calls
        calls = [c[0][0] for c in status_msg.edit_text.call_args_list if c[0]]
        # Did NOT display the disambiguation keyboard!
        assert not any("Знайдено декілька населених пунктів" in call for call in calls)

        # Verification card was displayed with Candidate 1's warehouse!
        last_card = calls[-1]
        assert "Розпарсені дані отримувача для перевірки:" in last_card
        assert "вул. Героїв Майдану, 237" in last_card


@pytest.mark.asyncio
async def test_auto_disambiguate_settlement_multi_turn_initial_message(setup_handlers):
    """Verify that follow-up messages (e.g. 'Оцінка 7500, в посилці планшет') preserve initial message address clues."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from src.nova_poshta.models import CityInfo, WarehouseInfo
    from src.ai.schemas import ParsedRecipientInfo
    from src.bot.handlers import router, PENDING_SESSIONS, USER_ACTIVE_SESSIONS, clear_user_active_session

    user_id = 99887766
    clear_user_active_session(user_id)

    c1 = CityInfo(Ref="c1_ref", Description="Берегомет", AreaDescription="Чернівецька")
    w1 = WarehouseInfo(
        Ref="w1_ref",
        Description="Відділення №1: вул. Героїв Майдану, 237",
        Number="1",
        TypeOfWarehouse="branch",
        CityRef="c1_ref",
    )

    c2 = CityInfo(Ref="c2_ref", Description="Берегомет (Кіцманський р-н)", AreaDescription="Чернівецька")
    w2 = WarehouseInfo(
        Ref="w2_ref",
        Description="Пункт приймання-видачі (до 30 кг): вул. Головна, 13а",
        Number="1",
        TypeOfWarehouse="branch",
        CityRef="c2_ref",
    )

    parsed_turn1 = ParsedRecipientInfo(
        is_recipient_info=True,
        first_name="Олександр",
        last_name="Данелюк",
        phone="0990723343",
        city_name="Берегомет",
        region_name="Чернівецька",
        warehouse_number=1,
    )

    parsed_turn2 = ParsedRecipientInfo(
        is_recipient_info=True,
        first_name="Олександр",
        last_name="Данелюк",
        phone="0990723343",
        city_name="Берегомет",
        region_name="Чернівецька",
        warehouse_number=1,
        declared_value=7500.0,
        cargo_description="планшет",
    )

    mock_msg1 = MagicMock()
    mock_msg1.from_user.id = user_id
    mock_msg1.chat.id = user_id
    mock_msg1.text = "хНЯ:\n0990723343 Данелюк Олександр Чернівецька обл. Смт. Берегомет 1 відділення, героїв Майдану 237."

    status_msg1 = MagicMock()
    status_msg1.edit_text = AsyncMock()
    mock_msg1.answer = AsyncMock(return_value=status_msg1)

    manager = setup_handlers
    manager.update_user_settings(
        user_id,
        nova_poshta_api_key="test_np_key",
        ai_api_key="test_ai_key",
    )

    with patch("src.ai.extractor.AIExtractor.parse_text", new_callable=AsyncMock) as m_parse, \
         patch("src.nova_poshta.client.NovaPoshtaClient.search_city", new_callable=AsyncMock) as m_city, \
         patch("src.nova_poshta.client.NovaPoshtaClient.get_warehouse", new_callable=AsyncMock) as m_wh:

        m_parse.return_value = parsed_turn1
        m_city.return_value = [c1, c2]

        async def _fake_get_wh(city_ref, warehouse_number, is_postomat=False):
            if city_ref == "c1_ref":
                return w1
            elif city_ref == "c2_ref":
                return w2
            return None

        m_wh.side_effect = _fake_get_wh

        # Turn 1: Process initial recipient message
        await router._handle_combined_text_message(mock_msg1, mock_msg1.text, user_id=user_id)

        # Turn 2: User sends update with only value and cargo description, no address info
        mock_msg2 = MagicMock()
        mock_msg2.from_user.id = user_id
        mock_msg2.chat.id = user_id
        mock_msg2.text = "Оцінка 7500, в посилці планшет"

        status_msg2 = MagicMock()
        status_msg2.edit_text = AsyncMock()
        mock_msg2.answer = AsyncMock(return_value=status_msg2)

        m_parse.return_value = parsed_turn2

        await router._handle_combined_text_message(mock_msg2, mock_msg2.text, user_id=user_id)

        # Verify Turn 2 output: status_msg2 must NOT show disambiguation keyboard!
        calls2 = [c[0][0] for c in status_msg2.edit_text.call_args_list if c[0]]
        assert not any("Знайдено декілька населених пунктів" in call for call in calls2)

        last_card2 = calls2[-1]
        assert "Розпарсені дані отримувача для перевірки:" in last_card2
        assert "вул. Героїв Майдану, 237" in last_card2
        assert "7500" in last_card2
        assert "планшет" in last_card2


@pytest.mark.asyncio
async def test_followup_message_does_not_cancel_active_processing_task(setup_handlers):
    """Regression test: sending a follow-up message while first message is being processed
    by AI/API must not cancel the first message mid-flight or trigger missing required fields."""
    user_id = 161101
    clear_user_active_session(user_id)
    PENDING_SESSIONS.clear()
    USER_ACTIVE_SESSIONS.clear()

    manager = setup_handlers
    manager.update_user_settings(
        user_id,
        nova_poshta_api_key="test_np_key",
        ai_api_key="test_ai_key",
    )

    text_handler = _get_message_handler("process_text_message")

    # Turn 1 Message
    mock_msg1 = MagicMock()
    mock_msg1.from_user.id = user_id
    mock_msg1.chat.id = user_id
    mock_msg1.text = "0990723343 Данелюк Олександр Чернівецька обл. Смт. Берегомет 1 відділення, героїв Майдану 237."

    status_msg1 = MagicMock()
    status_msg1.edit_text = AsyncMock()
    status_msg1.delete = AsyncMock()
    mock_msg1.answer = AsyncMock(return_value=status_msg1)

    # Turn 2 Message
    mock_msg2 = MagicMock()
    mock_msg2.from_user.id = user_id
    mock_msg2.chat.id = user_id
    mock_msg2.text = "Оцінка 7500, в посилці планшет"

    status_msg2 = MagicMock()
    status_msg2.edit_text = AsyncMock()
    status_msg2.delete = AsyncMock()
    mock_msg2.answer = AsyncMock(return_value=status_msg2)

    parsed_turn1 = ParsedRecipientInfo(
        is_recipient_info=True,
        last_name="Данелюк",
        first_name="Олександр",
        phone="0990723343",
        city_name="Берегомет",
        warehouse_number=1,
        declared_value=500.0,
        cargo_description="Документи",
    )

    parsed_turn2 = ParsedRecipientInfo(
        is_recipient_info=True,
        last_name="Данелюк",
        first_name="Олександр",
        phone="0990723343",
        city_name="Берегомет",
        warehouse_number=1,
        declared_value=7500.0,
        cargo_description="планшет",
    )

    city = CityInfo(Ref="city-ref-beregomet", Description="Берегомет", Area="Чернівецька")
    wh = WarehouseInfo(
        Ref="wh-ref-1",
        Description="Відділення №1: вул. Героїв Майдану, 237",
        Number="1",
        TypeOfWarehouse="Warehouse",
        CityRef="city-ref-beregomet",
    )

    async def _mock_parse(text, previous_info=None):
        if "7500" in text:
            return parsed_turn2
        else:
            await asyncio.sleep(0.3)
            return parsed_turn1

    with patch("src.ai.extractor.AIExtractor.parse_text", side_effect=_mock_parse), \
         patch("src.nova_poshta.client.NovaPoshtaClient.search_city", new_callable=AsyncMock) as m_city, \
         patch("src.nova_poshta.client.NovaPoshtaClient.get_warehouse", new_callable=AsyncMock) as m_wh:

        m_city.return_value = [city]
        m_wh.return_value = wh

        # Send Turn 1
        await text_handler(mock_msg1)

        # Wait 1.1s: debounce sleep (1.0s) finishes, Turn 1 enters _process_user_accumulated_messages
        # and starts awaiting _mock_parse (which sleeps 0.3s)
        await asyncio.sleep(1.1)

        # While Turn 1 is actively running in _mock_parse, send Turn 2
        await text_handler(mock_msg2)

        # Wait for both tasks to complete (Turn 1 finishes in ~0.2s, Turn 2 debounces for 1.0s and runs)
        await asyncio.sleep(1.5)

        # Verify Turn 1 was NOT cancelled! status_msg1 was edited
        assert status_msg1.edit_text.called

        # Verify active session exists and holds merged data
        active_sess_id = get_user_active_session_id(user_id)
        assert active_sess_id is not None
        final_sess = PENDING_SESSIONS[active_sess_id]
        assert final_sess["parsed_info"].full_name == "Данелюк Олександр"
        assert final_sess["parsed_info"].phone == "0990723343"
        assert final_sess["declared_value"] == 7500.0
        assert final_sess["cargo_description"] == "планшет"

        # Verify Turn 2 edit_text was called and did NOT complain about missing fields
        calls2 = [c[0][0] for c in status_msg2.edit_text.call_args_list if c[0]]
        assert not any("Очікую решту даних" in c for c in calls2)
        assert any("7500" in c for c in calls2)





