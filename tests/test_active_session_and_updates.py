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
