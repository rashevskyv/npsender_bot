"""Unit tests for API departure address extraction, in-app priority, and session continuity."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aiogram.types import CallbackQuery, Message, User, Chat

from src.config import Settings
from src.nova_poshta.client import NovaPoshtaClient
from src.storage import UserSettingsManager, SenderProfile
from src.bot.handlers import (
    register_handlers,
    router,
    PENDING_SESSIONS,
    USER_ACTIVE_SESSIONS,
    get_user_active_session_id,
)
from src.bot.keyboards import (
    UserProfileCallback,
    WaybillActionCallback,
)
from src.ai.schemas import ParsedRecipientInfo
from src.nova_poshta.models import CityInfo, WarehouseInfo


def _get_callback_handler(name: str):
    for h in reversed(router.callback_query.handlers):
        if name in str(h.callback):
            return h.callback
    raise RuntimeError(f"Callback handler {name} not found in router")


def _get_message_handler(name: str):
    for h in reversed(router.message.handlers):
        if name in str(h.callback):
            return h.callback
    raise RuntimeError(f"Message handler {name} not found in router")


@pytest.fixture
def test_settings():
    return Settings(
        TELEGRAM_BOT_TOKEN="test_token",
        NOVA_POSHTA_API_KEY="test_np_key",
        AI_API_KEY="test_ai_key",
        sender_phone="380991112233",
        sender_counterparty_ref="sender-cp-1",
        sender_contact_ref="contact-1",
        sender_city_ref="",
        sender_address_ref="",
    )


@pytest.fixture
def storage_mgr(tmp_path):
    storage_file = tmp_path / "user_settings.json"
    drafts_file = tmp_path / "drafts.json"
    return UserSettingsManager(filepath=str(storage_file), drafts_filepath=str(drafts_file))


@pytest.fixture(autouse=True)
def setup_handlers(storage_mgr, test_settings):
    register_handlers(
        settings=test_settings,
        ai_extractor=MagicMock(),
        np_client=MagicMock(),
        storage_manager=storage_mgr,
    )
    PENDING_SESSIONS.clear()
    USER_ACTIVE_SESSIONS.clear()


@pytest.mark.asyncio
async def test_fetch_sender_address_from_counterparty_addresses(test_settings):
    """NovaPoshtaClient.fetch_sender_address_from_api parses addresses from Counterparty/getCounterpartyAddresses."""
    client = NovaPoshtaClient(test_settings)

    async def mock_post(model_name, called_method, method_properties):
        if model_name == "Counterparty" and called_method == "getCounterpartyAddresses":
            return {
                "success": True,
                "data": [
                    {
                        "Ref": "addr-guid-1",
                        "CityRef": "city-guid-if",
                        "CityDescription": "Івано-Франківськ",
                        "Description": "Відділення №8 (до 30 кг): вул. Мазепи",
                    }
                ],
            }
        return {"success": True, "data": []}

    client._post = mock_post

    res = await client.fetch_sender_address_from_api("sender-cp-1")
    assert res["sender_city_ref"] == "city-guid-if"
    assert res["sender_address_ref"] == "addr-guid-1"
    assert res["sender_city_name"] == "Івано-Франківськ"
    assert "Відділення №8" in res["sender_warehouse_name"]


@pytest.mark.asyncio
async def test_fetch_sender_address_fallback_to_document_list(test_settings):
    """When getCounterpartyAddresses is empty, fetch_sender_address_from_api falls back to getDocumentList."""
    client = NovaPoshtaClient(test_settings)

    async def mock_post(model_name, called_method, method_properties):
        if model_name == "Counterparty" and called_method == "getCounterpartyAddresses":
            return {"success": True, "data": []}
        if model_name == "InternetDocument" and called_method == "getDocumentList":
            return {
                "success": True,
                "data": [
                    {
                        "CitySender": "city-guid-kyiv",
                        "SenderAddress": "addr-guid-wh1",
                        "CitySenderDescription": "Київ",
                        "SenderAddressDescription": "Відділення №1: вул. Пирогівський шлях, 135",
                    }
                ],
            }
        return {"success": True, "data": []}

    client._post = mock_post

    res = await client.fetch_sender_address_from_api("sender-cp-1")
    assert res["sender_city_ref"] == "city-guid-kyiv"
    assert res["sender_address_ref"] == "addr-guid-wh1"
    assert res["sender_city_name"] == "Київ"
    assert "Відділення №1" in res["sender_warehouse_name"]


def test_in_app_address_has_priority_over_api_address(storage_mgr, test_settings):
    """Address configured directly in the app (/set_city, /set_warehouse) has priority over API address."""
    user_id = 100

    # Profile with both in-app address and API address
    p = SenderProfile(
        id="prof_custom",
        name="Користувач з обома адресами",
        nova_poshta_api_key="key_1",
        sender_counterparty_ref="cp_1",
        sender_contact_ref="contact_1",
        # Configured in app:
        sender_city_ref="app-city-ref",
        sender_city_name="Львів",
        sender_address_ref="app-wh-ref",
        sender_warehouse_name="Відділення №2",
        # Pulled from API:
        api_sender_city_ref="api-city-ref",
        api_sender_city_name="Київ",
        api_sender_address_ref="api-wh-ref",
        api_sender_warehouse_name="Відділення №10",
    )
    storage_mgr.add_sender_profile(user_id, p, set_active=True)

    eff = storage_mgr.get_effective_settings(user_id, test_settings)
    # In-app address MUST win:
    assert eff.sender_city_ref == "app-city-ref"
    assert eff.sender_address_ref == "app-wh-ref"


def test_api_address_used_when_in_app_not_configured(storage_mgr, test_settings):
    """When in-app address is not set, API-pulled address is used as fallback."""
    user_id = 200

    p = SenderProfile(
        id="prof_api_only",
        name="Користувач тільки з API адресою",
        nova_poshta_api_key="key_2",
        sender_counterparty_ref="cp_2",
        sender_contact_ref="contact_2",
        # In-app is not set:
        sender_city_ref="",
        sender_address_ref="",
        # Pulled from API:
        api_sender_city_ref="api-city-kharkiv",
        api_sender_city_name="Харків",
        api_sender_address_ref="api-wh-5",
        api_sender_warehouse_name="Відділення №5",
    )
    storage_mgr.add_sender_profile(user_id, p, set_active=True)

    eff = storage_mgr.get_effective_settings(user_id, test_settings)
    assert eff.sender_city_ref == "api-city-kharkiv"
    assert eff.sender_address_ref == "api-wh-5"


@pytest.mark.asyncio
async def test_confirm_waybill_blocks_and_preserves_session_when_address_missing(storage_mgr, test_settings):
    """When confirming a waybill without sender city/warehouse, bot alerts and does NOT crash or call create_waybill."""
    user_id = 300
    session_id = "sess_missing_sender"

    # Profile with NO address configured anywhere
    p = SenderProfile(
        id="p_empty",
        name="Порожній профіль",
        nova_poshta_api_key="np_key_empty",
        sender_counterparty_ref="cp_empty",
        sender_contact_ref="contact_empty",
        sender_city_ref="",
        sender_address_ref="",
        api_sender_city_ref="",
        api_sender_address_ref="",
    )
    storage_mgr.add_sender_profile(user_id, p, set_active=True)

    # Put active session into PENDING_SESSIONS
    PENDING_SESSIONS[session_id] = {
        "user_id": user_id,
        "parsed_info": ParsedRecipientInfo(
            first_name="Іван",
            last_name="Іваненко",
            phone="0501234567",
            city="Київ",
            warehouse="Відділення 1",
        ),
        "city": CityInfo(Ref="rec-city-ref", Description="Київ"),
        "warehouse": WarehouseInfo(Ref="rec-wh-ref", Description="Відділення №1", Number="1", TypeOfWarehouse="Branch", CityRef="rec-city-ref"),
        "payer_type": "Recipient",
        "declared_value": 1000.0,
        "cargo_description": "Електроніка",
        "cod_amount": 0.0,
    }
    USER_ACTIVE_SESSIONS[user_id] = session_id

    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = User(id=user_id, is_bot=False, first_name="Tester")
    callback.message = MagicMock(spec=Message)
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()

    handler = _get_callback_handler("process_waybill_callback")
    cb_data = WaybillActionCallback(action="confirm", session_id=session_id)

    with patch("src.nova_poshta.client.NovaPoshtaClient.create_waybill", new_callable=AsyncMock) as m_create:
        await handler(callback, cb_data)
        # create_waybill must NOT be called
        m_create.assert_not_called()

    # Callback answered with alert
    callback.answer.assert_called_once()
    assert "Не вказано місто або відділення відправки" in callback.answer.call_args[0][0]
    # Reply informs user how to configure departure address
    callback.message.edit_text.assert_called_once()
    reply_text = callback.message.edit_text.call_args[0][0]
    assert "/set_city" in reply_text
    assert "/set_warehouse" in reply_text

    # Session must be preserved!
    assert session_id in PENDING_SESSIONS
    assert get_user_active_session_id(user_id) == session_id


@pytest.mark.asyncio
async def test_set_warehouse_resumes_active_waybill_session(storage_mgr, test_settings):
    """cmd_set_warehouse preserves active draft session and responds with the updated preview card."""
    user_id = 400
    session_id = "sess_resume_test"

    storage_mgr.update_user_settings(
        user_id=user_id,
        nova_poshta_api_key="np_key",
        sender_city_ref="city-if-ref",
        sender_city_name="Івано-Франківськ",
        sender_address_ref="",
    )

    PENDING_SESSIONS[session_id] = {
        "user_id": user_id,
        "parsed_info": ParsedRecipientInfo(
            first_name="Олена",
            last_name="Петренко",
            phone="0671112233",
            city="Львів",
            warehouse="Відділення 3",
        ),
        "city": CityInfo(Ref="rec-city-ref", Description="Львів"),
        "warehouse": WarehouseInfo(Ref="rec-wh-ref", Description="Відділення №3", Number="3", TypeOfWarehouse="Branch", CityRef="rec-city-ref"),
        "payer_type": "Recipient",
        "declared_value": 800.0,
        "cargo_description": "Одяг",
        "cod_amount": 0.0,
        "chat_id": 12345,
        "message_id": 67890,
    }
    USER_ACTIVE_SESSIONS[user_id] = session_id

    msg = MagicMock(spec=Message)
    msg.from_user = User(id=user_id, is_bot=False, first_name="Tester")
    msg.text = "/set_warehouse 8"
    msg.answer = AsyncMock()
    msg.bot = MagicMock()
    msg.bot.edit_message_reply_markup = AsyncMock()

    wh_mock = WarehouseInfo(Ref="wh-8-ref", Description="Відділення №8: вул. Мазепи", Number="8", TypeOfWarehouse="Branch", CityRef="city-if-ref")

    handler = _get_message_handler("cmd_set_warehouse")
    with patch("src.nova_poshta.client.NovaPoshtaClient.get_warehouse", new_callable=AsyncMock, return_value=wh_mock):
        await handler(msg)

    # Verified: user settings updated with warehouse
    u_set = storage_mgr.get_user_settings(user_id)
    assert u_set.sender_address_ref == "wh-8-ref"
    assert "Відділення №8" in u_set.sender_warehouse_name

    # Session is preserved
    assert session_id in PENDING_SESSIONS
    # Preview message was sent with updated warehouse
    msg.answer.assert_called()
    preview_call = [call for call in msg.answer.call_args_list if "Отримувач" in str(call)]
    assert len(preview_call) > 0


@pytest.mark.asyncio
async def test_sync_address_callback(storage_mgr, test_settings):
    """UserProfileCallback(action='sync_address') pulls address from API and saves to profile."""
    user_id = 500

    storage_mgr.update_user_settings(
        user_id=user_id,
        nova_poshta_api_key="np_key",
        sender_counterparty_ref="cp_500",
    )

    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = User(id=user_id, is_bot=False, first_name="Tester")
    callback.message = MagicMock(spec=Message)
    callback.message.answer = AsyncMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()

    api_addr = {
        "sender_city_ref": "city-api-ref",
        "sender_address_ref": "addr-api-ref",
        "sender_city_name": "Івано-Франківськ",
        "sender_warehouse_name": "Відділення №8 (до 30 кг)",
    }

    handler = _get_callback_handler("process_user_profile_callback")
    cb_data = UserProfileCallback(action="sync_address", profile_id="none")

    with patch("src.nova_poshta.client.NovaPoshtaClient.fetch_sender_address_from_api", new_callable=AsyncMock, return_value=api_addr):
        await handler(callback, cb_data)

    u_set = storage_mgr.get_user_settings(user_id)
    assert u_set.api_sender_city_ref == "city-api-ref"
    assert u_set.api_sender_address_ref == "addr-api-ref"
    assert u_set.sender_city_ref == "city-api-ref"
    assert u_set.sender_address_ref == "addr-api-ref"
