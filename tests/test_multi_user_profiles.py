"""Unit tests for Multi-User Sender Profiles and automated COD limit balancing."""

import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aiogram.types import CallbackQuery, Message, User

from src.config import Settings
from src.nova_poshta.client import NovaPoshtaClient
from src.nova_poshta.models import CODMonthlyStats, CityInfo, WarehouseInfo
from src.ai.schemas import ParsedRecipientInfo
from src.storage import UserSettingsManager, SenderProfile
from src.bot.handlers import (
    evaluate_all_profiles_cod_limits,
    evaluate_cod_limits,
    register_handlers,
    router,
    USER_ADD_PROFILE_WAITING,
    PENDING_SESSIONS,
    USER_ACTIVE_SESSIONS,
    USER_LAST_PARSED_INFO,
)
from src.bot.keyboards import (
    get_main_reply_keyboard,
    get_users_management_keyboard,
    get_profile_delete_keyboard,
    get_confirmation_keyboard,
    get_limit_exceeded_confirmation_keyboard,
    UserProfileCallback,
    WaybillActionCallback,
)


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
def mock_settings():
    return Settings(
        TELEGRAM_BOT_TOKEN="test_token",
        NOVA_POSHTA_API_KEY="test_np_key_1",
        AI_API_KEY="test_ai_key",
        sender_phone="380991112233",
        sender_counterparty_ref="sender-cp-1",
        sender_contact_ref="contact-1",
    )


@pytest.fixture
def multi_user_manager(tmp_path):
    storage_file = tmp_path / "user_settings.json"
    drafts_file = tmp_path / "drafts.json"
    manager = UserSettingsManager(filepath=str(storage_file), drafts_filepath=str(drafts_file))

    # Setup user 1 with 2 profiles
    manager.update_user_settings(
        user_id=1,
        nova_poshta_api_key="np_key_1",
        sender_name="Профіль 1 (Основний)",
        sender_phone="380991111111",
        sender_counterparty_ref="cp_1",
        sender_contact_ref="contact_1",
        cod_monthly_limit_sum=30000.0,
    )
    # Add second profile
    manager.add_sender_profile(
        user_id=1,
        profile=SenderProfile(
            id="p2",
            name="Профіль 2 (ФОП)",
            nova_poshta_api_key="np_key_2",
            sender_phone="380992222222",
            sender_counterparty_ref="cp_2",
            sender_contact_ref="contact_2",
        ),
    )
    return manager


@pytest.fixture(autouse=True)
def setup_bot_handlers(multi_user_manager, mock_settings):
    register_handlers(
        settings=mock_settings,
        ai_extractor=MagicMock(),
        np_client=MagicMock(),
        storage_manager=multi_user_manager,
    )
    PENDING_SESSIONS.clear()
    USER_ACTIVE_SESSIONS.clear()
    USER_LAST_PARSED_INFO.clear()
    USER_ADD_PROFILE_WAITING.clear()


# --- 1. Keyboard tests ---

def test_main_reply_keyboard_has_users_button():
    kb = get_main_reply_keyboard()
    button_texts = [btn.text for row in kb.keyboard for btn in row]
    assert "👥 Користувачі" in button_texts


def test_users_management_keyboard():
    profiles = [
        SenderProfile(id="p1", name="ФОП 1", nova_poshta_api_key="k1"),
        SenderProfile(id="p2", name="ФОП 2", nova_poshta_api_key="k2"),
    ]
    kb = get_users_management_keyboard(profiles, active_profile_id="p1")
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    callbacks = [btn.callback_data for btn in buttons]

    assert any("uprof:select:p1" in cb for cb in callbacks)
    assert any("uprof:select:p2" in cb for cb in callbacks)
    assert any("uprof:add:none" in cb for cb in callbacks)


def test_profile_delete_keyboard():
    profiles = [
        SenderProfile(id="p1", name="ФОП 1", nova_poshta_api_key="k1"),
        SenderProfile(id="p2", name="ФОП 2", nova_poshta_api_key="k2"),
    ]
    kb = get_profile_delete_keyboard(profiles, active_profile_id="p1")
    callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "uprof:delete:p2" in callbacks
    assert "uprof:refresh:none" in callbacks


def test_confirmation_keyboard_with_suggested_profile():
    suggested = {"id": "p2", "name": "ФОП 2", "rem_sum": 20000.0}
    kb = get_confirmation_keyboard(
        session_id="default",
        suggested_profile=suggested,
        active_profile_name="ФОП 1",
        has_multiple_profiles=True,
    )
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    switch_btns = [b for b in buttons if b.callback_data == "wb:switch_profile:default"]
    assert len(switch_btns) == 1
    assert "Переключити на: ФОП 2" in switch_btns[0].text


def test_limit_exceeded_keyboard_with_switch_profile():
    suggested = {"id": "p2", "name": "ФОП 2", "rem_sum": 25000.0}
    kb = get_limit_exceeded_confirmation_keyboard(session_id="default", suggested_profile=suggested)
    buttons = [btn for row in kb.inline_keyboard for btn in row]
    # First button should be switch profile
    assert buttons[0].callback_data == "wb:switch_profile:default"
    assert "ФОП 2" in buttons[0].text


# --- 2. COD Limit Balancing logic tests ---

@pytest.mark.asyncio
async def test_evaluate_all_profiles_recommends_alternative_user(multi_user_manager, mock_settings):
    stats_p1 = CODMonthlyStats(
        year=2026, month=9, month_name="Вересень 2026", from_date="01.09.2026", to_date="30.09.2026",
        total_count=10, total_sum=28000.0, items=[]
    )
    stats_p2 = CODMonthlyStats(
        year=2026, month=9, month_name="Вересень 2026", from_date="01.09.2026", to_date="30.09.2026",
        total_count=2, total_sum=5000.0, items=[]
    )

    async def mock_get_monthly_stats(self, **kwargs):
        if self.settings.nova_poshta_api_key in ("np_key_1", "test_np_key_1"):
            return stats_p1
        return stats_p2

    with patch.object(NovaPoshtaClient, "get_monthly_cod_stats", side_effect=mock_get_monthly_stats, autospec=True):
        res = await evaluate_all_profiles_cod_limits(
            user_id=1,
            cod_val=3000.0,
            storage_manager=multi_user_manager,
            eff_settings=mock_settings,
        )

        assert len(res["profiles_status"]) == 2
        rec = res["suggested_profile"]
        assert rec is not None
        assert rec["name"] == "Профіль 2 (ФОП)"
        assert rec["rem_sum"] == 24999.0


@pytest.mark.asyncio
async def test_evaluate_cod_limits_appends_recommendation(multi_user_manager, mock_settings):
    stats_p1 = CODMonthlyStats(
        year=2026, month=9, month_name="Вересень 2026", from_date="01.09.2026", to_date="30.09.2026",
        total_count=10, total_sum=28000.0, items=[]
    )
    stats_p2 = CODMonthlyStats(
        year=2026, month=9, month_name="Вересень 2026", from_date="01.09.2026", to_date="30.09.2026",
        total_count=2, total_sum=5000.0, items=[]
    )

    async def mock_get_monthly_stats(self, **kwargs):
        if self.settings.nova_poshta_api_key in ("np_key_1", "test_np_key_1"):
            return stats_p1
        return stats_p2

    user_client = NovaPoshtaClient(mock_settings)

    with patch.object(NovaPoshtaClient, "get_monthly_cod_stats", side_effect=mock_get_monthly_stats, autospec=True):
        res = await evaluate_cod_limits(
            user_id=1,
            cod_val=3000.0,
            user_np_client=user_client,
            storage_manager=multi_user_manager,
            eff_settings=mock_settings,
        )

        assert res["is_exceeded"] is True
        assert res["suggested_profile"] is not None
        assert res["suggested_profile"]["name"] == "Профіль 2 (ФОП)"
        assert "💡 *Пропозиція:* У користувача" in res["summary_text"]
        assert "Профіль 2 (ФОП)" in res["summary_text"]


# --- 3. Handler & Callback tests ---

@pytest.mark.asyncio
async def test_cmd_users_renders_dashboard(multi_user_manager, mock_settings):
    stats = CODMonthlyStats(
        year=2026, month=9, month_name="Вересень 2026", from_date="01.09.2026", to_date="30.09.2026",
        total_count=3, total_sum=6000.0, items=[]
    )

    message = AsyncMock(spec=Message)
    message.from_user = User(id=1, is_bot=False, first_name="Test")
    status_msg = AsyncMock()
    message.answer = AsyncMock(return_value=status_msg)

    cmd_users_handler = _get_message_handler("cmd_users")

    with patch.object(NovaPoshtaClient, "get_monthly_cod_stats", return_value=stats):
        await cmd_users_handler(message)

        message.answer.assert_called_once()
        status_msg.edit_text.assert_called_once()
        text = status_msg.edit_text.call_args[0][0]
        assert "👥 *Керування користувачами (відправниками Нової Пошти):*" in text
        assert "Профіль 1 (Основний)" in text
        assert "Профіль 2 (ФОП)" in text
        assert "📌 *Поточний активний відправник:*" in text


@pytest.mark.asyncio
async def test_user_profile_callback_select(multi_user_manager, mock_settings):
    profiles = multi_user_manager.get_sender_profiles(1)
    p2 = [p for p in profiles if p.name == "Профіль 2 (ФОП)"][0]

    callback = AsyncMock(spec=CallbackQuery)
    callback.from_user = User(id=1, is_bot=False, first_name="Test")
    callback.message = AsyncMock()
    callback.answer = AsyncMock()

    stats = CODMonthlyStats(
        year=2026, month=9, month_name="Вересень 2026", from_date="01.09.2026", to_date="30.09.2026",
        total_count=0, total_sum=0.0, items=[]
    )

    cb_handler = _get_callback_handler("process_user_profile_callback")

    with patch.object(NovaPoshtaClient, "get_monthly_cod_stats", return_value=stats):
        callback_data = UserProfileCallback(action="select", profile_id=p2.id)
        await cb_handler(callback, callback_data)

        # Active profile should now be p2
        active = multi_user_manager.get_active_profile(1)
        assert active.id == p2.id
        callback.answer.assert_called_with(f"✅ Активним обрано «{p2.name}»!")


@pytest.mark.asyncio
async def test_user_profile_callback_delete_flow(multi_user_manager, mock_settings):
    profiles = multi_user_manager.get_sender_profiles(1)
    p2 = [p for p in profiles if p.name == "Профіль 2 (ФОП)"][0]

    callback = AsyncMock(spec=CallbackQuery)
    callback.from_user = User(id=1, is_bot=False, first_name="Test")
    callback.message = AsyncMock()
    callback.answer = AsyncMock()

    cb_handler = _get_callback_handler("process_user_profile_callback")

    # Request delete prompt
    cb_data_prompt = UserProfileCallback(action="delete_prompt", profile_id="none")
    await cb_handler(callback, cb_data_prompt)
    callback.message.edit_text.assert_called_once()
    assert "Оберіть користувача, якого бажаєте видалити" in callback.message.edit_text.call_args[0][0]

    # Delete p2
    stats = CODMonthlyStats(
        year=2026, month=9, month_name="Вересень 2026", from_date="01.09.2026", to_date="30.09.2026",
        total_count=0, total_sum=0.0, items=[]
    )
    with patch.object(NovaPoshtaClient, "get_monthly_cod_stats", return_value=stats):
        cb_data_del = UserProfileCallback(action="delete", profile_id=p2.id)
        await cb_handler(callback, cb_data_del)

        # Profile 2 should be deleted
        rem_profiles = multi_user_manager.get_sender_profiles(1)
        assert len(rem_profiles) == 1
        assert rem_profiles[0].name == "Профіль 1 (Основний)"


@pytest.mark.asyncio
async def test_user_profile_callback_add_waiting_state(multi_user_manager, mock_settings):
    callback = AsyncMock(spec=CallbackQuery)
    callback.from_user = User(id=1, is_bot=False, first_name="Test")
    callback.message = AsyncMock()
    callback.answer = AsyncMock()

    cb_handler = _get_callback_handler("process_user_profile_callback")
    cb_data_add = UserProfileCallback(action="add", profile_id="new")
    await cb_handler(callback, cb_data_add)

    assert 1 in USER_ADD_PROFILE_WAITING
    callback.message.answer.assert_called_once()
    assert "API-ключ Нової Пошти" in callback.message.answer.call_args[0][0]
    USER_ADD_PROFILE_WAITING.discard(1)


@pytest.mark.asyncio
async def test_process_waybill_callback_switch_profile(multi_user_manager, mock_settings):
    profiles = multi_user_manager.get_sender_profiles(1)
    p2 = [p for p in profiles if p.name == "Профіль 2 (ФОП)"][0]

    session_id = "test-session-123"
    PENDING_SESSIONS[session_id] = {
        "user_id": 1,
        "parsed_info": ParsedRecipientInfo(
            full_name="Іван Тест",
            phone="0991112233",
            city_name="Київ",
            warehouse_number="1",
        ),
        "city": CityInfo(Ref="city-ref-1", Description="Київ"),
        "warehouse": WarehouseInfo(
            Ref="wh-ref-1",
            Description="Відділення №1: вул. Тестова",
            Number="1",
            TypeOfWarehouse="Warehouse",
            CityRef="city-ref-1",
        ),
        "destination_description": "Відділення №1: вул. Тестова",
        "declared_value": 500.0,
        "cargo_type": "Parcel",
        "cargo_description": "Тест",
        "weight": 1.0,
        "seats_amount": 1,
        "cod_amount": 0.0,
        "cod_payment_type": "cash",
        "payer_type": "Recipient",
    }

    callback = AsyncMock(spec=CallbackQuery)
    callback.from_user = User(id=1, is_bot=False, first_name="Test")
    callback.message = AsyncMock()
    callback.answer = AsyncMock()

    stats = CODMonthlyStats(
        year=2026, month=9, month_name="Вересень 2026", from_date="01.09.2026", to_date="30.09.2026",
        total_count=0, total_sum=0.0, items=[]
    )

    wb_handler = _get_callback_handler("process_waybill_callback")

    with patch.object(NovaPoshtaClient, "get_monthly_cod_stats", return_value=stats):
        cb_data = WaybillActionCallback(action="switch_profile", session_id=session_id)
        await wb_handler(callback, cb_data)

        # Active profile should have switched to p2
        assert multi_user_manager.get_active_profile(1).id == p2.id
        callback.answer.assert_called_with(f"✅ Відправника перемкнуто на «{p2.name}»! Баланс оновлено.")
        callback.message.edit_text.assert_called_once()


@pytest.mark.asyncio
async def test_user_profile_rename_callback_and_input(multi_user_manager, mock_settings):
    """Test interactive rename flow via callback and subsequent text message."""
    from src.bot.handlers import USER_RENAME_PROFILE_WAITING

    callback = AsyncMock(spec=CallbackQuery)
    callback.from_user = User(id=1, is_bot=False, first_name="Test")
    callback.message = AsyncMock()
    callback.answer = AsyncMock()

    uprof_handler = _get_callback_handler("process_user_profile_callback")

    # 1. Trigger rename_prompt with 2 profiles -> opens selection keyboard
    cb_prompt = UserProfileCallback(action="rename_prompt", profile_id="none")
    await uprof_handler(callback, cb_prompt)
    callback.message.edit_text.assert_called_once()
    assert "Оберіть користувача" in callback.message.edit_text.call_args[0][0]

    # 2. Select profile p1 for renaming
    profiles = multi_user_manager.get_sender_profiles(1)
    p1 = profiles[0]
    cb_target = UserProfileCallback(action="rename_target", profile_id=p1.id)
    await uprof_handler(callback, cb_target)
    assert USER_RENAME_PROFILE_WAITING[1] == p1.id
    callback.message.answer.assert_called_once()
    assert "Введіть новий псевдонім" in callback.message.answer.call_args[0][0]

    # 3. User sends new pseudonym text
    text_msg = AsyncMock(spec=Message)
    text_msg.from_user = User(id=1, is_bot=False, first_name="Test")
    text_msg.text = "Мій Улюблений Склад"
    text_msg.answer = AsyncMock()

    msg_handler = _get_message_handler("process_text_message")
    await msg_handler(text_msg)

    # State cleared and profile alias updated
    assert 1 not in USER_RENAME_PROFILE_WAITING
    updated_p1 = multi_user_manager.get_sender_profile(1, p1.id)
    assert updated_p1.alias == "Мій Улюблений Склад"
    assert updated_p1.name == "Мій Улюблений Склад"


@pytest.mark.asyncio
async def test_cmd_set_alias_and_reset(multi_user_manager, mock_settings):
    """Test /set_alias and /reset_alias commands."""
    cmd_set_alias_handler = _get_message_handler("cmd_set_alias")
    cmd_reset_alias_handler = _get_message_handler("cmd_reset_alias")

    msg = AsyncMock(spec=Message)
    msg.from_user = User(id=1, is_bot=False, first_name="Test")
    msg.text = "/set_alias Склад Дніпро"
    msg.answer = AsyncMock()

    stats = CODMonthlyStats(
        year=2026, month=9, month_name="Вересень 2026", from_date="01.09.2026", to_date="30.09.2026",
        total_count=0, total_sum=0.0, items=[]
    )

    with patch.object(NovaPoshtaClient, "get_monthly_cod_stats", return_value=stats):
        await cmd_set_alias_handler(msg)
        active_p = multi_user_manager.get_active_profile(1)
        assert active_p.alias == "Склад Дніпро"
        assert active_p.name == "Склад Дніпро"

        # Now reset alias
        msg_reset = AsyncMock(spec=Message)
        msg_reset.from_user = User(id=1, is_bot=False, first_name="Test")
        msg_reset.text = "/reset_alias"
        msg_reset.answer = AsyncMock()

        await cmd_reset_alias_handler(msg_reset)
        reset_p = multi_user_manager.get_active_profile(1)
        assert reset_p.alias is None

