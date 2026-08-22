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
    StreetSelectCallback,
    get_street_selection_keyboard,
    RegisterActionCallback,
    get_register_keyboard,
    AddressConfirmCallback,
    get_address_confirmation_keyboard,
    get_waybill_keyboard,
)

logger = logging.getLogger(__name__)
router = Router()

# In-memory storage for active pending waybill verification sessions
PENDING_SESSIONS: Dict[str, Dict[str, Any]] = {}
USER_ACTIVE_SESSIONS: Dict[int, str] = {}  # user_id -> active session_id
USER_LAST_PARSED_INFO: Dict[int, ParsedRecipientInfo] = {}  # user_id -> last parsed recipient info for natural language follow-up edits
VALUE_OPTIONS = [500.0, 1000.0, 2000.0, 5000.0, 10000.0]
SESSION_TIMEOUT_SECONDS: float = 15 * 60  # 15 minutes TTL for active parcel creation sessions

# Debouncer buffers for multi-part forwarded messages
USER_MESSAGE_BUFFERS: Dict[int, List[str]] = {}
USER_DEBOUNCE_TASKS: Dict[int, asyncio.Task] = {}
USER_LAST_MESSAGES: Dict[int, Message] = {}


def _cleanup_expired_sessions():
    """Remove expired sessions older than SESSION_TIMEOUT_SECONDS (15 minutes)."""
    now = datetime.datetime.now().timestamp()
    expired_sessions = [
        s_id
        for s_id, s_data in PENDING_SESSIONS.items()
        if now - s_data.get("updated_at", now) > SESSION_TIMEOUT_SECONDS
    ]
    for s_id in expired_sessions:
        s_data = PENDING_SESSIONS.pop(s_id, None)
        if s_data:
            u_id = s_data.get("user_id")
            if u_id and USER_ACTIVE_SESSIONS.get(u_id) == s_id:
                USER_ACTIVE_SESSIONS.pop(u_id, None)
                USER_LAST_PARSED_INFO.pop(u_id, None)


def get_user_active_session_id(user_id: int) -> Optional[str]:
    """Get active session ID for user if not expired and refresh timestamp."""
    _cleanup_expired_sessions()
    session_id = USER_ACTIVE_SESSIONS.get(user_id)
    if not session_id or session_id not in PENDING_SESSIONS:
        return None
    PENDING_SESSIONS[session_id]["updated_at"] = datetime.datetime.now().timestamp()
    return session_id


def clear_user_active_session(user_id: int):
    """Clear active waybill session and recent parsed info for a given user."""
    session_id = USER_ACTIVE_SESSIONS.pop(user_id, None)
    if session_id:
        PENDING_SESSIONS.pop(session_id, None)
    USER_LAST_PARSED_INFO.pop(user_id, None)


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


async def fetch_user_active_drafts(
    user_id: int,
    user_np_client: NovaPoshtaClient,
    storage_manager: UserSettingsManager,
) -> List[Dict[str, Any]]:
    """Fetch user's active un-shipped express waybill drafts from Nova Poshta API and local storage."""
    np_live_drafts = await user_np_client.get_internet_document_list(days_back=30)
    local_drafts = storage_manager.get_user_drafts(user_id)

    # Collect all candidate document numbers
    all_doc_numbers = list({
        d.int_doc_number for d in local_drafts if d.int_doc_number
    } | {
        item.int_doc_number for item in np_live_drafts if item.int_doc_number
    })

    # Check tracking statuses in batch
    statuses = {}
    if all_doc_numbers:
        try:
            statuses = await user_np_client.get_documents_status(all_doc_numbers)
        except Exception as e:
            logger.error(f"Error checking tracking statuses for drafts: {e}")

    # Purge local drafts that are not active drafts (physically shipped, refused, returned or deleted)
    purge_local_ids = [
        d_num for d_num in all_doc_numbers
        if statuses.get(d_num) and not statuses.get(d_num, {}).get("is_draft")
    ]
    if purge_local_ids:
        storage_manager.purge_sent_drafts(user_id, purge_local_ids)
        local_drafts = storage_manager.get_user_drafts(user_id)

    # Build combined strictly active un-shipped drafts map
    combined_drafts_map = {}
    for d in local_drafts:
        st = statuses.get(d.int_doc_number)
        if st and not st.get("is_draft"):
            continue

        is_lr = (
            getattr(d, "is_light_return", False)
            or (st.get("is_light_return", False) if st else False)
            or "легке повернення" in d.cargo_description.lower()
        )

        combined_drafts_map[d.int_doc_number] = {
            "ref": d.ref,
            "int_doc_number": d.int_doc_number,
            "recipient_name": d.recipient_name,
            "recipient_phone": d.recipient_phone,
            "city_description": d.city_description,
            "warehouse_description": d.warehouse_description,
            "cargo_description": d.cargo_description,
            "payer_type": d.payer_type,
            "declared_value": d.declared_value,
            "cod_amount": getattr(d, "cod_amount", 0.0) or 0.0,
            "cod_payment_type": getattr(d, "cod_payment_type", "cash"),
            "cost": d.cost,
            "created_at": d.created_at,
            "is_light_return": is_lr,
        }

    for live_item in np_live_drafts:
        doc_num = live_item.int_doc_number
        st = statuses.get(doc_num)
        if st and not st.get("is_draft"):
            continue

        is_lr = (
            getattr(live_item, "is_light_return", False)
            or (st.get("is_light_return", False) if st else False)
            or "легке повернення" in (live_item.description or "").lower()
        )

        if doc_num not in combined_drafts_map:
            combined_drafts_map[doc_num] = {
                "ref": live_item.ref or live_item.int_doc_number,
                "int_doc_number": live_item.int_doc_number,
                "recipient_name": live_item.recipient_name,
                "recipient_phone": live_item.recipient_phone or "Не вказано",
                "city_description": live_item.city_recipient or "Не вказано",
                "warehouse_description": live_item.address_recipient or "Накладна на сайті",
                "cargo_description": live_item.description or "Посилка",
                "payer_type": live_item.payer_type or "Recipient",
                "declared_value": live_item.declared_value,
                "cod_amount": live_item.cod_amount,
                "cod_payment_type": live_item.cod_payment_type,
                "cost": live_item.cost,
                "created_at": live_item.date_created or "Нещодавно",
                "is_light_return": is_lr,
            }

    return list(combined_drafts_map.values())


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

    async def ensure_user_configured(message: Message, user_id: Optional[int] = None) -> bool:
        """Check if user has configured personal NP API key and AI API key."""
        target_user_id = user_id if user_id is not None else message.from_user.id
        u_settings = storage_manager.get_user_settings(target_user_id)
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

    def _get_destination_desc(session: Dict[str, Any]) -> str:
        """Get human-readable destination description (warehouse or courier home address)."""
        if session.get("destination_description"):
            return str(session["destination_description"])
        wh = session.get("warehouse")
        if wh and hasattr(wh, "description"):
            return str(wh.description)
        if session.get("is_address_delivery"):
            st = session.get("street_name", "")
            bld = session.get("building_number", "")
            flt = session.get("flat_number", "")
            res = f"🏡 Адресна доставка: вул. {st}, буд. {bld}"
            if flt:
                res += f", кв. {flt}"
            return res
        return "Відділення Нової Пошти"

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

    @router.message(Command("drafts"))
    @router.message(F.text == "📝 Мої чернетки (ТТН)")
    async def cmd_drafts(message: Message):
        """Show list of active created express waybill drafts (fetching both local and live NP server drafts)."""
        clear_user_active_session(message.from_user.id)
        if not await ensure_user_configured(message):
            return

        user_id = message.from_user.id
        eff_settings = storage_manager.get_effective_settings(user_id, settings)
        user_np_client = NovaPoshtaClient(eff_settings)

        status_msg = await message.answer(
            "🔍 *Отримання ваших невідправлених чернеток з баз Нової Пошти...*", parse_mode="Markdown"
        )

        try:
            all_drafts = await fetch_user_active_drafts(user_id, user_np_client, storage_manager)

            if not all_drafts:
                await status_msg.edit_text(
                    "📝 *Активних чернеток ТТН (невідправлених) не знайдено.*\n"
                    "Усі ваші створені накладні вже відправлені або знаходяться в дорозі. "
                    "Ви можете переглянути їх у розділі 📦 *Активні посилки*!",
                    parse_mode="Markdown",
                )
                return

            try:
                await status_msg.delete()
            except Exception:
                pass

            await message.answer(
                f"📄 *Ваші чернетки ТТН у системі Нової Пошти ({len(all_drafts)}):*", parse_mode="Markdown"
            )

            for item in all_drafts[:15]:
                doc_num = item["int_doc_number"]
                tracking_url = f"https://novaposhta.ua/tracking/?cargo_number={doc_num}"
                payer_ua = "Отримувач" if item.get("payer_type") == "Recipient" else "Відправник"
                declared_val = item.get("declared_value", 500.0)
                cod_val = item.get("cod_amount", 0.0) or 0.0
                cod_type = item.get("cod_payment_type", "cash")

                if cod_val > 0:
                    payout_ua = "Картка" if cod_type == "card" else "Готівка"
                    cod_line = f"💵 *Накладений платіж:* {int(cod_val)} грн ({payout_ua})\n"
                else:
                    cod_line = ""

                light_return_line = (
                    "\n🔄 *Легке повернення*"
                    if item.get("is_light_return")
                    or "легке повернення" in item.get("cargo_description", "").lower()
                    else ""
                )

                card = (
                    f"🎫 *ТТН:* `{doc_num}`{light_return_line}\n"
                    f"👤 *Отримувач:* {item['recipient_name']}\n"
                    f"📞 *Телефон:* `{item['recipient_phone']}`\n"
                    f"🏙 *Місто:* {item['city_description']}\n"
                    f"📦 *Пункт призначення:* {item['warehouse_description']}\n"
                    f"📝 *Опис:* {item['cargo_description']}\n"
                    f"💳 *Платник:* {payer_ua} | 💰 *Оцінка:* {int(declared_val)} грн\n"
                    f"{cod_line}"
                    f"📅 *Створено:* {item['created_at']}\n\n"
                    f"🔗 [Відстежити ТТН на сайті Нової Пошти]({tracking_url})"
                )
                await message.answer(
                    card,
                    parse_mode="Markdown",
                    reply_markup=get_draft_keyboard(ref=item["ref"]),
                    disable_web_page_preview=True,
                )
        except Exception as e:
            logger.error(f"Error in cmd_drafts: {e}", exc_info=True)
            clean_err = str(e).replace("*", "").replace("_", "").replace("`", "").replace("'", "")
            await status_msg.edit_text(f"❌ *Помилка отримання чернеток:* {clean_err}", parse_mode="Markdown")

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
                light_return_line = (
                    "\n🔄 *Легке повернення*"
                    if getattr(item, "is_light_return", False)
                    or "легке повернення" in (item.description or "").lower()
                    else ""
                )
                card = (
                    f"📤 *Вихідна ТТН №{idx}:* `{item.int_doc_number}`{light_return_line}\n"
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
                    reply_markup=get_waybill_keyboard(doc_number=item.int_doc_number),
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
                light_return_line = (
                    "\n🔄 *Легке повернення*"
                    if getattr(item, "is_light_return", False)
                    or "легке повернення" in (item.description or "").lower()
                    else ""
                )
                card = (
                    f"📥 *Вхідна ТТН №{idx}:* `{item.int_doc_number}`{light_return_line}\n"
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
                    reply_markup=get_waybill_keyboard(doc_number=item.int_doc_number),
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

    async def _handle_combined_text_message(
        message: Message, text: str, user_id: Optional[int] = None
    ):
        """Core text processing logic for accumulated recipient messages."""
        actual_user_id = user_id if user_id is not None else message.from_user.id
        if not await ensure_user_configured(message, user_id=actual_user_id):
            return

        eff_settings = storage_manager.get_effective_settings(actual_user_id, settings)
        user_ai_extractor = AIExtractor(eff_settings)
        user_np_client = NovaPoshtaClient(eff_settings)

        _cleanup_expired_sessions()
        active_session_id = get_user_active_session_id(actual_user_id)
        prev_parsed_info = None
        if active_session_id and active_session_id in PENDING_SESSIONS:
            prev_parsed_info = PENDING_SESSIONS[active_session_id].get("parsed_info")
        if not prev_parsed_info:
            prev_parsed_info = USER_LAST_PARSED_INFO.get(actual_user_id)

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
                    try:
                        await status_msg.delete()
                    except Exception:
                        pass
                    await cmd_registers(message)
                    return

                await status_msg.edit_text(
                    "🔍 *Отримання ваших активних чернеток та підбір накладних через AI...*",
                    parse_mode="Markdown",
                )

                active_drafts = await fetch_user_active_drafts(actual_user_id, user_np_client, storage_manager)

                if not active_drafts:
                    await status_msg.edit_text(
                        "📝 *Активних чернеток ТТН (невідправлених) не знайдено.*\n"
                        "Усі ваші накладні вже відправлені або ще не створені. "
                        "Неможливо сформувати реєстр без накладних.",
                        parse_mode="Markdown",
                    )
                    return

                ai_reg_result = await user_ai_extractor.filter_drafts_for_register(
                    user_prompt=text, drafts=active_drafts
                )

                if ai_reg_result.action == "list_registers":
                    try:
                        await status_msg.delete()
                    except Exception:
                        pass
                    await cmd_registers(message)
                    return

                selected_nums_set = set(ai_reg_result.selected_doc_numbers)
                matched_drafts = [
                    d for d in active_drafts if str(d.get("int_doc_number")) in selected_nums_set
                ]

                if not matched_drafts or ai_reg_result.action == "not_found":
                    explanation_part = f"\n💡 _{ai_reg_result.explanation}_" if ai_reg_result.explanation else ""
                    await status_msg.edit_text(
                        f"🔍 *За вашим запитом не знайдено відповідних накладних серед ваших чернеток.*{explanation_part}",
                        parse_mode="Markdown",
                    )
                    return

                if ai_reg_result.action == "create":
                    summary_title = ai_reg_result.summary or f"{len(matched_drafts)} накладних"
                    await status_msg.edit_text(
                        f"⏳ *Формування реєстру (ScanSheet) для {summary_title}...*",
                        parse_mode="Markdown",
                    )
                    doc_refs = [d["ref"] for d in matched_drafts]
                    doc_nums = [d["int_doc_number"] for d in matched_drafts]

                    scansheet_info = await user_np_client.create_scan_sheet(doc_refs)

                    saved_scansheet = SavedScanSheet(
                        ref=scansheet_info.ref,
                        number=scansheet_info.number,
                        date_created=scansheet_info.date_created,
                        count_of_documents=scansheet_info.count_of_documents,
                        document_numbers=doc_nums,
                    )
                    storage_manager.add_user_scansheet(actual_user_id, saved_scansheet)

                    barcode_bytes = generate_code128_barcode(scansheet_info.number)
                    photo_file = BufferedInputFile(barcode_bytes, filename=f"scansheet_{scansheet_info.number}.png")

                    # Construct detailed list of included TTNs
                    details_lines = []
                    for idx, d in enumerate(matched_drafts, 1):
                        cod_str = f" | 💵 Наложка: {int(d.get('cod_amount', 0))} грн" if d.get("cod_amount") else ""
                        line = (
                            f"*{idx}. ТТН:* `{d['int_doc_number']}` | {d['recipient_name']}\n"
                            f"   🏙 {d['city_description']}, {d['warehouse_description']}\n"
                            f"   📝 {d['cargo_description']} | 💰 {int(d.get('declared_value', 500))} грн{cod_str}"
                        )
                        details_lines.append(line)
                    details_block = "\n\n".join(details_lines)

                    try:
                        await status_msg.delete()
                    except Exception:
                        pass

                    caption_text = (
                        f"✅ *Реєстр (ScanSheet) успішно створено!*\n\n"
                        f"📋 *Номер реєстру:* `{scansheet_info.number}`\n"
                        f"📅 *Дата створення:* {scansheet_info.date_created}\n"
                        f"📦 *Кількість накладних:* {scansheet_info.count_of_documents}\n\n"
                        f"📄 *Накладні у реєстрі:*\n{details_block}\n\n"
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
                summary_title = ai_reg_result.summary or f"{len(matched_drafts)} накладних"
                await status_msg.edit_text(
                    f"📋 *Знайдено {summary_title}:*",
                    parse_mode="Markdown",
                )
                for idx, draft in enumerate(matched_drafts, 1):
                    doc_num = draft["int_doc_number"]
                    tracking_url = f"https://novaposhta.ua/tracking/?cargo_number={doc_num}"
                    payer_ua = "Отримувач" if draft.get("payer_type") == "Recipient" else "Відправник"
                    declared_val = draft.get("declared_value", 500.0)
                    cod_val = draft.get("cod_amount", 0.0) or 0.0
                    cod_type = draft.get("cod_payment_type", "cash")

                    if cod_val > 0:
                        payout_ua = "Картка" if cod_type == "card" else "Готівка"
                        cod_line = f"💵 *Накладений платіж:* {int(cod_val)} грн ({payout_ua})\n"
                    else:
                        cod_line = ""

                    light_return_line = (
                        "\n🔄 *Легке повернення*"
                        if draft.get("is_light_return")
                        or "легке повернення" in draft.get("cargo_description", "").lower()
                        else ""
                    )

                    card = (
                        f"🎫 *{idx}. ТТН:* `{doc_num}`{light_return_line}\n"
                        f"👤 *Отримувач:* {draft['recipient_name']}\n"
                        f"📞 *Телефон:* `{draft['recipient_phone']}`\n"
                        f"🏙 *Місто:* {draft['city_description']}\n"
                        f"📦 *Пункт призначення:* {draft['warehouse_description']}\n"
                        f"📝 *Опис:* {draft['cargo_description']}\n"
                        f"💳 *Платник:* {payer_ua} | 💰 *Оцінка:* {int(declared_val)} грн\n"
                        f"{cod_line}"
                        f"📅 *Створено:* {draft['created_at']}\n\n"
                        f"🔗 [Відстежити ТТН на сайті Нової Пошти]({tracking_url})"
                    )
                    await message.answer(
                        card,
                        parse_mode="Markdown",
                        reply_markup=get_draft_keyboard(ref=draft["ref"]),
                        disable_web_page_preview=True,
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

            USER_LAST_PARSED_INFO[actual_user_id] = parsed_info

            # Get or generate active session ID
            session_id = active_session_id or str(uuid.uuid4())[:8]

            # Check for address delivery suspicion prompt
            existing_session = PENDING_SESSIONS.get(session_id) if session_id in PENDING_SESSIONS else None
            address_choice_made = existing_session.get("address_choice_made", False) if existing_session else False

            if parsed_info.has_address_suspicion and not address_choice_made:
                PENDING_SESSIONS[session_id] = {
                    "parsed_info": parsed_info,
                    "user_id": actual_user_id,
                    "updated_at": datetime.datetime.now().timestamp(),
                }
                USER_ACTIVE_SESSIONS[actual_user_id] = session_id

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

            await _continue_processing_recipient_info(
                message=message,
                user_id=actual_user_id,
                session_id=session_id,
                parsed_info=parsed_info,
                status_msg=status_msg,
                prev_parsed_info=prev_parsed_info,
            )
        except Exception as e:
            logger.error(f"Error processing text message: {e}", exc_info=True)
            await status_msg.edit_text(f"❌ *Сталася помилка:* {str(e)}", parse_mode="Markdown")

    async def _continue_processing_recipient_info(
        message: Message,
        user_id: int,
        session_id: str,
        parsed_info: ParsedRecipientInfo,
        status_msg: Message,
        prev_parsed_info: Optional[ParsedRecipientInfo] = None,
    ):
        """Resolve city, warehouse or street address, and display verification confirmation card."""
        eff_settings = storage_manager.get_effective_settings(user_id, settings)
        user_np_client = NovaPoshtaClient(eff_settings)
        now_ts = datetime.datetime.now().timestamp()

        existing_session = PENDING_SESSIONS.get(session_id, {})
        is_address_deliv = bool(parsed_info.is_address_delivery)

        # Check missing required fields
        missing_fields = []
        if not parsed_info.last_name:
            missing_fields.append("👤 Прізвище та Ім'я отримувача")
        if not parsed_info.phone:
            missing_fields.append("📞 Номер телефону отримувача")
        if not parsed_info.city_name:
            missing_fields.append("🏙 Місто / Населений пункт")

        if is_address_deliv:
            if not parsed_info.street_name:
                missing_fields.append("🏡 Вулиця для адресної доставки")
            if not parsed_info.building_number:
                missing_fields.append("🔢 Номер будинку")
        else:
            if not parsed_info.warehouse_number:
                missing_fields.append("📦 Номер відділення або поштомату")

        if missing_fields:
            PENDING_SESSIONS[session_id] = {
                "parsed_info": parsed_info,
                "user_id": user_id,
                "is_address_delivery": is_address_deliv,
                "updated_at": now_ts,
            }
            USER_ACTIVE_SESSIONS[user_id] = session_id

            missing_str = "\n".join([f"• {field}" for field in missing_fields])
            known_name = parsed_info.full_name or "Не вказано"
            known_phone = parsed_info.phone or "Не вказано"
            known_city = parsed_info.city_name or "Не вказано"
            if is_address_deliv:
                addr_p = [p for p in [parsed_info.street_name, f"буд. {parsed_info.building_number}" if parsed_info.building_number else ""] if p]
                known_dest = f"🏡 Кур'єр: {', '.join(addr_p)}" if addr_p else "Не вказано"
            else:
                known_dest = f"{'Поштомат' if parsed_info.is_postomat else 'Відділення'} № {parsed_info.warehouse_number}" if parsed_info.warehouse_number else "Не вказано"

            await status_msg.edit_text(
                "⏳ *Отримано часткові реквізити отримувача (Очікую решту даних...)*\n\n"
                "📋 *Вже збережено:* \n"
                f"• ПІБ: `{known_name}`\n"
                f"• Телефон: `{known_phone}`\n"
                f"• Місто: `{known_city}`\n"
                f"• Пункт: `{known_dest}`\n\n"
                "⚠️ *Очікую доповнення:* \n"
                f"{missing_str}\n\n"
                "💡 *Надішліть або перепостіть наступне повідомлення з рештою даних!*",
                parse_mode="Markdown",
            )
            return

        # Check if existing session already has resolved city and destination, and user didn't change them
        existing_city = existing_session.get("city")
        existing_wh = existing_session.get("warehouse")
        existing_street_ref = existing_session.get("street_ref")
        existing_is_addr = existing_session.get("is_address_delivery", False)

        def _is_city_matched(p_city: Optional[str], e_city: Optional[str]) -> bool:
            if not p_city or not e_city:
                return False
            p_clean = p_city.strip().lower()
            e_clean = e_city.strip().lower()
            return p_clean == e_clean or p_clean in e_clean or e_clean in p_clean

        matched_city = None
        warehouse = None
        dest_desc = ""
        street_ref = ""

        # Check if we can reuse the already chosen city and destination from active session
        can_reuse_destination = False
        if existing_city and _is_city_matched(parsed_info.city_name, existing_city.description):
            if not is_address_deliv and not existing_is_addr and existing_wh:
                if (
                    parsed_info.warehouse_number is not None
                    and (
                        str(parsed_info.warehouse_number) == str(existing_wh.number)
                        or parsed_info.warehouse_number == getattr(existing_wh, "warehouse_number", None)
                    )
                    and bool(parsed_info.is_postomat) == bool(existing_wh.is_postomat)
                ):
                    matched_city = existing_city
                    warehouse = existing_wh
                    dest_desc = existing_session.get("destination_description") or existing_wh.description
                    can_reuse_destination = True
            elif is_address_deliv and existing_is_addr and existing_street_ref:
                matched_city = existing_city
                street_ref = existing_street_ref
                dest_desc = existing_session.get("destination_description") or f"🏡 Адресна доставка: {parsed_info.street_name}, {parsed_info.building_number}"
                can_reuse_destination = True

        if not can_reuse_destination:
            # Lookup City in Nova Poshta
            await status_msg.edit_text(
                "🔍 *Пошук населеного пункту та адреси у базі Нової Пошти...*", parse_mode="Markdown"
            )
            cities = await user_np_client.search_city(parsed_info.city_name)
            if not cities:
                await status_msg.edit_text(
                    f"❌ Населений пункт *'{parsed_info.city_name}'* не знайдено у базі Нової Пошти. Перевірте написання.",
                    parse_mode="Markdown",
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

            matched_city = cities[0]

            if is_address_deliv:
                # Search street in NP database
                street_query = parsed_info.street_name or ""
                streets = await user_np_client.search_street(city_ref=matched_city.ref, street_name=street_query)
                if not streets:
                    await status_msg.edit_text(
                        f"❌ Вулицю *'{street_query}'* у населеному пункті *{matched_city.description}* не знайдено в базі Нової Пошти.\n"
                        "Будь ласка, перевірте правильність назви вулиці або вкажіть номер відділення/поштомату.",
                        parse_mode="Markdown",
                    )
                    return

                if len(streets) == 1:
                    matched_street = streets[0]
                    street_ref = matched_street.ref
                    street_display = f"{matched_street.streets_type} {matched_street.description}"

                    addr_parts = [f"{street_display}, буд. {parsed_info.building_number}"]
                    if parsed_info.flat_number:
                        addr_parts.append(f"кв. {parsed_info.flat_number}")
                    dest_desc = f"🏡 Адресна доставка: {', '.join(addr_parts)}"
                else:
                    # Multiple matching streets/lanes found -> present disambiguation keyboard
                    existing_session = PENDING_SESSIONS.get(session_id, {})
                    payer_type = parsed_info.payer_type or existing_session.get("payer_type") or eff_settings.default_payer_type
                    cargo_type = parsed_info.cargo_type or existing_session.get("cargo_type") or eff_settings.default_cargo_type

                    if parsed_info.cod_amount is not None:
                        cod_val = parsed_info.cod_amount
                    elif "cod_amount" in existing_session:
                        cod_val = existing_session["cod_amount"]
                    else:
                        cod_val = 0.0

                    if parsed_info.cod_payment_type:
                        cod_type = parsed_info.cod_payment_type
                    elif "cod_payment_type" in existing_session:
                        cod_type = existing_session["cod_payment_type"]
                    else:
                        cod_type = "cash"

                    if parsed_info.declared_value is not None:
                        raw_decl = parsed_info.declared_value
                    elif "declared_value" in existing_session:
                        raw_decl = existing_session["declared_value"]
                    else:
                        raw_decl = eff_settings.default_declared_value
                    declared_val = max(raw_decl, 500.0, cod_val)

                    if parsed_info.cargo_description:
                        cargo_desc = parsed_info.cargo_description
                    elif "cargo_description" in existing_session:
                        cargo_desc = existing_session["cargo_description"]
                    else:
                        cargo_desc = "Посилка"

                    session_payload = {
                        "parsed_info": parsed_info,
                        "city": matched_city,
                        "street_candidates": streets,
                        "is_address_delivery": True,
                        "building_number": parsed_info.building_number,
                        "flat_number": parsed_info.flat_number,
                        "payer_type": payer_type,
                        "cargo_type": cargo_type,
                        "declared_value": declared_val,
                        "cargo_description": cargo_desc,
                        "cod_amount": cod_val,
                        "cod_payment_type": cod_type,
                        "user_id": user_id,
                        "updated_at": now_ts,
                    }
                    editing_ref = existing_session.get("editing_draft_ref")
                    if editing_ref:
                        session_payload["editing_draft_ref"] = editing_ref

                    PENDING_SESSIONS[session_id] = session_payload
                    USER_ACTIVE_SESSIONS[user_id] = session_id

                    candidate_lines = [
                        f"⚠️ *Знайдено декілька варіантів вулиці для запиту '{street_query}' у м. {matched_city.description}:*\n"
                    ]
                    for idx, s in enumerate(streets[:8], 1):
                        candidate_lines.append(f"*{idx}.* 🏡 `{s.streets_type} {s.description}`\n")
                    candidate_lines.append("Будь ласка, оберіть потрібну вулицю / провулок нижче:")

                    await status_msg.edit_text(
                        "\n".join(candidate_lines),
                        parse_mode="Markdown",
                        reply_markup=get_street_selection_keyboard(streets, session_id),
                    )
                    return
            else:
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
                        f"❌ {w_type} *№ {parsed_info.warehouse_number}* у населеному пункті *{parsed_info.city_name}* не знайдено.",
                        parse_mode="Markdown",
                    )
                    return

                if len(matching_candidates) == 1:
                    matched_city, warehouse = matching_candidates[0]
                    dest_desc = warehouse.description
                else:
                    # Save candidates in session and present city disambiguation keyboard
                    existing_session = PENDING_SESSIONS.get(session_id, {})
                    session_payload = {
                        "parsed_info": parsed_info,
                        "candidates": matching_candidates,
                        "user_id": user_id,
                        "updated_at": now_ts,
                    }
                    editing_ref = existing_session.get("editing_draft_ref")
                    if editing_ref:
                        session_payload["editing_draft_ref"] = editing_ref
                    for k in ["payer_type", "cargo_type", "declared_value", "cargo_description", "cod_amount", "cod_payment_type"]:
                        if k in existing_session:
                            session_payload[k] = existing_session[k]

                    PENDING_SESSIONS[session_id] = session_payload
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

        existing_session = PENDING_SESSIONS.get(session_id, {})

        # Payer Type: explicit in parsed_info -> existing in session -> default
        payer_type = parsed_info.payer_type or existing_session.get("payer_type") or eff_settings.default_payer_type

        # Cargo Type: explicit in parsed_info -> existing in session -> default
        cargo_type = parsed_info.cargo_type or existing_session.get("cargo_type") or eff_settings.default_cargo_type

        # COD Amount & Payout Type:
        if parsed_info.cod_amount is not None:
            cod_val = parsed_info.cod_amount
        elif "cod_amount" in existing_session:
            cod_val = existing_session["cod_amount"]
        else:
            cod_val = 0.0

        if parsed_info.cod_payment_type:
            cod_type = parsed_info.cod_payment_type
        elif "cod_payment_type" in existing_session:
            cod_type = existing_session["cod_payment_type"]
        else:
            cod_type = "cash"

        # Declared Value:
        if parsed_info.declared_value is not None:
            raw_decl = parsed_info.declared_value
        elif "declared_value" in existing_session:
            raw_decl = existing_session["declared_value"]
        else:
            raw_decl = eff_settings.default_declared_value
        declared_val = max(raw_decl, 500.0, cod_val)

        # Cargo Description:
        if parsed_info.cargo_description:
            cargo_desc = parsed_info.cargo_description
        elif "cargo_description" in existing_session:
            cargo_desc = existing_session["cargo_description"]
        else:
            cargo_desc = "Посилка"

        # Keep parsed_info strictly synchronized with current state
        parsed_info.city_name = matched_city.description
        if matched_city.area:
            parsed_info.region_name = matched_city.area
        if warehouse:
            parsed_info.warehouse_number = warehouse.warehouse_number or int(warehouse.number)
            parsed_info.is_postomat = warehouse.is_postomat
        parsed_info.payer_type = payer_type
        parsed_info.cargo_type = cargo_type
        parsed_info.declared_value = declared_val
        parsed_info.cargo_description = cargo_desc
        parsed_info.cod_amount = cod_val
        parsed_info.cod_payment_type = cod_type

        editing_ref = existing_session.get("editing_draft_ref")

        session_payload = {
            "parsed_info": parsed_info,
            "city": matched_city,
            "warehouse": warehouse,
            "is_address_delivery": is_address_deliv,
            "street_name": parsed_info.street_name,
            "street_ref": street_ref,
            "building_number": parsed_info.building_number,
            "flat_number": parsed_info.flat_number,
            "destination_description": dest_desc,
            "payer_type": payer_type,
            "cargo_type": cargo_type,
            "declared_value": declared_val,
            "cargo_description": cargo_desc,
            "cod_amount": cod_val,
            "cod_payment_type": cod_type,
            "user_id": user_id,
            "updated_at": now_ts,
        }
        if editing_ref:
            session_payload["editing_draft_ref"] = editing_ref

        PENDING_SESSIONS[session_id] = session_payload
        USER_ACTIVE_SESSIONS[user_id] = session_id
        USER_LAST_PARSED_INFO[user_id] = parsed_info

        cod_str = "❌ Немає" if cod_val <= 0 else f"{int(cod_val)} грн ({'Картка' if cod_type == 'card' else 'Готівка'})"

        card_text = (
            "📋 *Розпарсені дані отримувача для перевірки:*\n\n"
            f"👤 *Отримувач:* {parsed_info.full_name}\n"
            f"📞 *Телефон:* `{parsed_info.phone}`\n"
            f"🏙 *Місто:* {matched_city.description}\n"
            f"📦 *Пункт призначення:* {dest_desc}\n"
            f"📝 *Опис вантажу:* {cargo_desc}\n"
            f"💰 *Оціночна вартість:* {int(declared_val)} грн (Мін. 500 грн)\n"
            f"💵 *Накладений платіж:* {cod_str}\n\n"
            "Перевірте дані та оберіть дію нижче:"
        )

        u_custom = storage_manager.get_user_settings(user_id)
        card_mask = u_custom.sender_card_mask

        await status_msg.edit_text(
            card_text,
            parse_mode="Markdown",
            reply_markup=get_confirmation_keyboard(
                payer_type=payer_type,
                cargo_type=cargo_type,
                declared_value=declared_val,
                cod_amount=cod_val,
                cod_payment_type=cod_type,
                sender_card_mask=card_mask,
                session_id=session_id,
            ),
        )

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

        # Check if user sent a standalone 16-digit bank card number
        clean_card_digits = "".join(filter(str.isdigit, text))
        if len(clean_card_digits) == 16 and not clean_card_digits.startswith("380"):
            masked_card = f"{clean_card_digits[:6]}******{clean_card_digits[-4:]}"
            storage_manager.update_user_settings(user_id, sender_card_mask=masked_card)
            await message.answer(
                f"💳 *Банківську картку для виплати наложки успішно збережено:* `{masked_card}`",
                parse_mode="Markdown",
            )
            return

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

        session["updated_at"] = datetime.datetime.now().timestamp()
        action = callback_data.action
        user_id = session["user_id"]

        if action == "cancel":
            clear_user_active_session(user_id)
            await callback.message.edit_text("❌ *Створення накладної скасовано.*", parse_mode="Markdown")
            await callback.answer()
            return

        if action == "toggle_payer":
            new_payer = "Sender" if session.get("payer_type", "Recipient") == "Recipient" else "Recipient"
            session["payer_type"] = new_payer
            await callback.message.edit_reply_markup(
                reply_markup=get_confirmation_keyboard(
                    payer_type=new_payer,
                    cargo_type=session.get("cargo_type", "Parcel"),
                    declared_value=session.get("declared_value", 500.0),
                    cod_amount=session.get("cod_amount", 0.0),
                    cod_payment_type=session.get("cod_payment_type", "cash"),
                    session_id=session_id,
                )
            )
            payer_ua = "Отримувач" if new_payer == "Recipient" else "Відправник"
            await callback.answer(f"Платника змінено на: {payer_ua}")
            return

        if action == "toggle_cargo":
            new_cargo = "Documents" if session.get("cargo_type", "Parcel") == "Parcel" else "Parcel"
            session["cargo_type"] = new_cargo
            await callback.message.edit_reply_markup(
                reply_markup=get_confirmation_keyboard(
                    payer_type=session.get("payer_type", "Recipient"),
                    cargo_type=new_cargo,
                    declared_value=session.get("declared_value", 500.0),
                    session_id=session_id,
                )
            )
            cargo_ua = "Посилка" if new_cargo == "Parcel" else "Документи"
            await callback.answer(f"Тип вантажу змінено на: {cargo_ua}")
            return

        if action == "cycle_value":
            current_val = session["declared_value"]
            cod_val = session.get("cod_amount", 0.0)
            try:
                curr_idx = VALUE_OPTIONS.index(current_val)
                next_val = VALUE_OPTIONS[(curr_idx + 1) % len(VALUE_OPTIONS)]
            except ValueError:
                next_val = VALUE_OPTIONS[0]

            if next_val < cod_val:
                next_val = cod_val

            session["declared_value"] = next_val

            parsed_info = session["parsed_info"]
            city = session["city"]
            warehouse = session.get("warehouse")
            dest_desc = session.get("destination_description")
            cargo_desc = session["cargo_description"]
            cod_type = session.get("cod_payment_type", "cash")
            cod_str = "❌ Немає" if cod_val <= 0 else f"{int(cod_val)} грн ({'Картка' if cod_type == 'card' else 'Готівка'})"

            card_text = (
                "📋 *Розпарсені дані отримувача для перевірки:*\n\n"
                f"👤 *Отримувач:* {parsed_info.full_name}\n"
                f"📞 *Телефон:* `{parsed_info.phone}`\n"
                f"🏙 *Місто:* {city.description}\n"
                f"📦 *Пункт призначення:* {dest_desc}\n"
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
            if next_cod > session["declared_value"]:
                session["declared_value"] = next_cod

            u_custom = storage_manager.get_user_settings(user_id)
            card_mask = u_custom.sender_card_mask
            parsed_info = session["parsed_info"]
            city = session["city"]
            dest_desc = session.get("destination_description")
            cargo_desc = session["cargo_description"]
            declared_val = session["declared_value"]
            cod_type = session.get("cod_payment_type", "cash")
            cod_str = "❌ Немає" if next_cod <= 0 else f"{int(next_cod)} грн ({'Картка' if cod_type == 'card' else 'Готівка'})"

            card_text = (
                "📋 *Розпарсені дані отримувача для перевірки:*\n\n"
                f"👤 *Отримувач:* {parsed_info.full_name}\n"
                f"📞 *Телефон:* `{parsed_info.phone}`\n"
                f"🏙 *Місто:* {city.description}\n"
                f"📦 *Пункт призначення:* {dest_desc}\n"
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
                    sender_card_mask=card_mask,
                    session_id=session_id,
                ),
            )
            msg_str = "Скасовано" if next_cod <= 0 else f"{int(next_cod)} грн"
            await callback.answer(f"Накладений платіж: {msg_str}")
            return

        if action == "toggle_cod_type":
            new_type = "card" if session.get("cod_payment_type", "cash") == "cash" else "cash"
            session["cod_payment_type"] = new_type

            u_custom = storage_manager.get_user_settings(user_id)
            card_mask = u_custom.sender_card_mask

            if new_type == "card" and not card_mask:
                await callback.answer(
                    "⚠️ Для виплати на картку збережіть її номер командою: /set_card НомерКартки",
                    show_alert=True,
                )

            parsed_info = session["parsed_info"]
            city = session["city"]
            dest_desc = session.get("destination_description")
            cargo_desc = session["cargo_description"]
            declared_val = session["declared_value"]
            cod_val = session.get("cod_amount", 0.0)
            cod_str = "❌ Немає" if cod_val <= 0 else f"{int(cod_val)} грн ({'Картка' if new_type == 'card' else 'Готівка'})"

            card_text = (
                "📋 *Розпарсені дані отримувача для перевірки:*\n\n"
                f"👤 *Отримувач:* {parsed_info.full_name}\n"
                f"📞 *Телефон:* `{parsed_info.phone}`\n"
                f"🏙 *Місто:* {city.description}\n"
                f"📦 *Пункт призначення:* {dest_desc}\n"
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
                    cod_amount=cod_val,
                    cod_payment_type=new_type,
                    sender_card_mask=card_mask,
                    session_id=session_id,
                ),
            )
            type_ua = "На картку" if new_type == "card" else "Готівкою у відділенні"
            await callback.answer(f"Виплату змінено на: {type_ua}")
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
            warehouse = session.get("warehouse")
            is_address_deliv = session.get("is_address_delivery", False)
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

                if is_address_deliv:
                    # Courier address delivery
                    street_ref = session.get("street_ref")
                    if not street_ref:
                        streets = await user_np_client.search_street(
                            city_ref=city.ref,
                            street_name=session.get("street_name") or parsed_info.street_name or "",
                        )
                        street_ref = streets[0].ref if streets else ""

                    if not street_ref:
                        raise RuntimeError(
                            f"Не вдалося знайти вулицю '{parsed_info.street_name}' у м. {city.description}"
                        )

                    addr_res = await user_np_client.create_counterparty_address(
                        counterparty_ref=recipient_res.counterparty_ref,
                        street_ref=street_ref,
                        building_number=session.get("building_number") or parsed_info.building_number or "1",
                        flat=session.get("flat_number") or parsed_info.flat_number or "",
                    )
                    target_address_ref = addr_res.ref
                    service_type = "WarehouseDoors"
                    dest_desc = session.get("destination_description") or f"🏡 Адресна доставка: вул. {parsed_info.street_name}, {parsed_info.building_number}"
                else:
                    target_address_ref = warehouse.ref
                    service_type = "WarehouseWarehouse"
                    dest_desc = warehouse.description

                # Create or Update Waybill
                if editing_ref:
                    wb_res = await user_np_client.update_waybill(
                        document_ref=editing_ref,
                        recipient_cp_ref=recipient_res.counterparty_ref,
                        recipient_contact_ref=recipient_res.contact_person_ref,
                        recipient_phone=parsed_info.phone or "",
                        recipient_city_ref=city.ref,
                        recipient_warehouse_ref=target_address_ref,
                        payer_type=payer_type,
                        description=cargo_desc,
                        seats_amount=eff_settings.default_seats_amount,
                        weight=eff_settings.default_weight,
                        declared_value=declared_value,
                        service_type=service_type,
                    )
                    storage_manager.delete_user_draft(user_id, editing_ref)
                else:
                    wb_res = await user_np_client.create_waybill(
                        recipient_cp_ref=recipient_res.counterparty_ref,
                        recipient_contact_ref=recipient_res.contact_person_ref,
                        recipient_phone=parsed_info.phone or "",
                        recipient_city_ref=city.ref,
                        recipient_warehouse_ref=target_address_ref,
                        payer_type=payer_type,
                        description=cargo_desc,
                        seats_amount=eff_settings.default_seats_amount,
                        weight=eff_settings.default_weight,
                        declared_value=declared_value,
                        cod_amount=cod_amount,
                        service_type=service_type,
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
                    warehouse_description=dest_desc,
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
                    f"📦 *Пункт призначення:* {dest_desc}\n"
                    f"📝 *Опис вантажу:* {cargo_desc}\n"
                    f"💳 *Платник:* {payer_ua}\n"
                    f"💰 *Доставка:* ~{wb_res.cost} грн | *Оцінка:* {int(declared_value)} грн\n"
                    f"💵 *Накладений платіж:* {cod_str}\n"
                    f"📅 *Очікувана дата доставки:* {wb_res.estimated_delivery_date or 'Не вказано'}\n\n"
                    f"🔗 [Відстежити ТТН на сайті Нової Пошти]({tracking_url})"
                )

                clear_user_active_session(user_id)
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
        action = callback_data.action
        ref = callback_data.ref

        eff_settings = storage_manager.get_effective_settings(user_id, settings)
        user_np_client = NovaPoshtaClient(eff_settings)

        if action == "delete":
            try:
                # Check if TTN is part of any active scan sheet (register) in user's saved lists
                user_scansheets = storage_manager.get_user_scansheets(user_id)
                user_drafts = storage_manager.get_user_drafts(user_id)
                current_draft = next((d for d in user_drafts if d.ref == ref or d.int_doc_number == ref), None)
                if current_draft:
                    for s in user_scansheets:
                        if current_draft.int_doc_number in s.document_numbers:
                            await callback.answer(
                                f"⚠️ Накладна {current_draft.int_doc_number} включена до Реєстру № {s.number}!\nСпочатку видаліть реєстр, щоб видалити ТТН.",
                                show_alert=True,
                            )
                            return

                deleted = await user_np_client.delete_waybill(ref)
                storage_manager.delete_user_draft(user_id, ref)
                if current_draft:
                    storage_manager.delete_user_draft(user_id, current_draft.ref)
                    storage_manager.delete_user_draft(user_id, current_draft.int_doc_number)

                if deleted:
                    await callback.message.edit_text(
                        "🗑 *Express-накладну успішно видалено з бази Нової Пошти.*",
                        parse_mode="Markdown",
                    )
                else:
                    await callback.message.edit_text(
                        "🗑 *Накладну видалено з локальної бази.*",
                        parse_mode="Markdown",
                    )
            except Exception as e:
                err_msg = str(e)
                logger.error(f"Error deleting waybill: {e}", exc_info=True)
                if "019fe6ed-54bf-7d7c-9bed-c30d0b31d983" in err_msg or "ScanSheet" in err_msg or "реєстр" in err_msg.lower():
                    await callback.answer(
                        "⚠️ Ця накладна вже додана до Реєстру (ScanSheet) Нової Пошти!\nСпочатку розформуйте/видаліть Реєстр, а потім видаляйте ТТН.",
                        show_alert=True,
                    )
                    return
                storage_manager.delete_user_draft(user_id, ref)
                if current_draft:
                    storage_manager.delete_user_draft(user_id, current_draft.ref)
                    storage_manager.delete_user_draft(user_id, current_draft.int_doc_number)
                await callback.message.edit_text(
                    "🗑 *Накладну видалено з локальної бази.*",
                    parse_mode="Markdown",
                )

        elif action == "copy":
            drafts = storage_manager.get_user_drafts(user_id)
            target = next((d for d in drafts if d.ref == ref or d.int_doc_number == ref), None)
            if target:
                text_to_copy = f"`{target.int_doc_number}`"
                await callback.answer(f"ТТН: {target.int_doc_number}", show_alert=False)
                await callback.message.reply(
                    f"📋 Номер накладної для копіювання:\n{text_to_copy}",
                    parse_mode="Markdown",
                )
            else:
                await callback.answer("Дані накладної не знайдено.", show_alert=True)

        elif action == "edit":
            drafts = storage_manager.get_user_drafts(user_id)
            target = next((d for d in drafts if d.ref == ref or d.int_doc_number == ref), None)
            
            target_dict = None
            if target:
                target_dict = {
                    "ref": target.ref,
                    "city_description": target.city_description,
                    "warehouse_description": target.warehouse_description,
                    "recipient_name": target.recipient_name,
                    "recipient_phone": target.recipient_phone,
                    "cargo_description": target.cargo_description,
                    "declared_value": target.declared_value,
                    "cod_amount": target.cod_amount,
                }
            else:
                live_drafts = await fetch_user_active_drafts(user_id, user_np_client, storage_manager)
                live_target = next((d for d in live_drafts if d["ref"] == ref or d["int_doc_number"] == ref), None)
                if live_target:
                    target_dict = live_target

            if not target_dict:
                await callback.answer("Дані чернетки не знайдено.", show_alert=True)
                return

            await callback.answer("Завантаження даних для редагування...")
            dummy_text = (
                f"Місто {target_dict['city_description']}, {target_dict['warehouse_description']}. "
                f"Отримувач {target_dict['recipient_name']}, {target_dict['recipient_phone']}. "
                f"{target_dict['cargo_description']}. Оцінка {int(target_dict['declared_value'])} грн."
            )
            cod_val = target_dict.get("cod_amount")
            if cod_val and float(cod_val) > 0:
                dummy_text += f" Накладений платіж {int(float(cod_val))} грн."

            status_msg = await callback.message.reply("⏳ *Завантаження чернетки накладної для редагування...*", parse_mode="Markdown")

            user_ai_extractor = AIExtractor(eff_settings)
            parsed_info = await user_ai_extractor.parse_text(dummy_text)

            session_id = str(uuid.uuid4())[:8]
            PENDING_SESSIONS[session_id] = {
                "parsed_info": parsed_info,
                "editing_draft_ref": target_dict["ref"],
                "user_id": user_id,
            }
            USER_ACTIVE_SESSIONS[user_id] = session_id

            await _continue_processing_recipient_info(
                message=callback.message,
                user_id=user_id,
                session_id=session_id,
                parsed_info=parsed_info,
                status_msg=status_msg,
            )

        elif action == "barcode":
            doc_number = None
            if ref.isdigit() and len(ref) >= 10:
                doc_number = ref
            else:
                drafts = storage_manager.get_user_drafts(user_id)
                target = next((d for d in drafts if d.ref == ref or d.int_doc_number == ref), None)
                if target:
                    doc_number = target.int_doc_number
                else:
                    try:
                        live_drafts = await fetch_user_active_drafts(user_id, user_np_client, storage_manager)
                        live_target = next((d for d in live_drafts if d.get("ref") == ref or d.get("int_doc_number") == ref), None)
                        if live_target:
                            doc_number = live_target.get("int_doc_number")
                    except Exception as e:
                        logger.error(f"Error fetching live drafts for barcode: {e}")

            if not doc_number:
                if ref and len(ref) >= 10:
                    doc_number = ref
                else:
                    await callback.answer("Номер накладної не знайдено.", show_alert=True)
                    return

            await callback.answer("Генерація штрихкоду ТТН...")
            try:
                barcode_bytes = generate_code128_barcode(doc_number)
                photo_file = BufferedInputFile(barcode_bytes, filename=f"ttn_{doc_number}.png")
                caption = (
                    f"📱 *Штрихкод накладної ТТН №* `{doc_number}`\n\n"
                    f"_Покажіть цей штрихкод оператору або відскануйте у відділенні чи поштоматі Нової Пошти._"
                )
                await callback.message.reply_photo(
                    photo=photo_file,
                    caption=caption,
                    parse_mode="Markdown",
                )
            except Exception as e:
                logger.error(f"Error generating waybill barcode photo: {e}", exc_info=True)
                await callback.message.reply(
                    f"❌ *Помилка створення штрихкоду для ТТН {doc_number}:* {str(e)}",
                    parse_mode="Markdown",
                )

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

        payer_type = session.get("payer_type") or parsed_info.payer_type or eff_settings.default_payer_type
        cargo_type = session.get("cargo_type") or parsed_info.cargo_type or eff_settings.default_cargo_type
        declared_val = session.get("declared_value") or max(
            parsed_info.declared_value or eff_settings.default_declared_value,
            500.0,
        )
        cargo_desc = session.get("cargo_description") or parsed_info.cargo_description or "Посилка"
        cod_val = session.get("cod_amount", 0.0)
        cod_type = session.get("cod_payment_type", "cash")

        session["city"] = matched_city
        session["warehouse"] = warehouse
        session["destination_description"] = warehouse.description
        session["payer_type"] = payer_type
        session["cargo_type"] = cargo_type
        session["declared_value"] = declared_val
        session["cargo_description"] = cargo_desc
        session["cod_amount"] = cod_val
        session["cod_payment_type"] = cod_type
        session["updated_at"] = datetime.datetime.now().timestamp()
        session.pop("candidates", None)

        parsed_info.city_name = matched_city.description
        if matched_city.area:
            parsed_info.region_name = matched_city.area
        parsed_info.warehouse_number = warehouse.warehouse_number or int(warehouse.number)
        parsed_info.is_postomat = warehouse.is_postomat
        parsed_info.payer_type = payer_type
        parsed_info.cargo_type = cargo_type
        parsed_info.declared_value = declared_val
        parsed_info.cargo_description = cargo_desc
        parsed_info.cod_amount = cod_val
        parsed_info.cod_payment_type = cod_type
        session["parsed_info"] = parsed_info
        USER_LAST_PARSED_INFO[user_id] = parsed_info

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

        u_custom = storage_manager.get_user_settings(user_id)
        card_mask = u_custom.sender_card_mask

        await callback.message.edit_text(
            card_text,
            parse_mode="Markdown",
            reply_markup=get_confirmation_keyboard(
                payer_type=payer_type,
                cargo_type=cargo_type,
                declared_value=declared_val,
                cod_amount=cod_val,
                cod_payment_type=cod_type,
                sender_card_mask=card_mask,
                session_id=session_id,
            ),
        )
        await callback.answer(f"Обрано: {matched_city.description}")

    @router.callback_query(StreetSelectCallback.filter())
    async def process_street_select_callback(
        callback: CallbackQuery, callback_data: StreetSelectCallback
    ):
        """Handle user selection of a street/lane when multiple candidates match."""
        session_id = callback_data.session_id
        session = PENDING_SESSIONS.get(session_id)

        if not session or "street_candidates" not in session:
            await callback.answer("Сесія застаріла. Надішліть реквізити заново.", show_alert=True)
            return

        user_id = callback.from_user.id
        eff_settings = storage_manager.get_effective_settings(user_id, settings)

        candidates = session["street_candidates"]
        target_ref = callback_data.street_ref

        selected_street = next((s for s in candidates if s.ref == target_ref), None)
        if not selected_street:
            await callback.answer("Вулицю не знайдено.", show_alert=True)
            return

        parsed_info = session["parsed_info"]
        city = session["city"]

        street_ref = selected_street.ref
        street_display = f"{selected_street.streets_type} {selected_street.description}"

        addr_parts = [f"{street_display}, буд. {session.get('building_number') or parsed_info.building_number or '1'}"]
        flat_num = session.get("flat_number") or parsed_info.flat_number
        if flat_num:
            addr_parts.append(f"кв. {flat_num}")
        dest_desc = f"🏡 Адресна доставка: {', '.join(addr_parts)}"

        declared_val = session.get("declared_value") or max(
            parsed_info.declared_value or eff_settings.default_declared_value,
            500.0,
        )
        cargo_desc = session.get("cargo_description") or parsed_info.cargo_description or "Посилка"
        cod_val = session.get("cod_amount", 0.0)
        cod_type = session.get("cod_payment_type", "cash")

        session["street_ref"] = street_ref
        session["street_name"] = selected_street.description
        session["destination_description"] = dest_desc
        session["is_address_delivery"] = True
        session["updated_at"] = datetime.datetime.now().timestamp()
        session.pop("street_candidates", None)

        parsed_info.city_name = city.description
        if city.area:
            parsed_info.region_name = city.area
        parsed_info.street_name = selected_street.description
        parsed_info.is_address_delivery = True
        parsed_info.declared_value = declared_val
        parsed_info.cargo_description = cargo_desc
        parsed_info.cod_amount = cod_val
        parsed_info.cod_payment_type = cod_type
        session["parsed_info"] = parsed_info
        USER_LAST_PARSED_INFO[user_id] = parsed_info

        cod_str = "❌ Немає" if cod_val <= 0 else f"{int(cod_val)} грн ({'Картка' if cod_type == 'card' else 'Готівка'})"

        card_text = (
            "📋 *Розпарсені дані отримувача для перевірки:*\n\n"
            f"👤 *Отримувач:* {parsed_info.full_name}\n"
            f"📞 *Телефон:* `{parsed_info.phone}`\n"
            f"🏙 *Місто:* {city.description}\n"
            f"📦 *Пункт призначення:* {dest_desc}\n"
            f"📝 *Опис вантажу:* {cargo_desc}\n"
            f"💰 *Оціночна вартість:* {int(declared_val)} грн (Мін. 500 грн)\n"
            f"💵 *Накладений платіж:* {cod_str}\n\n"
            "Перевірте дані та оберіть дію нижче:"
        )

        u_custom = storage_manager.get_user_settings(user_id)
        card_mask = u_custom.sender_card_mask

        await callback.message.edit_text(
            card_text,
            parse_mode="Markdown",
            reply_markup=get_confirmation_keyboard(
                payer_type=session.get("payer_type", eff_settings.default_payer_type),
                cargo_type=session.get("cargo_type", eff_settings.default_cargo_type),
                declared_value=declared_val,
                cod_amount=cod_val,
                cod_payment_type=cod_type,
                sender_card_mask=card_mask,
                session_id=session_id,
            ),
        )
        await callback.answer(f"Обрано: {street_display}")

    @router.callback_query(RegisterActionCallback.filter())
    async def process_register_callback(
        callback: CallbackQuery, callback_data: RegisterActionCallback
    ):
        """Handle inline actions on saved ScanSheet registers."""
        user_id = callback.from_user.id
        action = callback_data.action
        ref = callback_data.ref

        eff_settings = storage_manager.get_effective_settings(user_id, settings)
        user_np_client = NovaPoshtaClient(eff_settings)

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
            try:
                deleted = await user_np_client.delete_scan_sheet(ref)
                storage_manager.delete_user_scansheet(user_id, ref)
                deleted_text = "🗑 *Реєстр (ScanSheet) успішно розформовано та видалено з бази Нової Пошти.*" if deleted else "🗑 *Реєстр видалено з локальної бази.*"

                if callback.message.photo or callback.message.caption:
                    await callback.message.edit_caption(
                        caption=deleted_text,
                        parse_mode="Markdown",
                    )
                else:
                    await callback.message.edit_text(
                        deleted_text,
                        parse_mode="Markdown",
                    )
            except Exception as e:
                logger.error(f"Error deleting scan sheet: {e}", exc_info=True)
                storage_manager.delete_user_scansheet(user_id, ref)
                local_deleted_text = "🗑 *Реєстр видалено з локальної бази.*"
                if callback.message.photo or callback.message.caption:
                    try:
                        await callback.message.edit_caption(
                            caption=local_deleted_text,
                            parse_mode="Markdown",
                        )
                    except Exception:
                        await callback.message.answer(local_deleted_text, parse_mode="Markdown")
                else:
                    try:
                        await callback.message.edit_text(
                            local_deleted_text,
                            parse_mode="Markdown",
                        )
                    except Exception:
                        await callback.message.answer(local_deleted_text, parse_mode="Markdown")

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

        user_id = callback.from_user.id
        choice = callback_data.choice
        parsed_info: ParsedRecipientInfo = session["parsed_info"]
        session["address_choice_made"] = True
        session["user_id"] = user_id
        session["updated_at"] = datetime.datetime.now().timestamp()

        if choice == "courier":
            parsed_info.is_address_delivery = True
            parsed_info.has_address_suspicion = False
            session["is_address_delivery"] = True
            await callback.answer("Обрано адресну доставку кур'єром!")
        else:
            parsed_info.is_address_delivery = False
            parsed_info.has_address_suspicion = False
            session["is_address_delivery"] = False
            await callback.answer("Обрано доставку у відділення / поштомат!")

        status_msg = callback.message
        await _continue_processing_recipient_info(
            message=callback.message,
            user_id=user_id,
            session_id=session_id,
            parsed_info=parsed_info,
            status_msg=status_msg,
        )
