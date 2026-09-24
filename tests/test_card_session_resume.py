"""Unit tests verifying session preservation when setting card and resuming waybill drafting."""

import datetime
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aiogram.types import Message, User, CallbackQuery

from src.config import Settings
from src.nova_poshta.client import NovaPoshtaClient
from src.storage import UserSettingsManager, SenderProfile
from src.ai.schemas import ParsedRecipientInfo
from src.nova_poshta.models import CityInfo, WarehouseInfo
from src.bot.keyboards import WaybillActionCallback
from src.bot.handlers import (
    router,
    register_handlers,
    PENDING_SESSIONS,
    USER_ACTIVE_SESSIONS,
    USER_CARD_WAITING,
    USER_MESSAGE_BUFFERS,
    clear_user_active_session,
    extract_standalone_bank_card,
)


def _get_message_handler(name: str):
    for h in reversed(router.message.handlers):
        if name in str(h.callback):
            return h.callback
    raise RuntimeError(f"Message handler {name} not found in router")


def _get_callback_handler(name: str):
    for h in reversed(router.callback_query.handlers):
        if name in str(h.callback):
            return h.callback
    raise RuntimeError(f"Callback handler {name} not found in router")


@pytest.fixture
def mock_settings():
    return Settings(
        TELEGRAM_BOT_TOKEN="123456:ABC-DEF",
        NOVA_POSHTA_API_KEY="test_api_key",
        AI_API_KEY="test_ai_key",
        sender_phone="380502559301",
    )


@pytest.fixture
def sample_session_setup(tmp_path, mock_settings):
    st_mgr = UserSettingsManager(filepath=str(tmp_path / "user_settings.json"))
    user_id = 999111
    st_mgr.add_sender_profile(
        user_id,
        SenderProfile(
            id="prof_1",
            name="Тестовий Відправник",
            nova_poshta_api_key="np_test_key",
            sender_phone="380501112233",
        ),
        set_active=True,
    )

    session_id = "sess_test_123"
    city = CityInfo(
        Ref="city_ref_1",
        Description="Київ",
        AreaDescription="",
        RegionsDescription="",
    )
    wh = WarehouseInfo(
        Ref="wh_ref_1",
        Description="Відділення №1: вул. Хрещатик, 1",
        Number="1",
        TypeOfWarehouse="Warehouse",
        CityRef="city_ref_1",
    )
    parsed = ParsedRecipientInfo(
        full_name="Іванов Іван Іванович",
        phone="0501234567",
        city_name="Київ",
        warehouse_number=1,
        cod_amount=1500.0,
    )

    PENDING_SESSIONS[session_id] = {
        "user_id": user_id,
        "parsed_info": parsed,
        "city": city,
        "warehouse": wh,
        "destination_description": wh.description,
        "cargo_description": "Посилка",
        "declared_value": 1500.0,
        "cod_amount": 1500.0,
        "cod_payment_type": "cash",
        "payer_type": "Recipient",
        "cargo_type": "Parcel",
        "updated_at": datetime.datetime.now().timestamp(),
    }
    USER_ACTIVE_SESSIONS[user_id] = session_id

    # Register handlers with mocked dependencies
    mock_ai = MagicMock()
    mock_np = MagicMock()
    register_handlers(mock_settings, mock_ai, mock_np, st_mgr)

    yield {
        "user_id": user_id,
        "session_id": session_id,
        "storage_manager": st_mgr,
        "settings": mock_settings,
    }

    # Cleanup
    clear_user_active_session(user_id)


@pytest.mark.asyncio
async def test_cmd_set_card_preserves_and_resumes_session(sample_session_setup):
    """Setting card with /set_card must NOT wipe active session, and must update cod_payment_type to card."""
    user_id = sample_session_setup["user_id"]
    session_id = sample_session_setup["session_id"]
    st_mgr = sample_session_setup["storage_manager"]

    handler = _get_message_handler("cmd_set_card")

    mock_msg = AsyncMock(spec=Message)
    mock_msg.from_user = User(id=user_id, is_bot=False, first_name="User")
    mock_msg.text = "/set_card 4441111400765537"
    sent_response = AsyncMock()
    sent_response.chat.id = 123
    sent_response.message_id = 456
    mock_msg.answer = AsyncMock(return_value=sent_response)
    mock_msg.bot = AsyncMock()

    with patch("src.bot.handlers.evaluate_cod_limits", new_callable=AsyncMock) as mock_eval:
        mock_eval.return_value = {"warn_line": "", "suggested_profile": None}
        await handler(mock_msg)

    # 1. Session must still exist in memory!
    assert session_id in PENDING_SESSIONS
    assert USER_ACTIVE_SESSIONS.get(user_id) == session_id

    # 2. Session COD payment type must be flipped to 'card'
    session = PENDING_SESSIONS[session_id]
    assert session["cod_payment_type"] == "card"

    # 3. User settings must have masked card saved
    u_settings = st_mgr.get_user_settings(user_id)
    assert u_settings.sender_card_mask == "444111******5537"

    # 4. Message answer was called with confirmation text and updated keyboard
    mock_msg.answer.assert_called_once()
    args, kwargs = mock_msg.answer.call_args
    assert "Банківську картку для виплати наложки успішно збережено" in args[0]
    assert "Розпарсені дані отримувача для перевірки" in args[0]
    assert kwargs.get("reply_markup") is not None


@pytest.mark.asyncio
async def test_text_message_16_digits_resumes_session(sample_session_setup):
    """Sending plain 16-digit card number in chat resumes active session with card set."""
    user_id = sample_session_setup["user_id"]
    session_id = sample_session_setup["session_id"]
    st_mgr = sample_session_setup["storage_manager"]

    handler = _get_message_handler("process_text_message")

    mock_msg = AsyncMock(spec=Message)
    mock_msg.from_user = User(id=user_id, is_bot=False, first_name="User")
    mock_msg.text = "5375 4141 1234 5678"
    sent_response = AsyncMock()
    sent_response.chat.id = 123
    sent_response.message_id = 789
    mock_msg.answer = AsyncMock(return_value=sent_response)
    mock_msg.bot = AsyncMock()

    with patch("src.bot.handlers.evaluate_cod_limits", new_callable=AsyncMock) as mock_eval:
        mock_eval.return_value = {"warn_line": "", "suggested_profile": None}
        await handler(mock_msg)

    # Session preserved
    assert session_id in PENDING_SESSIONS
    assert PENDING_SESSIONS[session_id]["cod_payment_type"] == "card"
    assert st_mgr.get_user_settings(user_id).sender_card_mask == "537541******5678"


@pytest.mark.asyncio
async def test_toggle_cod_type_adds_user_card_waiting_when_card_missing(sample_session_setup):
    """Clicking payout to card without saved card triggers card waiting and notifies user."""
    user_id = sample_session_setup["user_id"]
    session_id = sample_session_setup["session_id"]

    handler = _get_callback_handler("process_waybill_callback")

    mock_cb = AsyncMock(spec=CallbackQuery)
    mock_cb.from_user = User(id=user_id, is_bot=False, first_name="User")
    mock_cb.message = AsyncMock()
    mock_cb.message.chat.id = 111
    mock_cb.message.message_id = 222
    mock_cb.answer = AsyncMock()

    cb_data = WaybillActionCallback(action="toggle_cod_type", session_id=session_id)

    with patch("src.bot.handlers.evaluate_cod_limits", new_callable=AsyncMock) as mock_eval:
        mock_eval.return_value = {"warn_line": "", "suggested_profile": None}
        with patch.object(NovaPoshtaClient, "get_payment_cards", new_callable=AsyncMock) as mock_get_cards:
            mock_get_cards.return_value = []
            await handler(mock_cb, cb_data)

    assert user_id in USER_CARD_WAITING
    mock_cb.answer.assert_called_once()
    assert "Введіть 16 цифр картки" in mock_cb.answer.call_args[0][0]
    assert PENDING_SESSIONS[session_id]["cod_payment_type"] == "card"


@pytest.mark.asyncio
async def test_create_waybill_populates_redelivery_payment_card(mock_settings):
    """NovaPoshtaClient.create_waybill includes RedeliveryPaymentCard when cod_payment_type is card."""
    client = NovaPoshtaClient(mock_settings)

    with patch.object(client, "_post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {
            "success": True,
            "data": [{
                "IntDocNumber": "20450000000000",
                "Ref": "wb_ref_123",
                "CostOnSite": 85.0,
                "EstimatedDeliveryDate": "23.09.2026",
            }],
        }

        res = await client.create_waybill(
            recipient_cp_ref="rcp_1",
            recipient_contact_ref="cnt_1",
            recipient_phone="0501234567",
            recipient_city_ref="city_1",
            recipient_warehouse_ref="wh_1",
            cod_amount=2500.0,
            cod_payment_type="card",
            payment_card="444111******5537",
        )

        assert res.int_doc_number == "20450000000000"
        mock_post.assert_called_once()
        props = mock_post.call_args[1]["method_properties"]

        assert props.get("RedeliveryPaymentCard") == "444111******5537"
        bw = props.get("BackwardDeliveryData", [])
        assert len(bw) == 1
        assert bw[0].get("RedeliveryPaymentCard") == "444111******5537"
        assert bw[0].get("CargoType") == "Money"


def test_extract_standalone_bank_card():
    """Verify robust bank card extraction and rejection of multi-field waybill numbers."""
    # 1. Valid formats
    assert extract_standalone_bank_card("5375 4141 1234 5678") == "5375414112345678"
    assert extract_standalone_bank_card("5375-4141-1234-5678") == "5375414112345678"
    assert extract_standalone_bank_card("5375414112345678") == "5375414112345678"
    assert extract_standalone_bank_card("картка 4441 1114 0076 5537") == "4441111400765537"
    assert extract_standalone_bank_card("414932******1234") == "414932******1234"

    # 2. Reject multi-field digits (e.g. 10-digit phone + 1-digit warehouse + 5-digit COD = 16 digits total)
    user_waybill_text = (
        "Циганко Денис Олегович\n"
        "0968071564\n"
        "Місто Кривий Ріг\n"
        "Відділення 3\n\n"
        "наложка 10300 грн, всередині планшет"
    )
    assert extract_standalone_bank_card(user_waybill_text) is None
    assert extract_standalone_bank_card(user_waybill_text, is_waiting=True) is None

    # 3. Reject waybill with phone and card inside (should be processed as waybill)
    waybill_with_card = "Іванов 0501234567 Київ відд 1 на картку 5375 4141 1234 5678"
    assert extract_standalone_bank_card(waybill_with_card) is None

    # 4. Reject other numbers
    assert extract_standalone_bank_card("0968071564") is None  # Phone
    assert extract_standalone_bank_card("20450123456789") is None  # 14-digit TTN
    assert extract_standalone_bank_card("105-80149920") is None  # Register


@pytest.mark.asyncio
async def test_waybill_with_scattered_16_digits_does_not_hijack_as_card(sample_session_setup):
    """A waybill text whose total scattered digits sum to 16 must NOT be treated as a card."""
    user_id = sample_session_setup["user_id"]
    st_mgr = sample_session_setup["storage_manager"]

    # Even if user was in card waiting mode
    USER_CARD_WAITING.add(user_id)

    handler = _get_message_handler("process_text_message")

    user_text = (
        "Циганко Денис Олегович\n"
        "0968071564\n"
        "Місто Кривий Ріг\n"
        "Відділення 3\n\n"
        "наложка 10300 грн, всередині планшет"
    )

    mock_msg = AsyncMock(spec=Message)
    mock_msg.from_user = User(id=user_id, is_bot=False, first_name="User")
    mock_msg.text = user_text
    mock_msg.answer = AsyncMock()

    await handler(mock_msg)
    # 1. Must clear USER_CARD_WAITING
    assert user_id not in USER_CARD_WAITING
    # 2. Must not save false card
    assert st_mgr.get_user_settings(user_id).sender_card_mask != "096807******0300"
    # 3. Must not answer that card was saved
    if mock_msg.answer.called:
        for call in mock_msg.answer.call_args_list:
            assert "Банківську картку для виплати наложки успішно збережено" not in call[0][0]
    # 4. Message must be added to user's debouncer message buffer for waybill processing
    assert user_text in USER_MESSAGE_BUFFERS.get(user_id, [])
