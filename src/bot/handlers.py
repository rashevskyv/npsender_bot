"""Telegram bot message handlers and callback handlers in Ukrainian."""

import asyncio
import datetime
import logging
import uuid
from typing import Dict, Any, Optional, List

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile

from src.config import Settings
from src.storage import UserSettingsManager, UserCustomSettings, SavedDraft, SavedScanSheet
from src.ai.schemas import ParsedRecipientInfo
from src.ai.extractor import AIExtractor
from src.nova_poshta.client import NovaPoshtaClient
from src.utils.barcode_gen import generate_code128_barcode
from src.bot.keyboards import (
    get_main_reply_keyboard,
    get_confirmation_keyboard,
    WaybillActionCallback,
    DraftActionCallback,
    get_draft_keyboard,
    CitySelectCallback,
    get_city_selection_keyboard,
    RegisterActionCallback,
    get_register_keyboard,
    AddressConfirmCallback,
    get_address_confirmation_keyboard,
)

logger = logging.getLogger(__name__)
router = Router()

# In-memory storage for active pending waybill verification sessions
PENDING_SESSIONS: Dict[str, Dict[str, Any]] = {}
USER_ACTIVE_SESSIONS: Dict[int, str] = {}  # user_id -> active session_id
VALUE_OPTIONS = [500.0, 1000.0, 2000.0, 5000.0, 10000.0]

# Debouncer buffers for multi-part forwarded messages
USER_MESSAGE_BUFFERS: Dict[int, List[str]] = {}
USER_DEBOUNCE_TASKS: Dict[int, asyncio.Task] = {}
USER_LAST_MESSAGES: Dict[int, Message] = {}


def clear_user_active_session(user_id: int):
    """Clear active waybill session for a given user."""
    session_id = USER_ACTIVE_SESSIONS.pop(user_id, None)
    if session_id:
        PENDING_SESSIONS.pop(session_id, None)


def _parse_draft_date(date_str: str) -> Optional[datetime.datetime]:
    """Parse draft date_created string safely into datetime object."""
    if not date_str:
        return None
    try:
        return datetime.datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    except Exception:
        try:
            return datetime.datetime.strptime(date_str.split(".")[0], "%Y-%m-%d %H:%M:%S")
        except Exception:
            try:
                # Handle DD.MM.YYYY format
                parts = date_str.split(".")
                if len(parts) == 3:
                    return datetime.datetime(int(parts[2]), int(parts[1]), int(parts[0]))
            except Exception:
                pass
            return None


def _is_recent_scansheet(date_str: str, max_days: int = 2) -> bool:
    """Check if scansheet creation date is within max_days (inclusive)."""
    if not date_str:
        return True
    d_obj = _parse_draft_date(date_str)
    if not d_obj:
        return True
    now = datetime.datetime.now()
    diff_days = (now.date() - d_obj.date()).days
    return diff_days <= max_days


def format_relative_delivery_date(raw_date_str: Optional[str]) -> str:
    """Format estimated delivery date string into relative Ukrainian text:
    - Today -> 'Сьогодні' (or 'Сьогодні о HH:MM')
    - Tomorrow -> 'Завтра' (or 'Завтра о HH:MM')
    - Day after tomorrow -> 'Післязавтра' (or 'Післязавтра о HH:MM')
    - > 2 days -> 'DD.MM.YYYY' (or 'DD.MM.YYYY о HH:MM')
    """
    if not raw_date_str or not raw_date_str.strip():
        return "Не вказано"

    clean_str = raw_date_str.strip()
    parsed_dt = None
    time_str = ""

    # Check for time part in string
    if " " in clean_str:
        parts = clean_str.split(" ", 1)
        date_part = parts[0].strip()
        time_part = parts[1].strip()
        if ":" in time_part:
            time_components = time_part.split(":")
            time_str = f"{time_components[0].zfill(2)}:{time_components[1].zfill(2)}"
        clean_str = date_part

    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            parsed_dt = datetime.datetime.strptime(clean_str, fmt).date()
            break
        except Exception:
            pass

    if not parsed_dt:
        return raw_date_str

    now_date = datetime.date.today()
    diff_days = (parsed_dt - now_date).days

    if diff_days == 0:
        day_label = "Сьогодні"
    elif diff_days == 1:
        day_label = "Завтра"
    elif diff_days == 2:
        day_label = "Післязавтра"
    else:
        day_label = parsed_dt.strftime("%d.%m.%Y")

    if time_str:
        return f"{day_label} о {time_str}"
    return day_label


def filter_user_drafts(
    drafts: List[SavedDraft],
    time_period: Optional[str] = None,
    cargo_query: Optional[str] = None,
) -> List[SavedDraft]:
    """Filter list of SavedDraft items by time period or cargo description."""
    filtered = list(drafts)
    now = datetime.datetime.now()

    if time_period == "today":
        today_date = now.date()
        filtered = [
            d for d in filtered
            if _parse_draft_date(d.created_at) and _parse_draft_date(d.created_at).date() == today_date
        ]
    elif time_period == "yesterday":
        yesterday_date = (now - datetime.timedelta(days=1)).date()
        filtered = [
            d for d in filtered
            if _parse_draft_date(d.created_at) and _parse_draft_date(d.created_at).date() == yesterday_date
        ]
    elif time_period == "yesterday_before_noon":
        yesterday_date = (now - datetime.timedelta(days=1)).date()
        filtered = [
            d for d in filtered
            if _parse_draft_date(d.created_at)
            and _parse_draft_date(d.created_at).date() == yesterday_date
            and _parse_draft_date(d.created_at).hour < 12
        ]

    if cargo_query:
        q_lower = cargo_query.lower()
        filtered = [
            d for d in filtered
            if q_lower in d.cargo_description.lower() or q_lower in d.recipient_name.lower()
        ]

    return filtered


def register_handlers(
    settings: Settings,
    ai_extractor: AIExtractor,
    np_client: NovaPoshtaClient,
    storage_manager: UserSettingsManager,
):
    """Factory to inject dependencies into router handlers."""

    @router.message(Command("start"))
    async def cmd_start(message: Message):
        """Welcome message and basic instructions."""
        clear_user_active_session(message.from_user.id)
        welcome_text = (
            "👋 **Вітаємо у боті автоматичної генерації ТТН Нової Пошти!**\n\n"
            "Надішліть мені реквізити отримувача у довільному форматі (ПІБ, телефон, місто, номер відділення або поштомату), "
            "і я за допомогою штучного інтелекту розпаршу дані та сформую express-накладну (ТТН).\n\n"
            "**Приклад повідомлення:**\n"
            "`Юрченко Роман Сергійович 0995360818 Київ поштомат 26584`\n\n"
            "**Користуйтеся кнопками меню нижче для швидкого доступу!**"
        )
        await message.answer(
            welcome_text, parse_mode="Markdown", reply_markup=get_main_reply_keyboard()
        )

    @router.message(Command("help"))
    @router.message(F.text == "❓ Допомога")
    async def cmd_help(message: Message):
        """Help instructions."""
        clear_user_active_session(message.from_user.id)
        help_text = (
            "📖 **Як користуватися ботом:**\n\n"
            "1. **Налаштування ключів (за бажанням):** Натисніть кнопку `⚙️ Налаштування` або скористайтеся командою `/set_np_key ВАШ_КЛЮЧ`, щоб підключити власний акаунт Нової Пошти.\n"
            "2. **Надішліть реквізити:** Надішліть дані отримувача одним повідомленням у довільному порядку.\n"
            "3. **Автоматична валідація та контекст:** Бот перевірить місто та відділення у базі НП. Якщо ви захочете уточнити опис (наприклад, написати слово *'сувенір'*) або змінити оцінку, просто надішліть доповнення наступним повідомленням!\n"
            "4. **Інтерактивні кнопки:** Використовуйте кнопки під карткою для зміни платника (Отримувач/Відправник), типу вантажу чи оціночної вартості (мін. 500 грн).\n"
            "5. **Чернетки та посилки:** Кнопки `📝 Мої чернетки (ТТН)` та `📦 Активні посилки` дозволяють переглядати та видаляти створені ТТН."
        )
        await message.answer(
            help_text, parse_mode="Markdown", reply_markup=get_main_reply_keyboard()
        )

    async def ensure_user_configured(message: Message) -> bool:
        """Check if user has configured personal NP API key and AI API key."""
        u_settings = storage_manager.get_user_settings(message.from_user.id)
        has_np = bool(u_settings.nova_poshta_api_key and u_settings.nova_poshta_api_key.strip())
        has_ai = bool(u_settings.ai_api_key and u_settings.ai_api_key.strip())

        if not has_np or not has_ai:
            missing_items = []
            if not has_np:
                missing_items.append("❌ *API-ключ Нової Пошти:* не налаштовано (`/set_np_key ВАШ_КЛЮЧ`)")
            if not has_ai:
                missing_items.append("❌ *Персональний AI API-ключ:* не налаштовано (`/set_ai_key ВАШ_AI_КЛЮЧ`)")

            missing_str = "\n".join(missing_items)
            card = (
                "⚠️ *Налаштування вашого персонального профілю:*\n\n"
                f"{missing_str}\n\n"
                "💡 *Команди для налаштування:*\n"
                "• `/set_np_key ВАШ_КЛЮЧ` — прив'язати API-ключ Нової Пошти\n"
                "• `/set_ai_key ВАШ_КЛЮЧ` — прив'язати персональний AI API-ключ\n"
                "• `/set_ai_url URL` — вказати власну адресу AI (напр. OpenAI, Gemini Web2API)\n"
                "• `/set_ai_model MODEL` — обрати модель (напр. `gpt-4o-mini`, `gemini-2.5-flash`)\n"
                "• `/set_city НазваМіста` — обрати місто відправки\n"
                "• `/set_warehouse Номер` — обрати відділення відправки\n\n"
                "⚙️ Перевірити статус вашого профілю можна командою `/settings` або `/profile`."
            )
            await message.answer(card, parse_mode="Markdown")
            return False
        return True

    @router.message(Command("settings"))
    @router.message(Command("profile"))
    @router.message(F.text == "⚙️ Налаштування")
    async def cmd_settings(message: Message):
        """Show current user configuration status."""
        clear_user_active_session(message.from_user.id)
        u_settings = storage_manager.get_user_settings(message.from_user.id)
        is_cfg = storage_manager.is_user_configured(message.from_user.id)

        status_icon = "✅ Підключено" if is_cfg else "⚠️ Потрібне налаштування"
        masked_np_key = (
            f"`{u_settings.nova_poshta_api_key[:6]}...{u_settings.nova_poshta_api_key[-4:]}`"
            if u_settings.nova_poshta_api_key
            else "_Не вказано_"
        )
        masked_ai_key = (
            f"`{u_settings.ai_api_key[:6]}...{u_settings.ai_api_key[-4:]}`"
            if u_settings.ai_api_key
            else "_Не вказано_"
        )
        ai_url_display = u_settings.ai_base_url or "https://api.openai.com/v1"
        ai_model_display = u_settings.ai_model or settings.ai_model

        card = (
            f"⚙️ *Персональний профіль користувача:* [{message.from_user.full_name}]\n\n"
            f"📊 *Загальний статус:* {status_icon}\n\n"
            "📮 *Дані Нової Пошти:*\n"
            f"• 🔑 *API-ключ НП:* {masked_np_key}\n"
            f"• 👤 *ПІБ відправника:* `{u_settings.sender_name or 'Не підтягнуто'}`\n"
            f"• 📞 *Телефон:* `{u_settings.sender_phone or 'Не підтягнуто'}`\n"
            f"• 🏙 *Місто відправки:* `{u_settings.sender_city_name or 'Не вказано'}`\n"
            f"• 📦 *Відділення відправки:* `{u_settings.sender_warehouse_name or 'Не вказано'}`\n\n"
            "🧠 *Дані AI-провайдера:*\n"
            f"• 🔑 *AI API-ключ:* {masked_ai_key}\n"
            f"• 🌐 *URL API:* `{ai_url_display}`\n"
            f"• 🤖 *Модель AI:* `{ai_model_display}`\n\n"
            "💡 *Команди для керування:*\n"
            "• `/set_np_key ВАШ_КЛЮЧ` — прив'язати API-ключ НП\n"
            "• `/set_ai_key ВАШ_КЛЮЧ` — прив'язати AI API-ключ\n"
            "• `/set_ai_url URL` — змінити URL AI-провайдера\n"
            "• `/set_ai_model MODEL` — змінити модель AI\n"
            "• `/set_name ПІБ` — змінити ПІБ відправника\n"
            "• `/set_city НазваМіста` — змінити місто відправки\n"
            "• `/set_warehouse Номер` — змінити відділення відправки"
        )
        await message.answer(card, parse_mode="Markdown", reply_markup=get_main_reply_keyboard())

    @router.message(Command("set_name"))
    @router.message(Command("set_sender_name"))
    async def cmd_set_sender_name(message: Message):
        """Set user's sender Full Name (ПІБ)."""
        clear_user_active_session(message.from_user.id)
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("⚠️ *Використання:* `/set_name Прізвище Ім'я По-батькові`", parse_mode="Markdown")
            return

        new_name = parts[1].strip()
        storage_manager.update_user_settings(
            message.from_user.id,
            sender_name=new_name,
        )
        await message.answer(
            f"✅ *ПІБ відправника успішно оновлено:* `{new_name}`",
            parse_mode="Markdown",
        )

    @router.message(Command("set_city"))
    async def cmd_set_city(message: Message):
        """Set user's sender city."""
        clear_user_active_session(message.from_user.id)
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("⚠️ *Використання:* `/set_city НазваМіста` (наприклад, `/set_city Київ`)", parse_mode="Markdown")
            return

        city_query = parts[1].strip()
        status_msg = await message.answer(f"🔍 *Пошук міста `{city_query}` у базі Нової Пошти...*", parse_mode="Markdown")
        try:
            eff_settings = storage_manager.get_effective_settings(message.from_user.id, settings)
            user_np_client = NovaPoshtaClient(eff_settings)
            cities = await user_np_client.search_city(city_query)
            if not cities:
                await status_msg.edit_text(f"❌ *Місто `{city_query}` не знайдено.* Спробуйте уточнити назву.", parse_mode="Markdown")
                return

            city = cities[0]
            storage_manager.update_user_settings(
                message.from_user.id,
                sender_city_ref=city.ref,
                sender_city_name=city.description,
            )
            await status_msg.edit_text(
                f"✅ *Місто відправника успішно збережено:* `{city.description}`\n\n"
                "Тепер вкажіть номер вашого відділення відправки: `/set_warehouse Номер`",
                parse_mode="Markdown",
            )
        except Exception as e:
            await status_msg.edit_text(f"❌ *Помилка встановлення міста:* {str(e)}", parse_mode="Markdown")

    @router.message(Command("set_warehouse"))
    async def cmd_set_warehouse(message: Message):
        """Set user's sender warehouse / postomat."""
        clear_user_active_session(message.from_user.id)
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip().isdigit():
            await message.answer("⚠️ *Використання:* `/set_warehouse НомерВідділення` (наприклад, `/set_warehouse 5`)", parse_mode="Markdown")
            return

        wh_num = int(parts[1].strip())
        u_settings = storage_manager.get_user_settings(message.from_user.id)
        if not u_settings.sender_city_ref:
            await message.answer("⚠️ *Спочатку встановіть місто відправника:* `/set_city НазваМіста`", parse_mode="Markdown")
            return

        status_msg = await message.answer(f"🔍 *Пошук відділення №{wh_num}...*", parse_mode="Markdown")
        try:
            eff_settings = storage_manager.get_effective_settings(message.from_user.id, settings)
            user_np_client = NovaPoshtaClient(eff_settings)
            wh = await user_np_client.get_warehouse(u_settings.sender_city_ref, wh_num)
            if not wh:
                await status_msg.edit_text(f"❌ *Відділення №{wh_num} у вашому місті не знайдено.*", parse_mode="Markdown")
                return

            storage_manager.update_user_settings(
                message.from_user.id,
                sender_address_ref=wh.ref,
                sender_warehouse_name=wh.description,
            )
            await status_msg.edit_text(
                f"✅ *Відділення відправника збережено:* `{wh.description}`\n\n"
                "🎉 Вітаємо! Ваш профіль повністю налаштовано. Надішліть дані отримувача у повідомленні для створення ТТН!",
                parse_mode="Markdown",
            )
        except Exception as e:
            await status_msg.edit_text(f"❌ *Помилка встановлення відділення:* {str(e)}", parse_mode="Markdown")

    @router.message(Command("set_np_key"))
    async def cmd_set_np_key(message: Message):
        """Set user's personal Nova Poshta API key."""
        clear_user_active_session(message.from_user.id)
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                "⚠️ *Використання:* `/set_np_key ВАШ_API_КЛЮЧ_НОВОЇ_ПОШТИ`", parse_mode="Markdown"
            )
            return

        api_key = parts[1].strip()
        status_msg = await message.answer(
            "⏳ *Перевірка API-ключа Нової Пошти та підтягування профілю відправника...*", parse_mode="Markdown"
        )

        try:
            profile = await np_client.fetch_sender_profile(api_key)

            storage_manager.update_user_settings(
                message.from_user.id,
                nova_poshta_api_key=api_key,
                sender_counterparty_ref=profile["sender_counterparty_ref"],
                sender_contact_ref=profile["sender_contact_ref"],
                sender_city_ref=profile["sender_city_ref"] or None,
                sender_address_ref=profile["sender_address_ref"] or None,
                sender_phone=profile["sender_phone"],
                sender_name=profile["sender_name"],
            )

            await status_msg.edit_text(
                "✅ *API-ключ Нової Пошти та профіль відправника успішно підв'язано!*\n\n"
                f"👤 *Відправник:* `{profile['sender_name']}`\n"
                f"📞 *Телефон:* `{profile['sender_phone'] or 'Не вказано'}`\n"
                f"🔑 *Ключ:* `{api_key[:6]}...{api_key[-4:]}`\n\n"
                "🏙 Тепер вкажіть місто відправки командою `/set_city НазваМіста`\n"
                "📦 Та номер відділення/поштомату: `/set_warehouse Номер`",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Failed to set Nova Poshta API key: {e}")
            await status_msg.edit_text(
                f"❌ *Помилка перевірки ключа Нової Пошти:* {str(e)}", parse_mode="Markdown"
            )

    @router.message(Command("set_ai_key"))
    async def cmd_set_ai_key(message: Message):
        """Set user's personal AI API key."""
        clear_user_active_session(message.from_user.id)
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                "⚠️ *Використання:* `/set_ai_key ВАШ_AI_API_KEY`", parse_mode="Markdown"
            )
            return

        api_key = parts[1].strip()
        storage_manager.update_user_settings(message.from_user.id, ai_api_key=api_key)

        await message.answer(
            f"✅ *Персональний AI API-ключ збережено!* (`{api_key[:6]}...{api_key[-4:]}`)\n\n"
            "За бажанням вкажіть власний URL: `/set_ai_url URL` або модель: `/set_ai_model НАЗВА`",
            parse_mode="Markdown",
        )

    @router.message(Command("set_ai_url"))
    async def cmd_set_ai_url(message: Message):
        """Set user's custom AI base URL."""
        clear_user_active_session(message.from_user.id)
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                "⚠️ *Використання:* `/set_ai_url http://localhost:8081/v1` (або `https://api.openai.com/v1`)",
                parse_mode="Markdown",
            )
            return

        url = parts[1].strip()
        storage_manager.update_user_settings(message.from_user.id, ai_base_url=url)

        await message.answer(
            f"✅ *URL AI-провайдера успішно збережено:* `{url}`",
            parse_mode="Markdown",
        )

    @router.message(Command("set_ai_model"))
    async def cmd_set_ai_model(message: Message):
        """Set user's custom AI model name."""
        clear_user_active_session(message.from_user.id)
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                "⚠️ *Використання:* `/set_ai_model gpt-4o-mini` (або `gemini-2.5-flash`)",
                parse_mode="Markdown",
            )
            return

        model_name = parts[1].strip()
        storage_manager.update_user_settings(message.from_user.id, ai_model=model_name)

        await message.answer(
            f"✅ *Модель AI успішно збережено:* `{model_name}`",
            parse_mode="Markdown",
        )

    @router.message(Command("set_card"))
    async def cmd_set_card(message: Message):
        """Set user's default bank card mask for cash on delivery payout."""
        clear_user_active_session(message.from_user.id)
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                "⚠️ *Використання:* `/set_card 414932******1234` (вкажіть маску або номер вашої банківської картки)",
                parse_mode="Markdown",
            )
            return

        card_str = parts[1].strip()
        storage_manager.update_user_settings(message.from_user.id, sender_card_mask=card_str)
        await message.answer(
            f"✅ *Банківську картку для виплати наложки успішно збережено:* `{card_str}`",
            parse_mode="Markdown",
        )

    @router.message(Command("reset_settings"))
    async def cmd_reset_settings(message: Message):
        """Reset custom settings to system defaults."""
        clear_user_active_session(message.from_user.id)
        storage_manager.reset_user_settings(message.from_user.id)
        await message.answer(
            "🔄 *Ваші персональні ключі та налаштування скинуто до системних за замовчуванням.*",
            parse_mode="Markdown",
            reply_markup=get_main_reply_keyboard(),
        )

    @router.message(Command("settings"))
    @router.message(F.text == "⚙️ Налаштування")
    async def cmd_settings(message: Message):
        """Show current effective settings for the user."""
        clear_user_active_session(message.from_user.id)
        eff = storage_manager.get_effective_settings(message.from_user.id, settings)
        u_custom = storage_manager.get_user_settings(message.from_user.id)

        has_custom_np = bool(u_custom.nova_poshta_api_key)
        has_custom_ai = bool(u_custom.ai_api_key)

        status_text = (
            "⚙️ **Ваші активні налаштування:**\n\n"
            f"• **Ключ Нової Пошти:** `{'Особистий (' + eff.nova_poshta_api_key[:6] + '...)' if has_custom_np else 'Системний'}`\n"
            f"• **Профіль відправника:** `{u_custom.sender_name or 'Основний акаунт'}`\n"
            f"• **Телефон відправника:** `{eff.sender_phone or 'Не вказано'}`\n"
            f"• **AI Провайдер:** `{eff.ai_provider}`\n"
            f"• **AI Ключ:** `{'Особистий' if has_custom_ai else 'Системний'}`\n"
            f"• **AI Модель:** `{eff.ai_model}`\n"
            f"• **Мін. оціночна вартість:** `{eff.default_declared_value} грн`\n\n"
            "Щоб змінити ключі, використайте команди:\n"
            "`/set_np_key ВАШ_КЛЮЧ` або `/set_ai_key ВАШ_КЛЮЧ`\n"
            "Або `/reset_settings` для скидання."
        )
        await message.answer(
            status_text, parse_mode="Markdown", reply_markup=get_main_reply_keyboard()
        )

    @router.message(Command("drafts"))
    @router.message(F.text == "📝 Мої чернетки (ТТН)")
    async def cmd_drafts(message: Message):
        """Show list of active created express waybill drafts."""
        clear_user_active_session(message.from_user.id)
        if not await ensure_user_configured(message):
            return

        user_id = message.from_user.id
        drafts = storage_manager.get_user_drafts(user_id)

        if not drafts:
            await message.answer(
                "📝 *Збережених чернеток ТТН не знайдено.*\n"
                "Створіть нову накладну, надіславши реквізити отримувача боту!",
                parse_mode="Markdown",
                reply_markup=get_main_reply_keyboard(),
            )
            return

        eff_settings = storage_manager.get_effective_settings(user_id, settings)
        if eff_settings.nova_poshta_api_key:
            user_np_client = NovaPoshtaClient(eff_settings)
            doc_numbers = [d.int_doc_number for d in drafts if d.int_doc_number]
            try:
                statuses = await user_np_client.get_documents_status(doc_numbers)
                sent_ids = [
                    doc_num for doc_num, info in statuses.items()
                    if info.get("is_shipped")
                ]
                if sent_ids:
                    storage_manager.purge_sent_drafts(user_id, sent_ids)
                    drafts = storage_manager.get_user_drafts(user_id)
            except Exception as e:
                logger.error(f"Error checking draft statuses for user {user_id}: {e}")

        if not drafts:
            await message.answer(
                "📝 *Активних чернеток ТТН (невідправлених) не знайдено.*\n"
                "Усі ваші створені накладні вже відправлені або знаходяться в дорозі. "
                "Ви можете переглянути їх у розділі 📦 *Активні посилки*!",
                parse_mode="Markdown",
                reply_markup=get_main_reply_keyboard(),
            )
            return

        await message.answer(
            f"📄 *Ваші збережені чернетки ТТН ({len(drafts)}):*", parse_mode="Markdown"
        )

        for draft in drafts[:10]:
            tracking_url = (
                f"https://novaposhta.ua/tracking/?cargo_number={draft.int_doc_number}"
            )
            card = (
                f"🎫 *ТТН:* `{draft.int_doc_number}`\n"
                f"👤 *Отримувач:* {draft.recipient_name}\n"
                f"📞 *Телефон:* `{draft.recipient_phone}`\n"
                f"🏙 *Місто:* {draft.city_description}\n"
                f"📦 *Пункт призначення:* {draft.warehouse_description}\n"
                f"📝 *Опис:* {draft.cargo_description}\n"
                f"💳 *Платник:* {draft.payer_type} | 💰 *Оцінка:* {int(draft.declared_value)} грн\n"
                f"📅 *Створено:* {draft.created_at}\n\n"
                f"🔗 [Відстежити ТТН на сайті Нової Пошти]({tracking_url})"
            )
            await message.answer(
                card,
                parse_mode="Markdown",
                disable_web_page_preview=True,
                reply_markup=get_draft_keyboard(ref=draft.ref),
            )

    @router.message(Command("outgoing"))
    @router.message(Command("parcels"))
    @router.message(F.text == "📤 Вихідні (що їдуть)")
    @router.message(F.text == "📦 Активні посилки")
    async def cmd_outgoing_parcels(message: Message):
        """Show active outgoing shipments sent by user (not yet received)."""
        clear_user_active_session(message.from_user.id)
        if not await ensure_user_configured(message):
            return

        eff_settings = storage_manager.get_effective_settings(message.from_user.id, settings)
        user_np_client = NovaPoshtaClient(eff_settings)

        status_msg = await message.answer(
            "🔍 *Отримання ваших вихідних посилок у дорозі...*", parse_mode="Markdown"
        )
        try:
            items = await user_np_client.get_outgoing_waybills(
                user_phone=eff_settings.sender_phone,
                user_name=eff_settings.sender_name,
                user_cp_ref=eff_settings.sender_counterparty_ref,
                days_back=30,
                limit=20,
            )
            if not items:
                await status_msg.edit_text(
                    "📤 *Активних вихідних посилок у дорозі не знайдено.*\n"
                    "Усі ваші відправлені посилки вже забрано або вони недійсні.",
                    parse_mode="Markdown",
                )
                return

            try:
                await status_msg.delete()
            except Exception:
                pass

            await message.answer(
                f"📤 *Ваші активні вихідні посилки ({len(items)}):*",
                parse_mode="Markdown",
            )

            for idx, item in enumerate(items, 1):
                tracking_url = (
                    f"https://novaposhta.ua/tracking/?cargo_number={item.int_doc_number}"
                )
                est_date = format_relative_delivery_date(item.estimated_delivery_date)
                card = (
                    f"📤 *Вихідна ТТН №{idx}:* `{item.int_doc_number}`\n"
                    f"👤 *Отримувач:* {item.recipient_name}\n"
                    f"🏙 *Пункт призначення:* {item.city_recipient}, {item.address_recipient}\n"
                    f"📝 *Опис:* {item.description}\n"
                    f"📅 *Очікуване прибуття:* {est_date}\n"
                    f"💰 *Вартість доставки:* ~{item.cost} грн | 📊 *Статус:* {item.state_name}\n\n"
                    f"🔗 [Відстежити на сайті Нової Пошти]({tracking_url})"
                )
                await message.answer(
                    card,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
        except Exception as e:
            logger.error(f"Error fetching outgoing parcels: {e}", exc_info=True)
            await message.answer(
                f"❌ *Не вдалося отримати вихідні посилки:* {str(e)}", parse_mode="Markdown"
            )

    @router.message(Command("incoming"))
    @router.message(F.text == "📥 Вхідні (що їдуть)")
    async def cmd_incoming_parcels(message: Message):
        """Show active incoming shipments traveling to user (not yet received)."""
        clear_user_active_session(message.from_user.id)
        eff_settings = storage_manager.get_effective_settings(message.from_user.id, settings)
        user_np_client = NovaPoshtaClient(eff_settings)

        status_msg = await message.answer(
            "🔍 *Отримання ваших вхідних посилок у дорозі...*", parse_mode="Markdown"
        )
        try:
            items = await user_np_client.get_incoming_waybills(
                user_phone=eff_settings.sender_phone,
                user_name=eff_settings.sender_name,
                user_cp_ref=eff_settings.sender_counterparty_ref,
                days_back=30,
                limit=20,
            )
            if not items:
                await status_msg.edit_text(
                    "📥 *Активних вхідних посилок у дорозі не знайдено.*\n"
                    "Немає очікуваних посилок, що прямують до вас.",
                    parse_mode="Markdown",
                )
                return

            try:
                await status_msg.delete()
            except Exception:
                pass

            await message.answer(
                f"📥 *Посилки, що їдуть до вас ({len(items)}):*",
                parse_mode="Markdown",
            )

            for idx, item in enumerate(items, 1):
                tracking_url = (
                    f"https://novaposhta.ua/tracking/?cargo_number={item.int_doc_number}"
                )
                est_date = format_relative_delivery_date(item.estimated_delivery_date)
                sender_info = item.sender_name or "Нова Пошта"
                card = (
                    f"📥 *Вхідна ТТН №{idx}:* `{item.int_doc_number}`\n"
                    f"🚚 *Відправник:* {sender_info}\n"
                    f"📅 *Очікуване прибуття:* **{est_date}**\n"
                    f"🏙 *Пункт призначення:* {item.city_recipient}, {item.address_recipient}\n"
                    f"📝 *Опис:* {item.description}\n"
                    f"💰 *До сплати/доставка:* ~{item.cost} грн | 📊 *Статус:* {item.state_name}\n\n"
                    f"🔗 [Відстежити на сайті Нової Пошти]({tracking_url})"
                )
                await message.answer(
                    card,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                )
        except Exception as e:
            logger.error(f"Error fetching incoming parcels: {e}", exc_info=True)
            await message.answer(
                f"❌ *Не вдалося отримати вхідні посилки:* {str(e)}", parse_mode="Markdown"
            )

    @router.message(Command("registers"))
    @router.message(F.text == "📋 Реєстри (ScanSheet)")
    async def cmd_registers(message: Message):
        """Show list of active created registers (ScanSheets) within last 2 days."""
        clear_user_active_session(message.from_user.id)
        if not await ensure_user_configured(message):
            return

        user_id = message.from_user.id
        eff_settings = storage_manager.get_effective_settings(user_id, settings)
        user_np_client = NovaPoshtaClient(eff_settings)

        status_msg = await message.answer(
            "🔍 *Отримання ваших активних реєстрів з Нової Пошти...*", parse_mode="Markdown"
        )
        try:
            raw_api_sheets = await user_np_client.get_scan_sheets(days_back=2)
            raw_saved_sheets = storage_manager.get_user_scansheets(user_id)

            # Purge locally saved sheets older than 2 days
            old_saved_refs = [
                s.ref for s in raw_saved_sheets
                if not _is_recent_scansheet(s.date_created, max_days=2)
            ]
            if old_saved_refs:
                storage_manager.purge_old_or_sent_scansheets(user_id, old_saved_refs)
                raw_saved_sheets = storage_manager.get_user_scansheets(user_id)

            # Filter API sheets by recent date & non-printed status
            recent_api_sheets = [
                s for s in raw_api_sheets
                if not s.is_printed and _is_recent_scansheet(s.date_created, max_days=2) and s.count_of_documents > 0
            ]

            # Collect document numbers from saved sheets to check if they are all shipped
            all_doc_numbers = []
            for sheet in raw_saved_sheets:
                if sheet.document_numbers:
                    all_doc_numbers.extend(sheet.document_numbers)

            shipped_doc_set = set()
            if all_doc_numbers and eff_settings.nova_poshta_api_key:
                try:
                    doc_statuses = await user_np_client.get_documents_status(all_doc_numbers)
                    for d_num, s_info in doc_statuses.items():
                        if s_info.get("is_shipped"):
                            shipped_doc_set.add(d_num)
                except Exception as e:
                    logger.error(f"Error checking TTN statuses for registers: {e}")

            # Filter saved sheets: purge sheets where all TTNs are shipped
            recent_saved_sheets = []
            sent_saved_refs = []
            for sheet in raw_saved_sheets:
                if not _is_recent_scansheet(sheet.date_created, max_days=2):
                    sent_saved_refs.append(sheet.ref)
                    continue
                if sheet.document_numbers and all(d_num in shipped_doc_set for d_num in sheet.document_numbers):
                    sent_saved_refs.append(sheet.ref)
                    continue
                recent_saved_sheets.append(sheet)

            if sent_saved_refs:
                storage_manager.purge_old_or_sent_scansheets(user_id, sent_saved_refs)

            if not recent_api_sheets and not recent_saved_sheets:
                await status_msg.edit_text(
                    "📋 *Активних невідправлених реєстрів (ScanSheet) за останні 2 дні не знайдено.*\n\n"
                    "Усі ваші створені реєстри вже відправлені або застаріли.\n\n"
                    "💡 *Ви можете попросити мене створити новий реєстр, наприклад:* \n"
                    "• *'Створи реєстр з усіх накладних за сьогодні'*\n"
                    "• *'Створи реєстр з накладних з описом сувенір'*",
                    parse_mode="Markdown",
                )
                return

            await status_msg.delete()
            await message.answer("📋 *Ваші активні реєстри (ScanSheet) за 2 дні:*", parse_mode="Markdown")

            displayed_refs = set()
            for sheet in recent_saved_sheets:
                displayed_refs.add(sheet.ref)
                card = (
                    f"📋 *Реєстр №* `{sheet.number}`\n"
                    f"📅 *Дата:* {sheet.date_created}\n"
                    f"📦 *Кількість накладних:* {sheet.count_of_documents}\n"
                )
                if sheet.document_numbers:
                    card += f"📄 *ТТН у реєстрі:* {', '.join(sheet.document_numbers)}\n"

                await message.answer(
                    card,
                    parse_mode="Markdown",
                    reply_markup=get_register_keyboard(ref=sheet.ref),
                )

            for a_sheet in recent_api_sheets:
                if a_sheet.ref not in displayed_refs:
                    card = (
                        f"📋 *Реєстр №* `{a_sheet.number}`\n"
                        f"📅 *Дата:* {a_sheet.date_created}\n"
                        f"📦 *Кількість накладних:* {a_sheet.count_of_documents}\n"
                    )
                    await message.answer(
                        card,
                        parse_mode="Markdown",
                        reply_markup=get_register_keyboard(ref=a_sheet.ref),
                    )
        except Exception as e:
            logger.error(f"Error fetching scan sheets: {e}", exc_info=True)
            await status_msg.edit_text(
                f"❌ *Не вдалося отримати список реєстрів:* {str(e)}", parse_mode="Markdown"
            )

    async def _handle_combined_text_message(message: Message, text: str):
        """Core text processing logic for accumulated recipient messages."""
        user_id = message.from_user.id
        if not await ensure_user_configured(message):
            return

        eff_settings = storage_manager.get_effective_settings(user_id, settings)
        user_ai_extractor = AIExtractor(eff_settings)
        user_np_client = NovaPoshtaClient(eff_settings)

        # Check if there is an active pending verification session for this user
        prev_parsed_info = None
        active_session_id = USER_ACTIVE_SESSIONS.get(user_id)
        if active_session_id and active_session_id in PENDING_SESSIONS:
            prev_parsed_info = PENDING_SESSIONS[active_session_id].get("parsed_info")

        status_msg = await message.answer(
            "⏳ *Обробка повідомлення та аналіз реквізитів через AI...*", parse_mode="Markdown"
        )

        try:
            # 1. Parse text with AI (with optional previous context)
            parsed_info = await user_ai_extractor.parse_text(
                text, previous_info=prev_parsed_info
            )

            # Handle Register & Waybill Filtering Intent
            if parsed_info.is_register_intent:
                action = parsed_info.register_action or "filter_drafts"

                if action == "list":
                    await cmd_registers(message)
                    return

                all_drafts = storage_manager.get_user_drafts(user_id)
                filtered_drafts = filter_user_drafts(
                    all_drafts,
                    time_period=parsed_info.filter_time_period,
                    cargo_query=parsed_info.filter_cargo_description,
                )

                period_labels = {
                    "today": "за сьогодні",
                    "yesterday": "за вчора",
                    "yesterday_before_noon": "за вчора до 12:00",
                    "all": "усі",
                }
                period_str = period_labels.get(parsed_info.filter_time_period or "all", "")
                cargo_str = f"з описом '{parsed_info.filter_cargo_description}'" if parsed_info.filter_cargo_description else ""
                filter_title = f"{period_str} {cargo_str}".strip() or "за вказаним фільтром"

                if not filtered_drafts:
                    await status_msg.edit_text(
                        f"🔍 *Накладних {filter_title} не знайдено серед ваших чернеток.*",
                        parse_mode="Markdown",
                    )
                    return

                if action == "create":
                    await status_msg.edit_text(
                        f"⏳ *Формування реєстру (ScanSheet) для {len(filtered_drafts)} накладних {filter_title}...*",
                        parse_mode="Markdown",
                    )
                    doc_refs = [d.ref for d in filtered_drafts]
                    doc_nums = [d.int_doc_number for d in filtered_drafts]

                    scansheet_info = await user_np_client.create_scan_sheet(doc_refs)

                    saved_scansheet = SavedScanSheet(
                        ref=scansheet_info.ref,
                        number=scansheet_info.number,
                        date_created=scansheet_info.date_created,
                        count_of_documents=scansheet_info.count_of_documents,
                        document_numbers=doc_nums,
                    )
                    storage_manager.add_user_scansheet(user_id, saved_scansheet)

                    barcode_bytes = generate_code128_barcode(scansheet_info.number)
                    photo_file = BufferedInputFile(barcode_bytes, filename=f"scansheet_{scansheet_info.number}.png")

                    await status_msg.delete()
                    caption_text = (
                        f"✅ *Реєстр (ScanSheet) успішно створено!*\n\n"
                        f"📋 *Номер реєстру:* `{scansheet_info.number}`\n"
                        f"📅 *Дата створення:* {scansheet_info.date_created}\n"
                        f"📦 *Кількість накладних:* {scansheet_info.count_of_documents}\n"
                        f"📄 *ТТН у реєстрі:* {', '.join(doc_nums)}\n\n"
                        "📱 *Покажіть цей штрихкод оператору Нової Пошти для сканування!*"
                    )
                    await message.answer_photo(
                        photo=photo_file,
                        caption=caption_text,
                        parse_mode="Markdown",
                        reply_markup=get_register_keyboard(ref=scansheet_info.ref),
                    )
                    return

                # Default action: show filtered drafts list
                await status_msg.edit_text(
                    f"📋 *Знайдено {len(filtered_drafts)} накладних {filter_title}:*",
                    parse_mode="Markdown",
                )
                for idx, draft in enumerate(filtered_drafts, 1):
                    card = (
                        f"*{idx}. ТТН:* `{draft.int_doc_number}` | {draft.recipient_name}\n"
                        f"🏙 *Місто:* {draft.city_description}, {draft.warehouse_description}\n"
                        f"📝 *Опис:* {draft.cargo_description} | 💰 {int(draft.declared_value)} грн\n"
                        f"📅 *Створено:* {draft.created_at}"
                    )
                    await message.answer(
                        card,
                        parse_mode="Markdown",
                        reply_markup=get_draft_keyboard(ref=draft.ref),
                    )
                return

            # Handle conversational / chat intent
            if not parsed_info.is_recipient_info:
                resp_text = (
                    parsed_info.conversational_response
                    or "👋 Привіт! Я ваш AI-асистент Нової Пошти. Надішліть реквізити отримувача (ПІБ, телефон, місто, номер відділення) для створення ТТН!"
                )
                await status_msg.edit_text(resp_text, parse_mode="Markdown")
                return

            # Get or generate active session ID
            session_id = active_session_id or str(uuid.uuid4())[:8]

            # Check for address delivery suspicion prompt
            existing_session = PENDING_SESSIONS.get(session_id) if session_id in PENDING_SESSIONS else None
            address_choice_made = existing_session.get("address_choice_made", False) if existing_session else False

            if parsed_info.has_address_suspicion and not address_choice_made:
                PENDING_SESSIONS[session_id] = {
                    "parsed_info": parsed_info,
                    "user_id": user_id,
                }
                USER_ACTIVE_SESSIONS[user_id] = session_id

                addr_parts = [p for p in [parsed_info.street_name, parsed_info.building_number, parsed_info.flat_number] if p]
                addr_text = " ".join(addr_parts) if addr_parts else "Вказано в описі"

                await status_msg.edit_text(
                    "🏡 *Виявлено можливу кур'єрську доставку на адресу (додому/в офіс):*\n\n"
                    f"📍 *Адреса:* `{addr_text}`\n\n"
                    "Бажаєте оформити кур'єрську доставку додому чи у відділення / поштомат?",
                    parse_mode="Markdown",
                    reply_markup=get_address_confirmation_keyboard(session_id),
                )
                return

            # Check missing required fields
            missing_fields = []
            if not parsed_info.last_name:
                missing_fields.append("👤 Прізвище та Ім'я отримувача")
            if not parsed_info.phone:
                missing_fields.append("📞 Номер телефону отримувача")
            if not parsed_info.city_name:
                missing_fields.append("🏙 Місто / Населений пункт")
            if not parsed_info.warehouse_number:
                missing_fields.append("📦 Номер відділення або поштомату")

            if missing_fields:
                # Save partial session so follow-up forwarded messages merge seamlessly
                PENDING_SESSIONS[session_id] = {
                    "parsed_info": parsed_info,
                    "user_id": user_id,
                }
                USER_ACTIVE_SESSIONS[user_id] = session_id

                missing_str = "\n".join([f"• {field}" for field in missing_fields])
                known_name = parsed_info.full_name or "Не вказано"
                known_phone = parsed_info.phone or "Не вказано"
                known_city = parsed_info.city_name or "Не вказано"
                known_wh = f"{'Поштомат' if parsed_info.is_postomat else 'Відділення'} № {parsed_info.warehouse_number}" if parsed_info.warehouse_number else "Не вказано"

                await status_msg.edit_text(
                    "⏳ *Отримано часткові реквізити отримувача (Очікую решту даних...)*\n\n"
                    "📋 *Вже збережено:* \n"
                    f"• ПІБ: `{known_name}`\n"
                    f"• Телефон: `{known_phone}`\n"
                    f"• Місто: `{known_city}`\n"
                    f"• Пункт: `{known_wh}`\n\n"
                    "⚠️ *Очікую доповнення:* \n"
                    f"{missing_str}\n\n"
                    "💡 *Надішліть або перепостіть наступне повідомлення з рештою даних!*"
                )
                return

            # Check if city and warehouse are already resolved in active session
            existing_session = PENDING_SESSIONS.get(active_session_id) if active_session_id else None
            has_resolved_location = (
                existing_session is not None
                and existing_session.get("city") is not None
                and existing_session.get("warehouse") is not None
            )

            city_changed = (
                not prev_parsed_info
                or parsed_info.city_name != prev_parsed_info.city_name
                or parsed_info.region_name != prev_parsed_info.region_name
            )
            wh_changed = (
                not prev_parsed_info
                or parsed_info.warehouse_number != prev_parsed_info.warehouse_number
                or parsed_info.is_postomat != prev_parsed_info.is_postomat
            )

            if has_resolved_location and not city_changed and not wh_changed:
                matched_city = existing_session["city"]
                warehouse = existing_session["warehouse"]
            else:
                # 2. Lookup City & Filter Warehouses in Nova Poshta
                await status_msg.edit_text(
                    "🔍 *Пошук населеного пункту та перевірка відділення у базі Нової Пошти...*", parse_mode="Markdown"
                )
                cities = await user_np_client.search_city(parsed_info.city_name)
                if not cities:
                    await status_msg.edit_text(
                        f"❌ Населений пункт *'{parsed_info.city_name}'* не знайдено у базі Нової Пошти. Перевірте написання."
                    )
                    return

                # Filter cities by region_name (Oblast) or district_name if specified by user
                if parsed_info.region_name:
                    reg_lower = parsed_info.region_name.lower()
                    filtered = [
                        c for c in cities
                        if (c.area and reg_lower in c.area.lower()) or (reg_lower in c.description.lower())
                    ]
                    if filtered:
                        cities = filtered

                # Find matching (city, warehouse) pairs across candidate cities
                matching_candidates = []
                for c in cities:
                    try:
                        wh = await user_np_client.get_warehouse(
                            city_ref=c.ref,
                            warehouse_number=parsed_info.warehouse_number,
                            is_postomat=parsed_info.is_postomat,
                        )
                        if wh:
                            matching_candidates.append((c, wh))
                    except Exception as e:
                        logger.warning(f"Error checking warehouse for city {c.description}: {e}")
                    await asyncio.sleep(0.25)

                w_type = "Поштомат" if parsed_info.is_postomat else "Відділення"

                if not matching_candidates:
                    await status_msg.edit_text(
                        f"❌ {w_type} *№ {parsed_info.warehouse_number}* у населеному пункті *{parsed_info.city_name}* не знайдено."
                    )
                    return

                # Filter matching_candidates further if street_name is provided
                if len(matching_candidates) > 1 and parsed_info.street_name:
                    st_lower = parsed_info.street_name.lower()
                    addr_filtered = [
                        (c, w) for (c, w) in matching_candidates
                        if st_lower in w.description.lower()
                    ]
                    if addr_filtered:
                        matching_candidates = addr_filtered

                # Handle single vs multiple candidates
                if len(matching_candidates) == 1:
                    matched_city, warehouse = matching_candidates[0]
                else:
                    # Save candidates in session and present city disambiguation keyboard
                    PENDING_SESSIONS[session_id] = {
                        "parsed_info": parsed_info,
                        "candidates": matching_candidates,
                        "user_id": user_id,
                    }
                    USER_ACTIVE_SESSIONS[user_id] = session_id

                    candidate_text_lines = [
                        f"⚠️ *Знайдено декілька населених пунктів з назвою '{parsed_info.city_name}', де є {w_type} № {parsed_info.warehouse_number}:*\n"
                    ]
                    for idx, (c, w) in enumerate(matching_candidates, 1):
                        area_info = f" ({c.area})" if c.area else ""
                        candidate_text_lines.append(f"*{idx}.* {c.description}{area_info}\n📍 `{w.description}`\n")
                    candidate_text_lines.append("Будь ласка, оберіть потрібний населений пункт нижче:")

                    await status_msg.edit_text(
                        "\n".join(candidate_text_lines),
                        parse_mode="Markdown",
                        reply_markup=get_city_selection_keyboard(matching_candidates, session_id),
                    )
                    return

            # Enforce minimum declared value of 500 UAH
            declared_val = max(
                parsed_info.declared_value or eff_settings.default_declared_value,
                500.0,
            )
            cargo_desc = parsed_info.cargo_description or "Посилка"
            cod_val = parsed_info.cod_amount or 0.0
            cod_type = parsed_info.cod_payment_type or "cash"

            # Create or update session for interactive confirmation
            session_id = active_session_id or str(uuid.uuid4())[:8]
            PENDING_SESSIONS[session_id] = {
                "parsed_info": parsed_info,
                "city": matched_city,
                "warehouse": warehouse,
                "payer_type": eff_settings.default_payer_type,
                "cargo_type": eff_settings.default_cargo_type,
                "declared_value": declared_val,
                "cargo_description": cargo_desc,
                "cod_amount": cod_val,
                "cod_payment_type": cod_type,
                "user_id": user_id,
            }
            USER_ACTIVE_SESSIONS[user_id] = session_id

            cod_str = "❌ Немає" if cod_val <= 0 else f"{int(cod_val)} грн ({'Картка' if cod_type == 'card' else 'Готівка'})"

            card_text = (
                "📋 *Розпарсені дані отримувача для перевірки:*\n\n"
                f"👤 *Отримувач:* {parsed_info.full_name}\n"
                f"📞 *Телефон:* `{parsed_info.phone}`\n"
                f"🏙 *Місто:* {matched_city.description}\n"
                f"📦 *Пункт призначення:* {warehouse.description}\n"
                f"📝 *Опис вантажу:* {cargo_desc}\n"
                f"💰 *Оціночна вартість:* {int(declared_val)} грн (Мін. 500 грн)\n"
                f"💵 *Накладений платіж:* {cod_str}\n\n"
                "Перевірте дані та оберіть дію нижче:"
            )

            await status_msg.edit_text(
                card_text,
                parse_mode="Markdown",
                reply_markup=get_confirmation_keyboard(
                    payer_type=eff_settings.default_payer_type,
                    cargo_type=eff_settings.default_cargo_type,
                    declared_value=declared_val,
                    cod_amount=cod_val,
                    cod_payment_type=cod_type,
                    session_id=session_id,
                ),
            )
        except Exception as e:
            logger.error(f"Error processing text message: {e}", exc_info=True)
            await status_msg.edit_text(f"❌ *Сталася помилка:* {str(e)}", parse_mode="Markdown")

    async def _process_user_accumulated_messages(user_id: int):
        """Wait for rapid forwarded messages to accumulate before parsing."""
        try:
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return

        buffered_texts = USER_MESSAGE_BUFFERS.pop(user_id, [])
        last_message = USER_LAST_MESSAGES.pop(user_id, None)

        if not buffered_texts or not last_message:
            return

        combined_text = "\n".join(buffered_texts)
        await _handle_combined_text_message(last_message, combined_text)

    @router.message(F.text)
    async def process_text_message(message: Message):
        """Handle raw user text with debouncing for rapid forwarded messages."""
        text = message.text.strip()
        if text.startswith("/"):
            return

        user_id = message.from_user.id
        if user_id not in USER_MESSAGE_BUFFERS:
            USER_MESSAGE_BUFFERS[user_id] = []

        USER_MESSAGE_BUFFERS[user_id].append(text)
        USER_LAST_MESSAGES[user_id] = message

        # Cancel existing pending debounce task if running, restart 1.0s timer
        if user_id in USER_DEBOUNCE_TASKS and not USER_DEBOUNCE_TASKS[user_id].done():
            USER_DEBOUNCE_TASKS[user_id].cancel()

        USER_DEBOUNCE_TASKS[user_id] = asyncio.create_task(
            _process_user_accumulated_messages(user_id)
        )

    @router.callback_query(WaybillActionCallback.filter())
    async def process_waybill_callback(
        callback: CallbackQuery, callback_data: WaybillActionCallback
    ):
        """Handle inline keyboard buttons for waybill creation."""
        session_id = callback_data.session_id
        session = PENDING_SESSIONS.get(session_id)

        if not session:
            await callback.answer(
                "Сесію завершено. Надішліть реквізити отримувача знову.", show_alert=True
            )
            return

        action = callback_data.action
        user_id = session["user_id"]

        if action == "cancel":
            PENDING_SESSIONS.pop(session_id, None)
            USER_ACTIVE_SESSIONS.pop(user_id, None)
            await callback.message.edit_text("❌ *Створення накладної скасовано.*", parse_mode="Markdown")
            await callback.answer()
            return

        if action == "toggle_payer":
            new_payer = "Sender" if callback_data.payer_type == "Recipient" else "Recipient"
            session["payer_type"] = new_payer
            await callback.message.edit_reply_markup(
                reply_markup=get_confirmation_keyboard(
                    payer_type=new_payer,
                    cargo_type=session["cargo_type"],
                    declared_value=session["declared_value"],
                    session_id=session_id,
                )
            )
            payer_ua = "Отримувач" if new_payer == "Recipient" else "Відправник"
            await callback.answer(f"Платника змінено на: {payer_ua}")
            return

        if action == "toggle_cargo":
            new_cargo = "Documents" if callback_data.cargo_type == "Parcel" else "Parcel"
            session["cargo_type"] = new_cargo
            await callback.message.edit_reply_markup(
                reply_markup=get_confirmation_keyboard(
                    payer_type=session["payer_type"],
                    cargo_type=new_cargo,
                    declared_value=session["declared_value"],
                    session_id=session_id,
                )
            )
            cargo_ua = "Посилка" if new_cargo == "Parcel" else "Документи"
            await callback.answer(f"Тип вантажу змінено на: {cargo_ua}")
            return

        if action == "cycle_value":
            current_val = session["declared_value"]
            try:
                curr_idx = VALUE_OPTIONS.index(current_val)
                next_val = VALUE_OPTIONS[(curr_idx + 1) % len(VALUE_OPTIONS)]
            except ValueError:
                next_val = VALUE_OPTIONS[0]

            session["declared_value"] = next_val

            parsed_info = session["parsed_info"]
            city = session["city"]
            warehouse = session["warehouse"]
            cargo_desc = session["cargo_description"]
            cod_val = session.get("cod_amount", 0.0)
            cod_type = session.get("cod_payment_type", "cash")
            cod_str = "❌ Немає" if cod_val <= 0 else f"{int(cod_val)} грн ({'Картка' if cod_type == 'card' else 'Готівка'})"

            card_text = (
                "📋 *Розпарсені дані отримувача для перевірки:*\n\n"
                f"👤 *Отримувач:* {parsed_info.full_name}\n"
                f"📞 *Телефон:* `{parsed_info.phone}`\n"
                f"🏙 *Місто:* {city.description}\n"
                f"📦 *Пункт призначення:* {warehouse.description}\n"
                f"📝 *Опис вантажу:* {cargo_desc}\n"
                f"💰 *Оціночна вартість:* {int(next_val)} грн (Мін. 500 грн)\n"
                f"💵 *Накладений платіж:* {cod_str}\n\n"
                "Перевірте дані та оберіть дію нижче:"
            )

            await callback.message.edit_text(
                card_text,
                parse_mode="Markdown",
                reply_markup=get_confirmation_keyboard(
                    payer_type=session["payer_type"],
                    cargo_type=session["cargo_type"],
                    declared_value=next_val,
                    cod_amount=cod_val,
                    cod_payment_type=cod_type,
                    session_id=session_id,
                ),
            )
            await callback.answer(f"Оціночну вартість встановлено: {int(next_val)} грн")
            return

        if action == "cycle_cod":
            COD_OPTIONS = [0.0, 500.0, 1000.0, 1500.0, 2000.0, 3000.0]
            current_cod = session.get("cod_amount", 0.0)
            try:
                curr_idx = COD_OPTIONS.index(current_cod)
                next_cod = COD_OPTIONS[(curr_idx + 1) % len(COD_OPTIONS)]
            except ValueError:
                next_cod = COD_OPTIONS[1]

            session["cod_amount"] = next_cod

            parsed_info = session["parsed_info"]
            city = session["city"]
            warehouse = session["warehouse"]
            cargo_desc = session["cargo_description"]
            declared_val = session["declared_value"]
            cod_type = session.get("cod_payment_type", "cash")
            cod_str = "❌ Немає" if next_cod <= 0 else f"{int(next_cod)} грн ({'Картка' if cod_type == 'card' else 'Готівка'})"

            card_text = (
                "📋 *Розпарсені дані отримувача для перевірки:*\n\n"
                f"👤 *Отримувач:* {parsed_info.full_name}\n"
                f"📞 *Телефон:* `{parsed_info.phone}`\n"
                f"🏙 *Місто:* {city.description}\n"
                f"📦 *Пункт призначення:* {warehouse.description}\n"
                f"📝 *Опис вантажу:* {cargo_desc}\n"
                f"💰 *Оціночна вартість:* {int(declared_val)} грн (Мін. 500 грн)\n"
                f"💵 *Накладений платіж:* {cod_str}\n\n"
                "Перевірте дані та оберіть дію нижче:"
            )

            await callback.message.edit_text(
                card_text,
                parse_mode="Markdown",
                reply_markup=get_confirmation_keyboard(
                    payer_type=session["payer_type"],
                    cargo_type=session["cargo_type"],
                    declared_value=declared_val,
                    cod_amount=next_cod,
                    cod_payment_type=cod_type,
                    session_id=session_id,
                ),
            )
            msg_str = "Скасовано" if next_cod <= 0 else f"{int(next_cod)} грн"
            await callback.answer(f"Накладений платіж: {msg_str}")
            return

        if action == "confirm":
            editing_ref = session.get("editing_draft_ref")
            action_title = "Оновлення" if editing_ref else "Генерація"
            await callback.answer(f"{action_title} express-накладної...")
            await callback.message.edit_text(
                f"⏳ *Реєстрація отримувача та {action_title.lower()} express-накладної у базі Нової Пошти...*",
                parse_mode="Markdown",
            )

            eff_settings = storage_manager.get_effective_settings(user_id, settings)
            user_np_client = NovaPoshtaClient(eff_settings)

            parsed_info = session["parsed_info"]
            city = session["city"]
            warehouse = session["warehouse"]
            payer_type = session["payer_type"]
            declared_value = session["declared_value"]
            cargo_desc = session["cargo_description"]
            cod_amount = session.get("cod_amount", 0.0)
            cod_payment_type = session.get("cod_payment_type", "cash")

            try:
                # Create recipient counterparty
                recipient_res = await user_np_client.create_recipient_counterparty(
                    first_name=parsed_info.first_name or "",
                    last_name=parsed_info.last_name or "",
                    middle_name=parsed_info.middle_name or "",
                    phone=parsed_info.phone or "",
                )

                # Create or Update Waybill
                if editing_ref:
                    wb_res = await user_np_client.update_waybill(
                        document_ref=editing_ref,
                        recipient_cp_ref=recipient_res.counterparty_ref,
                        recipient_contact_ref=recipient_res.contact_person_ref,
                        recipient_phone=parsed_info.phone or "",
                        recipient_city_ref=city.ref,
                        recipient_warehouse_ref=warehouse.ref,
                        payer_type=payer_type,
                        description=cargo_desc,
                        seats_amount=eff_settings.default_seats_amount,
                        weight=eff_settings.default_weight,
                        declared_value=declared_value,
                        cod_amount=cod_amount,
                    )
                    storage_manager.delete_user_draft(user_id, editing_ref)
                else:
                    wb_res = await user_np_client.create_waybill(
                        recipient_cp_ref=recipient_res.counterparty_ref,
                        recipient_contact_ref=recipient_res.contact_person_ref,
                        recipient_phone=parsed_info.phone or "",
                        recipient_city_ref=city.ref,
                        recipient_warehouse_ref=warehouse.ref,
                        payer_type=payer_type,
                        description=cargo_desc,
                        seats_amount=eff_settings.default_seats_amount,
                        weight=eff_settings.default_weight,
                        declared_value=declared_value,
                        cod_amount=cod_amount,
                    )

                PENDING_SESSIONS.pop(session_id, None)
                USER_ACTIVE_SESSIONS.pop(user_id, None)

                # Save created/updated draft locally for user
                draft_item = SavedDraft(
                    ref=wb_res.ref,
                    int_doc_number=wb_res.int_doc_number,
                    recipient_name=parsed_info.full_name,
                    recipient_phone=parsed_info.phone or "",
                    city_description=city.description,
                    warehouse_description=warehouse.description,
                    payer_type=payer_type,
                    cargo_description=cargo_desc,
                    declared_value=declared_value,
                    cod_amount=cod_amount,
                    cod_payment_type=cod_payment_type,
                    cost=wb_res.cost,
                    created_at=datetime.date.today().strftime("%d.%m.%Y"),
                )
                storage_manager.add_user_draft(user_id, draft_item)

                tracking_url = (
                    f"https://novaposhta.ua/tracking/?cargo_number={wb_res.int_doc_number}"
                )
                payer_ua = "Отримувач" if payer_type == "Recipient" else "Відправник"
                success_title = "одно успішно оновлено" if editing_ref else "о успішно створено"
                cod_str = "❌ Немає" if not cod_amount or cod_amount <= 0 else f"{int(cod_amount)} грн"

                success_card = (
                    f"✅ *Express-накладну{success_title}!*\n\n"
                    f"🎫 *Номер ТТН:* `{wb_res.int_doc_number}`\n"
                    f"👤 *Отримувач:* {parsed_info.full_name}\n"
                    f"📞 *Телефон:* `{parsed_info.phone}`\n"
                    f"🏙 *Місто:* {city.description}\n"
                    f"📦 *Пункт призначення:* {warehouse.description}\n"
                    f"📝 *Опис вантажу:* {cargo_desc}\n"
                    f"💳 *Платник:* {payer_ua}\n"
                    f"💰 *Доставка:* ~{wb_res.cost} грн | *Оцінка:* {int(declared_value)} грн\n"
                    f"💵 *Накладений платіж:* {cod_str}\n"
                    f"📅 *Очікувана дата доставки:* {wb_res.estimated_delivery_date or 'Не вказано'}\n\n"
                    f"🔗 [Відстежити ТТН на сайті Нової Пошти]({tracking_url})"
                )

                await callback.message.edit_text(
                    success_card,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                    reply_markup=get_draft_keyboard(ref=wb_res.ref),
                )
            except Exception as err:
                logger.error(f"Failed to create/update waybill: {err}", exc_info=True)
                await callback.message.edit_text(
                    f"❌ *Помилка формування ТТН:* {str(err)}", parse_mode="Markdown"
                )

    @router.callback_query(DraftActionCallback.filter())
    async def process_draft_callback(
        callback: CallbackQuery, callback_data: DraftActionCallback
    ):
        """Handle inline actions on saved waybill drafts."""
        user_id = callback.from_user.id
        eff_settings = storage_manager.get_effective_settings(user_id, settings)
        user_np_client = NovaPoshtaClient(eff_settings)

        action = callback_data.action
        ref = callback_data.ref

        if action == "edit":
            await callback.answer("Завантаження чернетки для редагування...")
            drafts = storage_manager.get_user_drafts(user_id)
            target_draft = next((d for d in drafts if d.ref == ref), None)

            if not target_draft:
                await callback.message.edit_text(
                    "⚠️ *Чернетку не знайдено.*", parse_mode="Markdown"
                )
                return

            user_ai_extractor = AIExtractor(eff_settings)
            draft_prompt = (
                f"{target_draft.recipient_name} {target_draft.recipient_phone} "
                f"{target_draft.city_description} {target_draft.warehouse_description}. "
                f"Опис вантажу: {target_draft.cargo_description}. Оціночна вартість: {target_draft.declared_value}"
            )

            # Use AI to parse the stored draft text into structured ParsedRecipientInfo
            parsed_info = await user_ai_extractor.parse_text(draft_prompt)

            city = None
            warehouse = None
            if parsed_info.city_name:
                cities = await user_np_client.search_city(parsed_info.city_name)
                if cities:
                    city = cities[0]
                    if parsed_info.warehouse_number:
                        warehouse = await user_np_client.get_warehouse(
                            city_ref=city.ref,
                            warehouse_number=parsed_info.warehouse_number,
                            is_postomat=parsed_info.is_postomat,
                        )

            # Activate editing session
            session_id = str(uuid.uuid4())[:8]
            PENDING_SESSIONS[session_id] = {
                "parsed_info": parsed_info,
                "city": city,
                "warehouse": warehouse,
                "payer_type": target_draft.payer_type,
                "cargo_type": "Parcel",
                "declared_value": target_draft.declared_value,
                "cargo_description": target_draft.cargo_description,
                "user_id": user_id,
                "editing_draft_ref": ref,
            }
            USER_ACTIVE_SESSIONS[user_id] = session_id

            card_text = (
                f"✏️ *Редагування ТТН № `{target_draft.int_doc_number}`*\n\n"
                f"👤 *Отримувач:* {target_draft.recipient_name}\n"
                f"📞 *Телефон:* `{target_draft.recipient_phone}`\n"
                f"🏙 *Місто:* {target_draft.city_description}\n"
                f"📦 *Пункт призначення:* {target_draft.warehouse_description}\n"
                f"📝 *Опис вантажу:* {target_draft.cargo_description}\n"
                f"💰 *Оціночна вартість:* {int(target_draft.declared_value)} грн\n\n"
                "💡 *Надішліть будь-які зміни живим текстом* (наприклад: *'зміни опис на сувенір'*, *'оцінка 2000 грн'*), "
                "або скористайтеся кнопками нижче:"
            )

            await callback.message.edit_text(
                card_text,
                parse_mode="Markdown",
                reply_markup=get_confirmation_keyboard(
                    payer_type=target_draft.payer_type,
                    cargo_type="Parcel",
                    declared_value=target_draft.declared_value,
                    session_id=session_id,
                ),
            )
            return

        if action == "delete":
            await callback.answer("Видалення накладної з Нової Пошти...")
            try:
                success = await user_np_client.delete_waybill(ref)
                storage_manager.delete_user_draft(user_id, ref)

                if success:
                    await callback.message.edit_text(
                        "🗑 *Express-накладну (ТТН) успішно видалено з бази Нової Пошти!*",
                        parse_mode="Markdown",
                    )
                else:
                    await callback.message.edit_text(
                        "⚠️ *Не вдалося видалити ТТН з Нової Пошти (можливо, її вже скасовано або оброблено).*",
                        parse_mode="Markdown",
                    )
            except Exception as e:
                logger.error(f"Error deleting waybill: {e}", exc_info=True)
                await callback.answer(f"Помилка видалення: {str(e)}", show_alert=True)

    @router.callback_query(CitySelectCallback.filter())
    async def process_city_select_callback(
        callback: CallbackQuery, callback_data: CitySelectCallback
    ):
        """Handle user selection of a city when multiple candidates match."""
        session_id = callback_data.session_id
        session = PENDING_SESSIONS.get(session_id)

        if not session or "candidates" not in session:
            await callback.answer("Сесія застаріла. Надішліть реквізити заново.", show_alert=True)
            return

        user_id = callback.from_user.id
        eff_settings = storage_manager.get_effective_settings(user_id, settings)

        candidates = session["candidates"]
        target_ref = callback_data.city_ref

        selected = next((pair for pair in candidates if pair[0].ref == target_ref), None)
        if not selected:
            await callback.answer("Населений пункт не знайдено.", show_alert=True)
            return

        matched_city, warehouse = selected
        parsed_info = session["parsed_info"]

        declared_val = max(
            parsed_info.declared_value or eff_settings.default_declared_value,
            500.0,
        )
        cargo_desc = parsed_info.cargo_description or "Посилка"

        session["city"] = matched_city
        session["warehouse"] = warehouse
        session["payer_type"] = eff_settings.default_payer_type
        session["cargo_type"] = eff_settings.default_cargo_type
        session["declared_value"] = declared_val
        session["cargo_description"] = cargo_desc
        session.pop("candidates", None)

        card_text = (
            "📋 *Розпарсені дані отримувача для перевірки:*\n\n"
            f"👤 *Отримувач:* {parsed_info.full_name}\n"
            f"📞 *Телефон:* `{parsed_info.phone}`\n"
            f"🏙 *Місто:* {matched_city.description}\n"
            f"📦 *Пункт призначення:* {warehouse.description}\n"
            f"📝 *Опис вантажу:* {cargo_desc}\n"
            f"💰 *Оціночна вартість:* {int(declared_val)} грн (Мін. 500 грн)\n\n"
            "Перевірте дані та оберіть дію нижче:"
        )

        await callback.message.edit_text(
            card_text,
            parse_mode="Markdown",
            reply_markup=get_confirmation_keyboard(
                payer_type=eff_settings.default_payer_type,
                cargo_type=eff_settings.default_cargo_type,
                declared_value=declared_val,
                session_id=session_id,
            ),
        )
        await callback.answer(f"Обрано: {matched_city.description}")

    @router.callback_query(RegisterActionCallback.filter())
    async def process_register_callback(
        callback: CallbackQuery, callback_data: RegisterActionCallback
    ):
        """Handle inline actions on ScanSheet registers."""
        user_id = callback.from_user.id
        eff_settings = storage_manager.get_effective_settings(user_id, settings)
        user_np_client = NovaPoshtaClient(eff_settings)

        action = callback_data.action
        ref = callback_data.ref

        if action == "barcode":
            saved_sheets = storage_manager.get_user_scansheets(user_id)
            target = next((s for s in saved_sheets if s.ref == ref), None)
            reg_num = target.number if target else ref

            await callback.answer("Генерація штрихкоду...")
            try:
                barcode_bytes = generate_code128_barcode(reg_num)
                photo_file = BufferedInputFile(barcode_bytes, filename=f"barcode_{reg_num}.png")
                await callback.message.answer_photo(
                    photo=photo_file,
                    caption=f"📱 *Штрихкод реєстру № `{reg_num}`*",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error(f"Error generating barcode photo: {e}", exc_info=True)
                await callback.answer(f"Не вдалося згенерувати штрихкод: {e}", show_alert=True)
            return

        if action == "delete":
            await callback.answer("Видалення реєстру...")
            try:
                await user_np_client.delete_scan_sheet(ref)
                storage_manager.delete_user_scansheet(user_id, ref)
                await callback.message.edit_text(
                    "🗑 *Реєстр (ScanSheet) успішно видалено / розформовано!*",
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error(f"Error deleting scan sheet: {e}", exc_info=True)
                storage_manager.delete_user_scansheet(user_id, ref)
                await callback.message.edit_text(
                    "🗑 *Реєстр видалено з локальної бази.*",
                    parse_mode="Markdown",
                )

    @router.callback_query(AddressConfirmCallback.filter())
    async def process_address_confirm_callback(
        callback: CallbackQuery, callback_data: AddressConfirmCallback
    ):
        """Handle user choice between courier address delivery and warehouse."""
        session_id = callback_data.session_id
        session = PENDING_SESSIONS.get(session_id)

        if not session or "parsed_info" not in session:
            await callback.answer("Сесія застаріла. Надішліть реквізити заново.", show_alert=True)
            return

        choice = callback_data.choice
        parsed_info: ParsedRecipientInfo = session["parsed_info"]
        session["address_choice_made"] = True

        if choice == "courier":
            parsed_info.is_address_delivery = True
            parsed_info.has_address_suspicion = False
            await callback.answer("Обрано адресну доставку кур'єром!")
        else:
            parsed_info.is_address_delivery = False
            parsed_info.has_address_suspicion = False
            await callback.answer("Обрано доставку у відділення / поштомат!")

        await callback.message.edit_text("⏳ *Оновлення способу доставки...*", parse_mode="Markdown")
        await _handle_combined_text_message(callback.message, "")
