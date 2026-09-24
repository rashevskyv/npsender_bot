"""Unit tests for Client Card generation, Users keyboard redesign, and bot menu commands."""

import os
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from aiogram import Bot
from aiogram.types import Message, User, CallbackQuery

from src.config import Settings
from src.nova_poshta.client import NovaPoshtaClient
from src.storage import UserSettingsManager, SenderProfile
from src.utils.barcode_gen import generate_client_card_image, generate_code128_barcode
from src.bot.keyboards import (
    get_users_management_keyboard,
    get_client_card_keyboard,
    get_settings_keyboard,
    get_main_reply_keyboard,
    ClientCardCallback,
    UserProfileCallback,
)
from src.bot.main import setup_bot_commands
from src.bot.handlers import router, register_handlers
from src.ai.extractor import AIExtractor


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


def test_users_management_keyboard_formatting():
    """Verify that buttons use a dedicated 2-row layout per profile to avoid mobile truncation."""
    p1 = SenderProfile(id="p1", name="Рашевський Владіслав Сергійович", nova_poshta_api_key="k1", sender_phone="380502559301")
    p2 = SenderProfile(id="p2", name="Рашевський Владіслав Сергійович", nova_poshta_api_key="k2", sender_phone="380501234567")

    balances = {
        "p1": {"rem_sum": 29999.0},
        "p2": {"rem_sum": 1099.0},
    }

    kb = get_users_management_keyboard([p1, p2], active_profile_id="p1", balances_map=balances)
    buttons = [btn for row in kb.inline_keyboard for btn in row]

    active_name_btn = buttons[0]
    active_bal_btn = buttons[1]
    inactive_name_btn = buttons[2]
    inactive_bal_btn = buttons[3]

    # Active profile checks (duplicate names get disambiguated with shortened name and phone suffix)
    assert active_name_btn.text == "✅ Рашевський В. С. (..9301)"
    assert active_bal_btn.text == "💰 Залишок: 29999 грн"

    # Inactive profile checks
    assert inactive_name_btn.text == "🔄 Рашевський В. С. (..4567)"
    assert inactive_bal_btn.text == "💰 Залишок: 1099 грн"

    # Profile with alias
    p1.alias = "Мій Основний"
    kb_alias = get_users_management_keyboard([p1, p2], active_profile_id="p1", balances_map=balances)
    buttons_alias = [btn for row in kb_alias.inline_keyboard for btn in row]
    assert buttons_alias[0].text == "✅ Мій Основний"
    assert buttons_alias[1].text == "💰 Залишок: 29999 грн"


def test_pseudonym_rename_and_disambiguation(tmp_path):
    """Test setting aliases and renaming profiles in storage and keyboards."""
    from src.storage import UserSettingsManager, SenderProfile
    from src.bot.keyboards import format_profile_display_name, get_rename_profile_selection_keyboard

    storage_file = os.path.join(tmp_path, "user_settings.json")
    manager = UserSettingsManager(filepath=storage_file)

    p1 = SenderProfile(
        id="p1",
        name="Рашевський Владіслав Сергійович",
        sender_name="Рашевський Владіслав Сергійович",
        sender_phone="380502559301",
        nova_poshta_api_key="k1",
    )
    p2 = SenderProfile(
        id="p2",
        name="Рашевський Владіслав Сергійович",
        sender_name="Рашевський Владіслав Сергійович",
        sender_phone="380509998877",
        nova_poshta_api_key="k2",
    )
    manager.add_sender_profile(12345, p1, set_active=True)
    manager.add_sender_profile(12345, p2, set_active=False)

    # Initial disambiguation when both profiles share the same name
    profiles = manager.get_sender_profiles(12345)
    assert format_profile_display_name(profiles[0], profiles) == "Рашевський В. С. (..9301)"
    assert format_profile_display_name(profiles[1], profiles) == "Рашевський В. С. (..8877)"

    # Set alias for p1
    updated = manager.rename_sender_profile(12345, "p1", "Основний ФОП")
    assert updated.alias == "Основний ФОП"
    assert updated.name == "Основний ФОП"

    profiles = manager.get_sender_profiles(12345)
    assert format_profile_display_name(profiles[0], profiles) == "Основний ФОП"
    assert format_profile_display_name(profiles[1], profiles) == "Рашевський В. С."

    # Rename selection keyboard contains the profiles
    rename_kb = get_rename_profile_selection_keyboard(profiles)
    btns = [btn for row in rename_kb.inline_keyboard for btn in row]
    assert any("Основний ФОП" in btn.text for btn in btns)

    # Reset alias for p1
    reset_p = manager.rename_sender_profile(12345, "p1", "")
    assert reset_p.alias is None
    assert reset_p.name == "Рашевський Владіслав Сергійович"


def test_client_card_keyboard_structure():
    """Verify client card action buttons and callback data."""
    kb_card = get_client_card_keyboard(profile_id="prof_123", current_mode="card")
    assert len(kb_card.inline_keyboard) == 2
    assert "📱 Штрихкод телефону" in kb_card.inline_keyboard[0][0].text

    kb_phone = get_client_card_keyboard(profile_id="prof_123", current_mode="phone")
    assert "💳 Штрихкод картки (CID)" in kb_phone.inline_keyboard[0][0].text

    settings_kb = get_settings_keyboard()
    assert any("💳 Картка клієнта" in btn.text for row in settings_kb.inline_keyboard for btn in row)

    main_kb = get_main_reply_keyboard()
    reply_texts = [btn.text for row in main_kb.keyboard for btn in row]
    assert "💳 Картка клієнта" in reply_texts


def test_generate_client_card_image_png_validity():
    """Verify that generate_client_card_image creates valid PNG image bytes."""
    png_bytes = generate_client_card_image(
        full_name="Рашевський Владислав Сергійович",
        phone="380502559301",
        card_number="CID1428940191989",
        barcode_data="CID1428940191989",
        barcode_label="CID1428940191989",
    )
    assert png_bytes is not None
    assert len(png_bytes) > 1000
    # Check PNG magic signature
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


def test_generate_client_card_image_phone_barcode():
    """Verify card image with phone number as barcode."""
    png_bytes = generate_client_card_image(
        full_name="Тестовий Клієнт",
        phone="380501234567",
        card_number="",
        barcode_data="380501234567",
        barcode_label="+380501234567",
    )
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.asyncio
async def test_get_loyalty_info(mock_settings):
    """Verify get_loyalty_info parses LoyaltyUser/getLoyaltyInfoByApiKey correctly."""
    client = NovaPoshtaClient(mock_settings)
    mock_res = {
        "success": True,
        "data": [
            {
                "UserLogin": "046186045",
                "LoyaltyCard": "CID1428940191989",
                "FirstName": "Владислав",
                "LastName": "Рашевський",
                "MiddleName": "Сергійович",
                "Phone": "380502559301",
                "Discount": 0,
                "LoyaltyCardType": "loyalty rozniza",
            }
        ],
    }

    with patch.object(client, "_post", AsyncMock(return_value=mock_res)):
        loyalty = await client.get_loyalty_info()
        assert loyalty["full_name"] == "Рашевський Владислав Сергійович"
        assert loyalty["phone"] == "380502559301"
        assert loyalty["loyalty_card"] == "CID1428940191989"
        assert loyalty["user_login"] == "046186045"


@pytest.mark.asyncio
async def test_setup_bot_commands():
    """Verify setup_bot_commands calls set_my_commands and set_chat_menu_button."""
    mock_bot = AsyncMock(spec=Bot)
    await setup_bot_commands(mock_bot)
    mock_bot.set_my_commands.assert_called_once()
    mock_bot.set_chat_menu_button.assert_called_once()


@pytest.mark.asyncio
async def test_cmd_add_user_flow(tmp_path, mock_settings):
    """Verify that adding user via command deletes status_msg and calls message.answer."""
    manager = UserSettingsManager(filepath=str(tmp_path / "u.json"), drafts_filepath=str(tmp_path / "d.json"))
    client = NovaPoshtaClient(mock_settings)
    extractor = AIExtractor(mock_settings)
    register_handlers(mock_settings, extractor, client, manager)

    mock_profile_data = {
        "sender_name": "Іванов Іван",
        "sender_phone": "380501112233",
        "sender_counterparty_ref": "cp-1",
        "sender_contact_ref": "con-1",
    }

    message = AsyncMock(spec=Message)
    message.from_user = User(id=42, is_bot=False, first_name="Ivan")
    message.text = "/add_user valid_np_key_1234567890 Тест"
    status_msg = AsyncMock(spec=Message)
    message.answer = AsyncMock(return_value=status_msg)

    cmd_add_user_handler = _get_message_handler("cmd_add_user")

    with patch.object(client, "fetch_sender_profile", AsyncMock(return_value=mock_profile_data)):
        await cmd_add_user_handler(message)

        status_msg.delete.assert_called_once()
        assert message.answer.call_count >= 1
        last_answer_call = message.answer.call_args
        assert "✅ *Користувача «Тест» успішно додано!*" in last_answer_call[0][0]
        assert last_answer_call[1]["reply_markup"] == get_main_reply_keyboard()


@pytest.mark.asyncio
async def test_cmd_client_card_flow(tmp_path, mock_settings):
    """Verify that /client_card generates and sends the digital client card."""
    manager = UserSettingsManager(filepath=str(tmp_path / "u.json"), drafts_filepath=str(tmp_path / "d.json"))
    manager.update_user_settings(
        user_id=42,
        nova_poshta_api_key="np_test_key",
        sender_name="Рашевський Владислав",
        sender_phone="380502559301",
    )
    client = NovaPoshtaClient(mock_settings)
    extractor = AIExtractor(mock_settings)
    register_handlers(mock_settings, extractor, client, manager)

    mock_loyalty = {
        "full_name": "Рашевський Владислав",
        "phone": "380502559301",
        "loyalty_card": "CID1428940191989",
        "user_login": "046186045",
    }

    message = AsyncMock(spec=Message)
    message.from_user = User(id=42, is_bot=False, first_name="Vlad")
    status_msg = AsyncMock(spec=Message)
    message.answer = AsyncMock(return_value=status_msg)
    message.answer_photo = AsyncMock()

    cmd_client_card_handler = _get_message_handler("cmd_client_card")

    with patch.object(client, "get_loyalty_info", AsyncMock(return_value=mock_loyalty)):
        await cmd_client_card_handler(message)

        message.answer_photo.assert_called_once()
        call_kwargs = message.answer_photo.call_args[1]
        assert "💳 *Картка клієнта Нової Пошти*" in call_kwargs["caption"]
        assert "CID1428940191989" in call_kwargs["caption"]

