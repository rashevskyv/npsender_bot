"""Telegram bot message handlers and callback handlers in Ukrainian."""

import asyncio
import datetime
import logging
import re
import uuid
from typing import Dict, Any, Optional, List, Tuple

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, BufferedInputFile, InputMediaPhoto, InlineKeyboardMarkup

from src.config import Settings
from src.storage import (
    UserSettingsManager,
    UserCustomSettings,
    SavedDraft,
    SavedScanSheet,
    SenderProfile,
)
from src.ai.schemas import ParsedRecipientInfo
from src.ai.extractor import AIExtractor
from src.nova_poshta.client import NovaPoshtaClient
from src.utils.barcode_gen import generate_code128_barcode, generate_client_card_image
from src.utils.text_cleaner import normalize_apostrophes, is_city_matched
from src.nova_poshta.models import CODItemInfo, CODMonthlyStats, TrackingDocumentDetails
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
    CODActionCallback,
    CODSettingsCallback,
    get_cod_stats_keyboard,
    get_cod_settings_keyboard,
    get_cod_shipments_keyboard,
    TrackActionCallback,
    get_tracking_keyboard,
    get_waybill_action_keyboard,
    get_barcode_keyboard,
    get_limit_exceeded_confirmation_keyboard,
    UserProfileCallback,
    get_users_management_keyboard,
    get_profile_delete_keyboard,
    get_rename_profile_selection_keyboard,
    format_profile_display_name,
    ClientCardCallback,
    get_client_card_keyboard,
    get_settings_keyboard,
    get_missing_sender_address_keyboard,
)


logger = logging.getLogger(__name__)
router = Router()

# In-memory storage for active pending waybill verification sessions
PENDING_SESSIONS: Dict[str, Dict[str, Any]] = {}
USER_ACTIVE_SESSIONS: Dict[int, str] = {}  # user_id -> active session_id
USER_LAST_PARSED_INFO: Dict[int, ParsedRecipientInfo] = {}  # user_id -> last parsed recipient info for natural language follow-up edits
USER_INITIAL_MESSAGE_TEXT: Dict[int, str] = {}  # user_id -> initial message text introducing recipient/settlement details
USER_LAST_SCANSHEET_CONTEXT: Dict[int, Dict[str, Any]] = {}  # user_id -> last created or viewed register context
VALUE_OPTIONS = [500.0, 1000.0, 2000.0, 5000.0, 10000.0]
SESSION_TIMEOUT_SECONDS: float = 15 * 60  # 15 minutes TTL for active parcel creation sessions

# Debouncer buffers for multi-part forwarded messages
USER_MESSAGE_BUFFERS: Dict[int, List[str]] = {}
USER_DEBOUNCE_TASKS: Dict[int, asyncio.Task] = {}
USER_LAST_MESSAGES: Dict[int, Message] = {}
USER_PROCESSING_LOCKS: Dict[int, asyncio.Lock] = {}
USER_TRACKING_WAITING: set = set()
USER_ADD_PROFILE_WAITING: set = set()
USER_BARCODE_WAITING: set = set()
USER_CARD_WAITING: set = set()
USER_RENAME_PROFILE_WAITING: Dict[int, str] = {}


def get_user_processing_lock(user_id: int) -> asyncio.Lock:
    """Get or create a per-user asyncio.Lock for sequential message processing."""
    if user_id not in USER_PROCESSING_LOCKS:
        USER_PROCESSING_LOCKS[user_id] = asyncio.Lock()
    return USER_PROCESSING_LOCKS[user_id]



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
                USER_INITIAL_MESSAGE_TEXT.pop(u_id, None)


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
    USER_INITIAL_MESSAGE_TEXT.pop(user_id, None)
    USER_TRACKING_WAITING.discard(user_id)
    USER_ADD_PROFILE_WAITING.discard(user_id)
    USER_BARCODE_WAITING.discard(user_id)
    USER_CARD_WAITING.discard(user_id)
    USER_RENAME_PROFILE_WAITING.pop(user_id, None)


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
            "scan_sheet_number": getattr(d, "scan_sheet_number", None),
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

        live_ss_num = getattr(live_item, "scan_sheet_number", None)

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
                "scan_sheet_number": live_ss_num,
            }
        else:
            combined_drafts_map[doc_num]["scan_sheet_number"] = live_ss_num

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


def _render_progress_bar(used: float, total: Optional[float], length: int = 10) -> str:
    """Render emoji progress bar (e.g. 🟩🟩🟩🟩🟨⬜⬜ 60%)."""
    if not total or total <= 0:
        return "Без ліміту"
    ratio = used / total
    capped_ratio = min(max(ratio, 0.0), 1.0)
    filled = int(round(capped_ratio * length))

    if ratio >= 1.0:
        bar = "🟥" * length
    elif ratio >= 0.8:
        green_count = max(0, filled - 2)
        warn_count = filled - green_count
        empty_count = length - filled
        bar = ("🟩" * green_count) + ("🟨" * warn_count) + ("⬜" * empty_count)
    else:
        empty_count = length - filled
        bar = ("🟩" * filled) + ("⬜" * empty_count)

    pct = int(ratio * 100)
    return f"{bar} {pct}%"


def format_cod_dashboard(stats: CODMonthlyStats, user_settings: UserCustomSettings) -> str:
    """Format full Ukrainian visual text report for monthly Cash On Delivery stats and limits."""
    sum_limit = user_settings.cod_monthly_limit_sum
    count_limit = user_settings.cod_monthly_limit_count
    safe_limit = (sum_limit - 1.0) if (sum_limit and sum_limit > 0) else None

    sum_bar = _render_progress_bar(stats.total_sum, sum_limit) if sum_limit and sum_limit > 0 else "Вимкнено"
    cnt_bar = _render_progress_bar(float(stats.total_count), float(count_limit)) if count_limit and count_limit > 0 else "Вимкнено"

    sum_limit_str = f" / макс. `{int(safe_limit)} грн`" if safe_limit else ""
    cnt_limit_str = f" / `{count_limit} шт`" if count_limit and count_limit > 0 else ""

    lines = [
        f"📊 *Звіт накладеного платежу за {stats.month_name}*",
        f"🗓 _Період:_ `{stats.from_date}` — `{stats.to_date}`\n",
        f"💰 *Загальна сума:* `{int(stats.total_sum)} грн`{sum_limit_str}",
        f"   {sum_bar}\n",
        f"📦 *Кількість посилок:* `{stats.total_count} шт`{cnt_limit_str}",
        f"   {cnt_bar}\n",
        "──────────────",
        "📌 *Статуси за поточний місяць:*",
        f"  🟢 *Виплачено / Забрано:* `{int(stats.received_sum)} грн` ({stats.received_count} шт)",
        f"  🚚 *У дорозі / Очікують:* `{int(stats.in_transit_sum)} грн` ({stats.in_transit_count} шт)",
    ]

    if stats.drafts_count > 0:
        lines.append(f"  📝 *Чернетки (не відправлені):* `{int(stats.drafts_sum)} грн` ({stats.drafts_count} шт)")

    if stats.refused_count > 0:
        lines.append(f"  🔴 *Відмови / Повернення:* `{int(stats.refused_sum)} грн` ({stats.refused_count} шт)")

    lines.append("──────────────")

    # Remaining limit info & warnings
    if safe_limit and sum_limit:
        rem_sum = max(0.0, safe_limit - stats.total_sum)
        if stats.total_sum >= sum_limit:
            exceeded = int(stats.total_sum - safe_limit)
            lines.append(f"🚨 *УВАГА: Безпечний ліміт ({int(safe_limit)} грн) ПЕРЕВИЩЕНО на {exceeded} грн!*")
        elif (stats.total_sum / sum_limit) >= 0.8:
            lines.append(f"⚠️ *Увага: Залишок до безпечного ліміту ({int(safe_limit)} грн) лише {int(rem_sum)} грн!*")
        else:
            lines.append(f"✅ *Залишок до безпечного ліміту ({int(safe_limit)} грн):* `{int(rem_sum)} грн`")

    if count_limit and count_limit > 0:
        rem_cnt = max(0, count_limit - stats.total_count)
        if stats.total_count >= count_limit:
            lines.append(f"⚠️ *УВАГА: Ліміт кількості посилок ПЕРЕВИЩЕНО на {stats.total_count - count_limit} шт!*")
        elif stats.total_count >= int(count_limit * 0.8):
            lines.append(f"⚠️ *Увага: Залишилось всього {rem_cnt} посилок до ліміту!*")
        else:
            lines.append(f"✅ *Залишок посилок:* `{rem_cnt} шт`")

    # Next reset date
    next_month = stats.month + 1 if stats.month < 12 else 1
    next_year = stats.year if stats.month < 12 else stats.year + 1
    lines.append(f"\n📅 _Лічильник автоматично обнулиться:_ `01.{next_month:02d}.{next_year}`")

    return "\n".join(lines)


def format_cod_shipments_page(stats: CODMonthlyStats, page: int = 0, page_size: int = 5) -> str:
    """Format paginated list of shipments with Cash On Delivery."""
    items = stats.items
    if not items:
        return f"📜 *Посилок з накладеним платежем за {stats.month_name} не знайдено.*"

    total_items = len(items)
    total_pages = (total_items + page_size - 1) // page_size
    current_page = max(0, min(page, total_pages - 1))
    start_idx = current_page * page_size
    end_idx = min(start_idx + page_size, total_items)
    page_items = items[start_idx:end_idx]

    lines = [
        f"📜 *Накладні та суми післяплати ({stats.month_name})*",
        f"💰 *Всього за місяць:* `{int(stats.total_sum)} грн` ({stats.total_count} ТТН)",
        f"_Показано {start_idx + 1}-{end_idx} із {total_items} (Сторінка {current_page + 1}/{total_pages}):_\n",
    ]

    for idx, item in enumerate(page_items, start_idx + 1):
        if item.is_received:
            status_icon = "🟢"
            status_text = "Виплачено / Забрано"
        elif item.is_refused:
            status_icon = "🔴"
            status_text = "Відмова / Повернення"
        elif item.is_draft:
            status_icon = "📝"
            status_text = "Чернетка"
        else:
            status_icon = "🚚"
            status_text = item.state_name or "У дорозі"

        payout_type_str = "💳 на картку" if item.cod_payment_type == "card" else "💵 готівкою"

        lines.append(
            f"*{idx}.* 🎫 `{item.int_doc_number}` ({item.date_created})\n"
            f"   💰 *Сума післяплати:* `{int(item.cod_amount)} грн` ({payout_type_str})\n"
            f"   {status_icon} *Статус:* {status_text}\n"
            f"   👤 *Отримувач:* {item.recipient_name} ({item.city_recipient})\n"
            f"   📦 *Вантаж:* {item.description}\n"
        )

    return "\n".join(lines)


def extract_ttn_from_text(text: str) -> Optional[str]:
    """Extract standalone or prominent 14-digit (or 11-digit) Nova Poshta express waybill number from text."""
    if not text:
        return None

    stripped = text.strip()

    # 1. Direct match if the string consists of digits and separators
    clean_digits = "".join(filter(str.isdigit, stripped))
    if len(clean_digits) == 14 and not clean_digits.startswith("380"):
        alpha_chars = [c for c in stripped if c.isalpha()]
        if len(alpha_chars) <= 15:
            return clean_digits

    # 2. Match standard 14-digit pattern (starting with 1, 2, or 5) with optional spaces or hyphens
    match_14 = re.search(r'(?<!\d)(?:1|2|5)\d{3}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{2}(?!\d)', stripped)
    if match_14:
        found_num = "".join(filter(str.isdigit, match_14.group(0)))
        if len(found_num) == 14:
            return found_num

    # 3. Match any standalone 14 digits
    match_any14 = re.search(r'(?<!\d)\d{14}(?!\d)', stripped)
    if match_any14:
        found_num = match_any14.group(0)
        if not found_num.startswith("380"):
            return found_num

    # 4. Match 11-digit format
    match_11 = re.search(r'(?<!\d)(?:1|2|5)\d{10}(?!\d)', stripped)
    if match_11:
        return match_11.group(0)

    if len(clean_digits) == 11 and clean_digits[0] in ("1", "2", "5"):
        alpha_chars = [c for c in stripped if c.isalpha()]
        if len(alpha_chars) <= 15:
            return clean_digits

    return None


def is_tracking_intent(text: str) -> bool:
    """Check if message expresses an intent to track a parcel."""
    t = text.lower()
    keywords = [
        "відстеж",
        "відслідк",
        "трекінг",
        "трек",
        "track",
    ]
    if any(k in t for k in keywords):
        return True

    # Combination of inquiry keyword and parcel entity
    has_inquiry = any(w in t for w in ["де ", "статус", "перевір", "знайди", "знайти", "пошук"])
    has_parcel_entity = any(w in t for w in ["посилк", "вантаж", "накладн", "ттн", "пакунок"])
    return bool(has_inquiry and has_parcel_entity)


def is_barcode_intent(text: str) -> bool:
    """Check if message expresses an intent to generate barcode for a waybill."""
    t = text.lower()
    keywords = [
        "штрихкод",
        "штрих-код",
        "штрих код",
        "barcode",
        "бар код",
        "баркод",
    ]
    return any(k in t for k in keywords)


CARD_BLOCK_REGEX = re.compile(
    r"(?:\b|^)(?:(?:\d{4}[ -]?){3}\d{4}(?:\d{2,3})?|\d{16,19})(?:\b|$)"
)
PHONE_IN_TEXT_REGEX = re.compile(
    r"(?:(?:\+?38)?\s*\(?0\d{2}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}|0\d{9})"
)
RECIPIENT_KEYWORDS_REGEX = re.compile(
    r"\b(?:відділення|відділ|відд|склад|поштомат|почтомат|отделение|місто|м\.|село|смт|вул|вулиця|просп|проспект|буд|будинок|обл|область|наложк|накладен|оцінк|посилк)\b",
    re.IGNORECASE,
)


def extract_standalone_bank_card(text: str, is_waiting: bool = False) -> Optional[str]:
    """Extract a valid standalone bank card number (16-19 digits) from user message.

    Returns clean digit string if the message represents a bank card, or None if:
    - No contiguous 16-19 digit card pattern is found.
    - Digits are scattered across multiple fields (e.g. phone + warehouse + cod amount).
    - Message contains recipient waybill indicators (recipient phone number, delivery branch/city keywords, multi-line format).
    """
    if not text:
        return None

    # Handle masked card format e.g. 414932******1234
    masked_match = re.search(r"(?:\b|^)\d{6}\*{4,6}\d{4}(?:\b|$)", text.strip())
    if masked_match:
        return masked_match.group(0)

    m = CARD_BLOCK_REGEX.search(text)
    if not m:
        return None

    card_str = m.group(0)
    clean_digits = "".join(filter(str.isdigit, card_str))
    if len(clean_digits) not in (16, 18, 19):
        return None
    if clean_digits.startswith("380"):
        return None

    # Inspect remaining text around the matched card
    remainder = (text[:m.start()] + " " + text[m.end():]).strip()

    # If the rest of the message has a recipient phone or delivery keywords, it is a waybill, NOT a standalone card
    if PHONE_IN_TEXT_REGEX.search(remainder):
        return None
    if RECIPIENT_KEYWORDS_REGEX.search(remainder):
        return None
    if len(remainder.splitlines()) > 2 or len(remainder) > 60:
        return None

    return clean_digits


def format_tracking_card(doc: TrackingDocumentDetails) -> str:
    """Format full tracking details from Nova Poshta API into an informative Telegram markdown card."""
    lines = []

    # 1. Header & Number
    lines.append("📦 *Експрес-накладна (ТТН):*")
    lines.append(f"📄 `{doc.number}`\n")

    # 2. Status with icon
    status_lower = (doc.status or "").lower()
    code = doc.status_code

    if code in ("2", "3") or any(w in status_lower for w in ["не знайдено", "видалено", "скасовано"]):
        icon = "❌"
    elif code in ("9", "10", "11") or "отримано" in status_lower or "доставлено" in status_lower:
        icon = "🟢"
    elif code in ("7", "8") or "у відділенні" in status_lower or "прибув" in status_lower:
        icon = "📦"
    elif code in ("4", "5", "6") or "прямує" in status_lower or "в дорозі" in status_lower:
        icon = "🚚"
    elif code == "1" or "очікує" in status_lower or "створено" in status_lower:
        icon = "📝"
    elif code in ("101", "102", "103", "104", "105", "106", "108") or "відмова" in status_lower or "повернення" in status_lower:
        icon = "🔴"
    else:
        icon = "ℹ️"

    lines.append(f"{icon} *Статус:* {doc.status or 'Інформація оновлюється'}")
    if doc.is_light_return:
        lines.append("🔄 *Послуга:* Легке повернення")

    # If document not found or deleted, return concise card
    if code in ("2", "3") or "не знайдено" in status_lower:
        lines.append("\n💡 _Перевірте правильність номера ТТН або спробуйте пізніше._")
        return "\n".join(lines)

    lines.append("")

    # 3. Route (Звідки ➡️ Куди)
    lines.append("📍 *Маршрут:*")
    from_parts = []
    if doc.city_sender:
        from_parts.append(doc.city_sender)
    if doc.warehouse_sender:
        from_parts.append(doc.warehouse_sender)
    elif doc.warehouse_sender_address:
        from_parts.append(doc.warehouse_sender_address)
    elif doc.sender_address:
        from_parts.append(doc.sender_address)

    from_desc = ", ".join(from_parts) if from_parts else "Відділення / Склад"
    lines.append(f"📤 *Звідки:* {from_desc}")
    if doc.sender_full_name:
        phone_s = f" ({doc.sender_phone})" if doc.sender_phone else ""
        lines.append(f"   👤 {doc.sender_full_name}{phone_s}")

    to_parts = []
    if doc.city_recipient:
        to_parts.append(doc.city_recipient)
    if doc.warehouse_recipient:
        to_parts.append(doc.warehouse_recipient)
    elif doc.warehouse_recipient_address:
        to_parts.append(doc.warehouse_recipient_address)
    elif doc.recipient_address:
        to_parts.append(doc.recipient_address)

    to_desc = ", ".join(to_parts) if to_parts else "Відділення / Склад"
    lines.append(f"📥 *Куди:* {to_desc}")
    if doc.recipient_full_name:
        phone_r = f" ({doc.recipient_phone})" if doc.recipient_phone else ""
        lines.append(f"   👤 {doc.recipient_full_name}{phone_r}")

    lines.append("")

    # 4. Dates & Timeline
    dates_block = []
    if doc.date_created:
        dates_block.append(f"• Створено: {doc.date_created}")
    if doc.scheduled_delivery_date:
        dates_block.append(f"• Орієнтовна доставка: {doc.scheduled_delivery_date}")
    if doc.actual_delivery_date or doc.recipient_date_time:
        actual = doc.actual_delivery_date or doc.recipient_date_time
        dates_block.append(f"• Отримано: {actual}")
    elif doc.date_scan or doc.date_moving:
        scan = doc.date_scan or doc.date_moving
        dates_block.append(f"• Останній рух: {scan}")
    if doc.date_first_day_storage:
        dates_block.append(f"• Безкоштовне зберігання до: {doc.date_first_day_storage}")

    if dates_block:
        lines.append("📅 *Хронологія та дати:*")
        lines.extend(dates_block)
        lines.append("")

    # 5. Parcel Parameters
    lines.append("📦 *Параметри вантажу:*")
    cargo_desc = doc.cargo_description or doc.cargo_type or "Посилка"
    lines.append(f"• Вміст: {cargo_desc}")

    w = doc.document_weight or doc.factual_weight
    if w > 0:
        vol_str = f" (об'ємна: {doc.volume_weight:.1f} кг)" if doc.volume_weight > 0 else ""
        lines.append(f"• Вага: {w:.1f} кг{vol_str}")
    if doc.seats_amount > 1:
        lines.append(f"• Кількість місць: {doc.seats_amount}")
    if doc.announced_price > 0:
        lines.append(f"• Оціночна вартість: {int(doc.announced_price)} грн")

    lines.append("")

    # 6. Financial & Payment details
    lines.append("💳 *Оплата та доставка:*")
    p_map = {"Recipient": "Отримувач", "Sender": "Відправник", "ThirdPerson": "Третя особа"}
    payer_ua = p_map.get(doc.payer_type, doc.payer_type or "Отримувач")
    cost_val = doc.document_cost
    if cost_val > 0:
        lines.append(f"• Вартість доставки: {cost_val:.2f} грн (Платник: {payer_ua})")

    pay_status = doc.express_waybill_payment_status or doc.payment_status
    if pay_status:
        status_icon = "🟢" if any(w in pay_status.lower() for w in ["сплачено", "оплачено", "paid"]) else "🟡"
        lines.append(f"• Оплата доставки: {status_icon} {pay_status}")

    cod_val = doc.afterpayment_cost or doc.redelivery_sum
    if cod_val > 0:
        lines.append(f"• 💰 *Накладений платіж:* {cod_val:.2f} грн")
        if doc.redelivery_card:
            lines.append(f"  💳 Виплата на картку: `{doc.redelivery_card}`")
        elif doc.redelivery_payer:
            lines.append(f"  💵 Платник наложки: {doc.redelivery_payer}")
        if doc.redelivery_num:
            lines.append(f"  🔢 Переказ №: `{doc.redelivery_num}`")

    if doc.amount_to_pay > 0:
        lines.append(f"• 💵 *Разом до сплати при отриманні:* *{doc.amount_to_pay:.2f} грн*")
    elif doc.amount_paid > 0 and (code in ("9", "10", "11") or "отримано" in status_lower):
        lines.append(f"• 🟢 Сплачено при отриманні: {doc.amount_paid:.2f} грн")

    if doc.undelivery_reasons:
        sub = f" ({doc.undelivery_reasons_subtype})" if doc.undelivery_reasons_subtype else ""
        lines.append(f"\n⚠️ *Причина затримки/недоставки:* {doc.undelivery_reasons}{sub}")

    return "\n".join(lines)


async def evaluate_all_profiles_cod_limits(
    user_id: int,
    cod_val: float,
    storage_manager: UserSettingsManager,
    eff_settings: Settings,
    editing_ref: Optional[str] = None,
) -> Dict[str, Any]:
    """Evaluate COD limits across all configured sender profiles of a user.
    Finds the profile with the most remaining limit and determines if an alternative should be recommended.
    """
    profiles = storage_manager.get_sender_profiles(user_id)
    active_profile = storage_manager.get_active_profile(user_id)

    if not profiles:
        return {
            "profiles_status": {},
            "suggested_profile": None,
            "active_profile": None,
        }

    profiles_status = {}

    async def _fetch_prof_stat(p):
        try:
            p_settings = storage_manager.get_effective_settings(user_id, eff_settings, profile_id=p.id)
            if not p_settings.nova_poshta_api_key or not p_settings.sender_phone:
                s_limit = (p.cod_monthly_limit_sum - 1.0) if (p.cod_monthly_limit_sum and p.cod_monthly_limit_sum > 0) else 29999.0
                return p.id, {
                    "profile": p,
                    "used_sum": 0.0,
                    "used_cnt": 0,
                    "rem_sum": s_limit,
                    "safe_limit": s_limit,
                    "new_sum": cod_val,
                    "is_exceeded": False,
                }

            p_client = NovaPoshtaClient(p_settings)
            stats = await p_client.get_monthly_cod_stats(
                user_phone=p_settings.sender_phone,
                user_cp_ref=p_settings.sender_counterparty_ref,
            )
            u_sum = stats.total_sum
            u_cnt = stats.total_count

            if editing_ref and stats.items:
                for item in stats.items:
                    if item.ref == editing_ref or item.int_doc_number == editing_ref:
                        u_sum = max(0.0, u_sum - item.cod_amount)
                        u_cnt = max(0, u_cnt - 1)
                        break

            s_limit = (p.cod_monthly_limit_sum - 1.0) if (p.cod_monthly_limit_sum and p.cod_monthly_limit_sum > 0) else 29999.0
            r_sum = max(0.0, s_limit - u_sum)
            new_s = u_sum + cod_val
            is_exc = bool(p.cod_monthly_limit_sum and new_s >= p.cod_monthly_limit_sum)
            return p.id, {
                "profile": p,
                "used_sum": u_sum,
                "used_cnt": u_cnt,
                "rem_sum": r_sum,
                "safe_limit": s_limit,
                "new_sum": new_s,
                "is_exceeded": is_exc,
            }
        except Exception as e:
            logger.warning(f"Failed to fetch COD stats for profile {p.name} ({p.id}): {e}")
            s_limit = (p.cod_monthly_limit_sum - 1.0) if (p.cod_monthly_limit_sum and p.cod_monthly_limit_sum > 0) else 29999.0
            return p.id, {
                "profile": p,
                "used_sum": 0.0,
                "used_cnt": 0,
                "rem_sum": s_limit,
                "safe_limit": s_limit,
                "new_sum": cod_val,
                "is_exceeded": False,
            }

    results = await asyncio.gather(*[_fetch_prof_stat(p) for p in profiles])
    for pid, pdata in results:
        profiles_status[pid] = pdata

    suggested_profile = None
    if len(profiles) > 1 and active_profile:
        act_stat = profiles_status.get(active_profile.id)
        other_stats = [st for pid, st in profiles_status.items() if pid != active_profile.id]

        if act_stat and other_stats:
            act_rem = act_stat["rem_sum"]
            act_exceeded = act_stat["is_exceeded"]
            act_near = bool(act_stat["safe_limit"] and (act_stat["new_sum"] / (act_stat["safe_limit"] + 1.0)) >= 0.8)

            non_exceeded_others = [st for st in other_stats if not st["is_exceeded"]]
            candidates = non_exceeded_others if non_exceeded_others else other_stats
            best_other = max(candidates, key=lambda st: st["rem_sum"])

            should_suggest = False
            if act_exceeded and not best_other["is_exceeded"]:
                should_suggest = True
            elif act_near and best_other["rem_sum"] > act_rem:
                should_suggest = True
            elif cod_val > 0 and best_other["rem_sum"] > (act_rem + 500.0):
                should_suggest = True

            if should_suggest:
                b_prof = best_other["profile"]
                suggested_profile = {
                    "id": b_prof.id,
                    "name": b_prof.name or b_prof.sender_name or "Користувач",
                    "rem_sum": best_other["rem_sum"],
                    "used_sum": best_other["used_sum"],
                    "safe_limit": best_other["safe_limit"],
                    "is_exceeded": best_other["is_exceeded"],
                }

    return {
        "profiles_status": profiles_status,
        "suggested_profile": suggested_profile,
        "active_profile": active_profile,
    }


async def evaluate_cod_limits(
    user_id: int,
    cod_val: float,
    user_np_client: NovaPoshtaClient,
    storage_manager: UserSettingsManager,
    eff_settings: Settings,
    editing_ref: Optional[str] = None,
) -> Dict[str, Any]:
    """Evaluate current monthly COD statistics, calculate new totals, and check limits."""
    if cod_val <= 0:
        return {
            "has_cod": False,
            "is_exceeded": False,
            "is_near": False,
            "summary_text": "",
            "warn_line": "",
            "base_sum": 0.0,
            "base_cnt": 0,
            "new_total_sum": 0.0,
            "new_total_cnt": 0,
            "safe_limit": None,
            "sum_limit": None,
            "count_limit": None,
            "rem_sum": 0.0,
            "rem_cnt": 0,
            "suggested_profile": None,
        }

    u_custom = storage_manager.get_user_settings(user_id)
    warning_enabled = getattr(u_custom, "cod_warning_enabled", True)
    sum_limit = u_custom.cod_monthly_limit_sum
    count_limit = u_custom.cod_monthly_limit_count

    try:
        stats = await user_np_client.get_monthly_cod_stats(
            user_phone=eff_settings.sender_phone,
            user_cp_ref=eff_settings.sender_counterparty_ref,
        )
        base_sum = stats.total_sum
        base_cnt = stats.total_count

        # If editing existing draft, subtract its previous COD amount to prevent double-counting
        if editing_ref and stats.items:
            for item in stats.items:
                if item.ref == editing_ref or item.int_doc_number == editing_ref:
                    base_sum = max(0.0, base_sum - item.cod_amount)
                    base_cnt = max(0, base_cnt - 1)
                    break

        new_total_sum = base_sum + cod_val
        new_total_cnt = base_cnt + 1

        safe_limit = (sum_limit - 1.0) if (sum_limit and sum_limit > 0) else None

        is_sum_exceeded = bool(sum_limit and sum_limit > 0 and new_total_sum >= sum_limit)
        is_sum_near = bool(
            sum_limit and sum_limit > 0 and not is_sum_exceeded and (new_total_sum / sum_limit) >= 0.8
        )
        rem_sum = max(0.0, (safe_limit - new_total_sum)) if safe_limit else 0.0
        exceeded_sum = int(new_total_sum - safe_limit) if (is_sum_exceeded and safe_limit) else 0

        is_cnt_exceeded = bool(count_limit and count_limit > 0 and new_total_cnt > count_limit)
        is_cnt_near = bool(
            count_limit and count_limit > 0 and not is_cnt_exceeded and new_total_cnt >= int(count_limit * 0.8)
        )
        rem_cnt = max(0, (count_limit - new_total_cnt)) if count_limit else 0
        exceeded_cnt = (new_total_cnt - count_limit) if is_cnt_exceeded else 0

        is_exceeded = bool((is_sum_exceeded or is_cnt_exceeded) and warning_enabled)
        is_near = bool((is_sum_near or is_cnt_near) and warning_enabled)

        # Build informative markdown block
        lines = []
        lines.append("📊 *Контроль місячного ліміту післяплати:*")
        lines.append(f"• Використано наразі: `{int(base_sum)} грн` ({base_cnt} ТТН)")
        lines.append(f"• Ця накладна: `+{int(cod_val)} грн`")

        if sum_limit and sum_limit > 0:
            if is_sum_exceeded:
                lines.append(
                    f"• 🚨 *УВАГА: Разом буде `{int(new_total_sum)} грн` — ПЕРЕТИН ВСТАНОВЛЕНОЇ МЕЖІ ({int(safe_limit)} грн) на {exceeded_sum} грн!*"
                )
            elif is_sum_near:
                lines.append(
                    f"• ⚠️ *Увага: Разом буде `{int(new_total_sum)} грн`* із {int(safe_limit)} грн (залишок: лише `{int(rem_sum)} грн`!)"
                )
            else:
                lines.append(
                    f"• ✅ *Разом буде:* `{int(new_total_sum)} грн` із {int(safe_limit)} грн (залишок безпечного ліміту: `{int(rem_sum)} грн`)"
                )
        else:
            lines.append(f"• Разом буде: `{int(new_total_sum)} грн` (_ліміт суми вимкнено_)")

        if count_limit and count_limit > 0:
            if is_cnt_exceeded:
                lines.append(
                    f"• 🚨 *Кількість ТТН ({new_total_cnt} шт) перевищить встановлений ліміт ({count_limit} шт) на {exceeded_cnt} шт!*"
                )
            elif is_cnt_near:
                lines.append(
                    f"• ⚠️ Кількість ТТН: {new_total_cnt} із {count_limit} шт (залишок: {rem_cnt} шт)"
                )
            else:
                lines.append(
                    f"• ✅ Кількість ТТН: {new_total_cnt} із {count_limit} шт"
                )

        # Check other profiles for recommendation
        all_profs_eval = await evaluate_all_profiles_cod_limits(
            user_id=user_id,
            cod_val=cod_val,
            storage_manager=storage_manager,
            eff_settings=eff_settings,
            editing_ref=editing_ref,
        )
        suggested_profile = all_profs_eval.get("suggested_profile")
        if suggested_profile:
            s_name = suggested_profile["name"]
            s_rem = int(suggested_profile["rem_sum"])
            s_used = int(suggested_profile["used_sum"])
            lines.append(
                f"\n💡 *Пропозиція:* У користувача *«{s_name}»* залишилося більше ліміту: `{s_rem} грн` (використано лише `{s_used} грн`).\n"
                f"Ви можете переключити відправника кнопкою нижче!"
            )

        info_block = "\n" + "\n".join(lines) + "\n"
        summary_text = "\n".join(lines)

        return {
            "has_cod": True,
            "is_exceeded": is_exceeded,
            "is_near": is_near,
            "summary_text": summary_text,
            "warn_line": info_block,
            "base_sum": base_sum,
            "base_cnt": base_cnt,
            "new_total_sum": new_total_sum,
            "new_total_cnt": new_total_cnt,
            "safe_limit": safe_limit,
            "sum_limit": sum_limit,
            "count_limit": count_limit,
            "exceeded_sum": exceeded_sum,
            "exceeded_cnt": exceeded_cnt,
            "rem_sum": rem_sum,
            "rem_cnt": rem_cnt,
            "suggested_profile": suggested_profile,
        }
    except Exception as e:
        logger.warning(f"Error evaluating COD limits: {e}")
        return {
            "has_cod": True,
            "is_exceeded": False,
            "is_near": False,
            "summary_text": "",
            "warn_line": "",
            "base_sum": 0.0,
            "base_cnt": 0,
            "new_total_sum": cod_val,
            "new_total_cnt": 1,
            "safe_limit": None,
            "sum_limit": None,
            "count_limit": None,
            "rem_sum": 0.0,
            "rem_cnt": 0,
            "suggested_profile": None,
        }


def register_handlers(

    settings: Settings,
    ai_extractor: AIExtractor,
    np_client: NovaPoshtaClient,
    storage_manager: UserSettingsManager,
):
    """Factory to inject dependencies into router handlers."""

    async def _build_waybill_preview_message(
        session_id: str,
        user_id: int,
    ) -> Tuple[str, InlineKeyboardMarkup]:
        """Centralized renderer for the waybill draft verification card and action keyboard."""
        session = PENDING_SESSIONS.get(session_id, {})
        eff_settings = storage_manager.get_effective_settings(user_id, settings)
        user_np_client = NovaPoshtaClient(eff_settings)

        parsed_info = session.get("parsed_info")
        city = session.get("city")
        dest_desc = session.get("destination_description") or "Не вказано"
        cargo_desc = session.get("cargo_description") or "Посилка"
        declared_val = session.get("declared_value", 500.0)
        cod_val = session.get("cod_amount", 0.0)
        cod_type = session.get("cod_payment_type", "cash")
        payer_type = session.get("payer_type", "Recipient")
        cargo_type = session.get("cargo_type", "Parcel")
        editing_ref = session.get("editing_draft_ref")

        cod_str = "❌ Немає" if cod_val <= 0 else f"{int(cod_val)} грн ({'Картка' if cod_type == 'card' else 'Готівка'})"

        eval_res = await evaluate_cod_limits(
            user_id=user_id,
            cod_val=cod_val,
            user_np_client=user_np_client,
            storage_manager=storage_manager,
            eff_settings=eff_settings,
            editing_ref=editing_ref,
        )
        warn_line = eval_res.get("warn_line", "")
        suggested_prof = eval_res.get("suggested_profile")

        profiles = storage_manager.get_sender_profiles(user_id)
        active_p = storage_manager.get_active_profile(user_id)
        has_multiple_profiles = len(profiles) > 1
        active_name = active_p.name if active_p else None
        sender_prefix = f"👤 *Відправник:* {active_name}\n" if (active_name and has_multiple_profiles) else ""
        departure_warn = (
            "⚠️ *Пункт відправки:* Не вказано місто або відділення! (/set_city та /set_warehouse)\n"
            if (not eff_settings.sender_city_ref or not eff_settings.sender_address_ref)
            else ""
        )

        rec_name = parsed_info.full_name if parsed_info else "Не вказано"
        rec_phone = parsed_info.phone if parsed_info else "Не вказано"
        city_name = city.description if hasattr(city, "description") else (getattr(city, "name", str(city)) if city else "Не вказано")

        card_text = (
            "📋 *Розпарсені дані отримувача для перевірки:*\n\n"
            f"{sender_prefix}"
            f"{departure_warn}"
            f"👤 *Отримувач:* {rec_name}\n"
            f"📞 *Телефон:* `{rec_phone}`\n"
            f"🏙 *Місто:* {city_name}\n"
            f"📦 *Пункт призначення:* {dest_desc}\n"
            f"📝 *Опис вантажу:* {cargo_desc}\n"
            f"💰 *Оціночна вартість:* {int(declared_val)} грн (Мін. 500 грн)\n"
            f"💵 *Накладений платіж:* {cod_str}\n"
            f"{warn_line}\n"
            "Перевірте дані та оберіть дію нижче:"
        )

        u_custom = storage_manager.get_user_settings(user_id)
        card_mask = u_custom.sender_card_mask

        reply_markup = get_confirmation_keyboard(
            payer_type=payer_type,
            cargo_type=cargo_type,
            declared_value=declared_val,
            cod_amount=cod_val,
            cod_payment_type=cod_type,
            sender_card_mask=card_mask,
            session_id=session_id,
            suggested_profile=suggested_prof,
            active_profile_name=active_name,
            has_multiple_profiles=has_multiple_profiles,
        )
        return card_text, reply_markup

    async def _save_user_card_and_resume_session(
        message: Message,
        user_id: int,
        raw_card_str: str,
    ):
        """Save user's payout card and resume in-progress waybill drafting session without disruption."""
        USER_CARD_WAITING.discard(user_id)
        clean_digits = "".join(filter(str.isdigit, raw_card_str))
        if len(clean_digits) == 16:
            masked_card = f"{clean_digits[:6]}******{clean_digits[-4:]}"
        else:
            masked_card = raw_card_str.strip()

        storage_manager.update_user_settings(user_id, sender_card_mask=masked_card)

        active_session_id = get_user_active_session_id(user_id)
        if active_session_id and active_session_id in PENDING_SESSIONS:
            session = PENDING_SESSIONS[active_session_id]
            session["cod_payment_type"] = "card"
            session["updated_at"] = datetime.datetime.now().timestamp()

            card_text, markup = await _build_waybill_preview_message(active_session_id, user_id)

            prev_chat_id = session.get("chat_id")
            prev_msg_id = session.get("message_id")
            if prev_chat_id and prev_msg_id:
                try:
                    await message.bot.edit_message_reply_markup(
                        chat_id=prev_chat_id,
                        message_id=prev_msg_id,
                        reply_markup=markup,
                    )
                except Exception:
                    pass

            sent_msg = await message.answer(
                f"✅ *Банківську картку для виплати наложки успішно збережено:* `{masked_card}`\n\n"
                f"{card_text}",
                parse_mode="Markdown",
                reply_markup=markup,
            )
            session["chat_id"] = sent_msg.chat.id
            session["message_id"] = sent_msg.message_id
        else:
            await message.answer(
                f"✅ *Банківську картку для виплати наложки успішно збережено:* `{masked_card}`",
                parse_mode="Markdown",
            )

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
            "5. **Чернетки та посилки:** Кнопки `📝 Мої чернетки (ТТН)` та `📦 Активні посилки` дозволяють переглядати та видаляти створені ТТН.\n"
            "6. **Відстеження будь-якої ТТН:** Надішліть номер накладної (14 цифр) у чат, скористайтеся кнопкою `🔍 Відстежити ТТН` або командою `/track НОМЕР`, щоб отримати детальний статус, маршрут, терміни доставки та фінансову інформацію."
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
    async def _render_users_dashboard(target_msg_or_callback: Any, user_id: int):
        """Render or update multi-user sender profiles dashboard with live COD balances."""
        profiles = storage_manager.get_sender_profiles(user_id)
        if not profiles:
            text = (
                "👥 *Керування користувачами (відправниками):*\n\n"
                "У вас ще не додано жодного профілю відправника Нової Пошти.\n\n"
                "💡 *Як додати відправника:*\n"
                "• Надішліть команду `/add_user ВАШ_КЛЮЧ [Назва]` для додавання користувача\n"
                "• Або надішліть `/set_np_key ВАШ_КЛЮЧ` для прив'язки основного ключа"
            )
            if isinstance(target_msg_or_callback, CallbackQuery):
                await target_msg_or_callback.message.edit_text(text, parse_mode="Markdown")
            else:
                await target_msg_or_callback.answer(text, parse_mode="Markdown")
            return

        eff_s = storage_manager.get_effective_settings(user_id, settings)
        all_eval = await evaluate_all_profiles_cod_limits(
            user_id=user_id,
            cod_val=0.0,
            storage_manager=storage_manager,
            eff_settings=eff_s,
        )
        balances_map = all_eval.get("profiles_status", {})
        active_profile = storage_manager.get_active_profile(user_id)
        active_id = active_profile.id if active_profile else None

        card_lines = ["👥 *Керування користувачами (відправниками Нової Пошти):*\n"]

        if active_profile:
            act_bal = balances_map.get(active_profile.id, {})
            used_s = int(act_bal.get("used_sum", 0))
            safe_l = int(act_bal.get("safe_limit", 29999))
            rem_s = int(act_bal.get("rem_sum", 29999))
            used_c = act_bal.get("used_cnt", 0)

            act_source = " _(в додатку)_" if active_profile.sender_warehouse_name else (" _(з сайту/API)_" if active_profile.api_sender_warehouse_name else "")
            act_city = active_profile.sender_city_name or active_profile.api_sender_city_name or "Місто не вказано"
            act_wh = active_profile.sender_warehouse_name or active_profile.api_sender_warehouse_name or "Відділення не вказано"

            if active_profile.alias and active_profile.sender_name and active_profile.alias != active_profile.sender_name:
                act_name_str = f"*{active_profile.alias}* — {active_profile.sender_name}"
            elif active_profile.alias:
                act_name_str = f"*{active_profile.alias}*"
            else:
                act_name_str = f"*{active_profile.name}*"

            card_lines.append("📌 *Поточний активний відправник:*")
            card_lines.append(f"✅ {act_name_str} (`{active_profile.sender_phone or 'тел. не вказано'}`)")
            card_lines.append(f"   📊 *Післяплата:* `{used_s} грн` із {safe_l} грн (вільно: `{rem_s} грн`) | {used_c} ТТН")
            card_lines.append(f"   🏙 *Відправка:* {act_city}, {act_wh}{act_source}\n")

        other_profs = [p for p in profiles if p.id != active_id]
        if other_profs:
            card_lines.append("📋 *Інші налаштовані користувачі:*")
            for p in other_profs:
                p_bal = balances_map.get(p.id, {})
                p_used = int(p_bal.get("used_sum", 0))
                p_safe = int(p_bal.get("safe_limit", 29999))
                p_rem = int(p_bal.get("rem_sum", 29999))
                p_cnt = p_bal.get("used_cnt", 0)

                o_source = " _(в додатку)_" if p.sender_warehouse_name else (" _(з сайту/API)_" if p.api_sender_warehouse_name else "")
                o_city = p.sender_city_name or p.api_sender_city_name or "Не вказано"
                o_wh = p.sender_warehouse_name or p.api_sender_warehouse_name or "Не вказано"

                if p.alias and p.sender_name and p.alias != p.sender_name:
                    p_name_str = f"*{p.alias}* — {p.sender_name}"
                elif p.alias:
                    p_name_str = f"*{p.alias}*"
                else:
                    p_name_str = f"*{p.name}*"

                card_lines.append(f"▫️ {p_name_str} (`{p.sender_phone or 'тел. не вказано'}`)")
                card_lines.append(f"   📊 *Післяплата:* `{p_used} грн` із {p_safe} грн (вільно: `{p_rem} грн`) | {p_cnt} ТТН")
                card_lines.append(f"   🏙 *Відправка:* {o_city}, {o_wh}{o_source}\n")

        card_lines.append("💡 *Адреса відправлення та пріоритет:*")
        card_lines.append("Адреса, налаштована в додатку (`/set_city`, `/set_warehouse`), має найвищий пріоритет над адресою з сайту Нової Пошти (API).")
        card_lines.append("Підтягнути збережену на сайті адресу можна кнопкою нижче або командою `/sync_address`.\n")

        card_lines.append("💡 *Оптимізація післяплати:*")
        card_lines.append("Безпечна межа накладеного платежу становить **29 999 грн** на людину в місяць. Бот автоматично пропонує перемкнути користувача, коли у поточного закінчується ліміт!")

        kb = get_users_management_keyboard(profiles, active_id, balances_map)
        card_text = "\n".join(card_lines)

        if isinstance(target_msg_or_callback, CallbackQuery):
            await target_msg_or_callback.message.edit_text(card_text, parse_mode="Markdown", reply_markup=kb)
        elif getattr(getattr(target_msg_or_callback, "from_user", None), "is_bot", None) is False:
            await target_msg_or_callback.answer(card_text, parse_mode="Markdown", reply_markup=kb)
        else:
            await target_msg_or_callback.edit_text(card_text, parse_mode="Markdown", reply_markup=kb)

    async def _handle_add_profile_with_key(message: Message, user_id: int, api_key: str, custom_name: str = ""):
        """Fetch sender info for new API key and add a new sender profile."""
        USER_ADD_PROFILE_WAITING.discard(user_id)
        status_msg = await message.answer(
            "⏳ *Перевірка API-ключа та підтягування даних контрагента з Нової Пошти...*",
            parse_mode="Markdown",
        )
        try:
            profile_data = await np_client.fetch_sender_profile(api_key)
            prof_name = (
                custom_name
                or profile_data.get("sender_name")
                or f"Користувач {len(storage_manager.get_sender_profiles(user_id)) + 1}"
            )
            new_id = f"prof_{int(datetime.datetime.now().timestamp())}"
            new_profile = SenderProfile(
                id=new_id,
                name=prof_name,
                nova_poshta_api_key=api_key,
                sender_counterparty_ref=profile_data.get("sender_counterparty_ref"),
                sender_contact_ref=profile_data.get("sender_contact_ref"),
                sender_city_ref=profile_data.get("sender_city_ref"),
                sender_city_name=profile_data.get("sender_city_name"),
                sender_address_ref=profile_data.get("sender_address_ref"),
                sender_warehouse_name=profile_data.get("sender_warehouse_name"),
                api_sender_city_ref=profile_data.get("api_sender_city_ref"),
                api_sender_city_name=profile_data.get("api_sender_city_name"),
                api_sender_address_ref=profile_data.get("api_sender_address_ref"),
                api_sender_warehouse_name=profile_data.get("api_sender_warehouse_name"),
                sender_phone=profile_data.get("sender_phone"),
                sender_name=profile_data.get("sender_name"),
                cod_monthly_limit_sum=30000.0,
                cod_monthly_limit_count=10,
                cod_warning_enabled=True,
            )
            added_p = storage_manager.add_sender_profile(user_id, new_profile, set_active=False)
            try:
                await status_msg.delete()
            except Exception:
                pass

            if added_p.sender_warehouse_name and added_p.sender_city_name:
                wh_line = f"🏙 *Відправка:* `{added_p.sender_city_name}, {added_p.sender_warehouse_name}` (автоматично підтягнуто)\n\n"
            else:
                wh_line = "🏙 Не забудьте перевірити або встановити місто (`/set_city Назва`) та відділення (`/set_warehouse Номер`) після перемикання на цей профіль.\n\n"

            await message.answer(
                f"✅ *Користувача «{prof_name}» успішно додано!*\n\n"
                f"👤 *ПІБ:* `{profile_data.get('sender_name')}`\n"
                f"📞 *Телефон:* `{profile_data.get('sender_phone') or 'Не вказано'}`\n"
                f"🔑 *API-ключ:* `{api_key[:6]}...{api_key[-4:]}`\n\n"
                f"{wh_line}"
                "Натисніть кнопку `👥 Користувачі`, щоб переглянути баланси або обрати активного користувача.",
                parse_mode="Markdown",
                reply_markup=get_main_reply_keyboard(),
            )
        except Exception as e:
            logger.error(f"Failed to add sender profile: {e}")
            await status_msg.edit_text(
                f"❌ *Помилка перевірки API-ключа:* {str(e)}", parse_mode="Markdown"
            )

    @router.message(Command("users"))
    @router.message(Command("profiles"))
    @router.message(Command("senders"))
    @router.message(F.text == "👥 Користувачі")
    async def cmd_users(message: Message):
        """Display sender profiles management dashboard with live COD limit balances."""
        clear_user_active_session(message.from_user.id)
        user_id = message.from_user.id
        profiles = storage_manager.get_sender_profiles(user_id)

        if not profiles:
            await message.answer(
                "👥 *Керування користувачами (відправниками):*\n\n"
                "У вас ще не додано жодного профілю відправника Нової Пошти.\n\n"
                "💡 *Як додати відправника:*\n"
                "• Надішліть команду `/add_user ВАШ_КЛЮЧ [Назва]` для додавання користувача\n"
                "• Або надішліть команду `/set_np_key ВАШ_КЛЮЧ` для налаштування основного профілю",
                parse_mode="Markdown",
                reply_markup=get_main_reply_keyboard(),
            )
            return

        status_msg = await message.answer(
            "⏳ *Отримання актуальних балансів накладеного платежу для всіх користувачів...*",
            parse_mode="Markdown",
        )
        await _render_users_dashboard(status_msg, user_id)

    @router.message(Command("add_user"))
    async def cmd_add_user(message: Message):
        """Add a new sender user profile via command."""
        clear_user_active_session(message.from_user.id)
        user_id = message.from_user.id
        parts = message.text.split(maxsplit=2)
        if len(parts) < 2:
            USER_ADD_PROFILE_WAITING.add(user_id)
            await message.answer(
                "➕ *Додавання нового користувача (відправника Нової Пошти):*\n\n"
                "⚠️ *Використання:* `/add_user ВАШ_API_КЛЮЧ_НП [Назва/Ярлик]`\n\n"
                "Або просто надішліть API-ключ Нової Пошти у наступному повідомленні.",
                parse_mode="Markdown",
            )
            return

        api_key = parts[1].strip()
        custom_name = parts[2].strip() if len(parts) > 2 else ""
        await _handle_add_profile_with_key(message, user_id, api_key, custom_name)

    @router.message(Command("set_alias"))
    @router.message(Command("set_name"))
    @router.message(Command("rename_user"))
    async def cmd_set_alias(message: Message):
        """Set a pseudonym / nickname for a sender profile."""
        clear_user_active_session(message.from_user.id)
        user_id = message.from_user.id
        parts = message.text.split(maxsplit=1)
        active_p = storage_manager.get_active_profile(user_id)
        if not active_p:
            await message.answer("⚠️ Немає налаштованих користувачів.")
            return

        if len(parts) < 2:
            USER_RENAME_PROFILE_WAITING[user_id] = active_p.id
            await message.answer(
                f"✏️ *Встановлення псевдоніма для активного користувача ({active_p.name}):*\n\n"
                f"📄 *Офіційне ПІБ НП:* `{active_p.sender_name or active_p.name}`\n"
                f"📞 *Телефон:* `{active_p.sender_phone or 'не вказано'}`\n\n"
                "Надішліть новий псевдонім у чат (наприклад: `/set_alias Водафон` або просто надішліть назву у відповідь).\n\n"
                "💡 _Щоб повернути оригінальне ПІБ, надішліть_ `/reset_alias`",
                parse_mode="Markdown",
            )
            return

        new_alias = parts[1].strip()
        if len(new_alias) > 40:
            new_alias = new_alias[:40]
        storage_manager.rename_sender_profile(user_id, active_p.id, new_alias)
        await message.answer(
            f"✅ *Псевдонім для активного користувача успішно встановлено:* «{new_alias}»",
            parse_mode="Markdown",
        )
        await _render_users_dashboard(message, user_id)

    @router.message(Command("reset_alias"))
    async def cmd_reset_alias(message: Message):
        """Reset pseudonym for the active sender profile back to official full name."""
        clear_user_active_session(message.from_user.id)
        user_id = message.from_user.id
        active_p = storage_manager.get_active_profile(user_id)
        if not active_p:
            await message.answer("⚠️ Немає налаштованих користувачів.")
            return

        orig_name = active_p.sender_name or "Користувач"
        storage_manager.update_sender_profile(user_id, active_p.id, name=orig_name, alias=None)
        await message.answer(
            f"🔄 *Псевдонім для активного користувача скинуто до:* «{orig_name}»",
            parse_mode="Markdown",
        )
        await _render_users_dashboard(message, user_id)

    @router.callback_query(UserProfileCallback.filter())
    async def process_user_profile_callback(callback: CallbackQuery, callback_data: UserProfileCallback):
        """Handle inline actions for sender user profiles."""
        user_id = callback.from_user.id
        action = callback_data.action
        profile_id = callback_data.profile_id

        if action == "select":
            switched = storage_manager.set_active_profile(user_id, profile_id)
            if switched:
                await callback.answer(f"✅ Активним обрано «{switched.name}»!")
            else:
                await callback.answer("❌ Профіль не знайдено", show_alert=True)
            await _render_users_dashboard(callback, user_id)
            return

        if action == "refresh":
            await callback.answer("🔄 Оновлення балансів...")
            await _render_users_dashboard(callback, user_id)
            return

        if action == "sync_address":
            await callback.answer("⏳ Запит адреси з сайту Нової Пошти...")
            try:
                eff_settings = storage_manager.get_effective_settings(user_id, settings)
                user_np_client = NovaPoshtaClient(eff_settings)
                addr_info = await user_np_client.fetch_sender_address_from_api(
                    counterparty_ref=eff_settings.sender_counterparty_ref
                )
                city_ref = addr_info.get("sender_city_ref")
                addr_ref = addr_info.get("sender_address_ref")
                city_name = addr_info.get("sender_city_name")
                wh_name = addr_info.get("sender_warehouse_name")

                if city_ref and addr_ref:
                    storage_manager.update_user_settings(
                        user_id,
                        api_sender_city_ref=city_ref,
                        api_sender_city_name=city_name,
                        api_sender_address_ref=addr_ref,
                        api_sender_warehouse_name=wh_name,
                        sender_city_ref=city_ref,
                        sender_city_name=city_name,
                        sender_address_ref=addr_ref,
                        sender_warehouse_name=wh_name,
                    )
                    await callback.message.answer(
                        "✅ *Адресу відправлення успішно підтягнуто з кабінету сайту!*\n\n"
                        f"🏙 *Місто:* `{city_name or city_ref}`\n"
                        f"🏢 *Відділення/Адреса:* `{wh_name or addr_ref}`\n\n"
                        "💡 *Пріоритет:* Адреса, налаштована в додатку через `/set_city` та `/set_warehouse`, завжди має пріоритет над адресою з сайту.",
                        parse_mode="Markdown",
                    )
                else:
                    await callback.message.answer(
                        "⚠️ *У вашому кабінеті Нової Пошти не знайдено налаштованої адреси відправника.*\n\n"
                        "Вкажіть її безпосередньо в додатку: `/set_city Назва` та `/set_warehouse Номер`.",
                        parse_mode="Markdown",
                    )
            except Exception as e:
                logger.error(f"Error syncing sender address: {e}")
                await callback.message.answer(f"❌ *Помилка підтягування адреси з API:* {str(e)}", parse_mode="Markdown")

            await _render_users_dashboard(callback, user_id)
            return

        if action == "rename_prompt":
            profiles = storage_manager.get_sender_profiles(user_id)
            if not profiles:
                await callback.answer("❌ Профілів не знайдено", show_alert=True)
                return
            if len(profiles) == 1:
                target_p = profiles[0]
                USER_RENAME_PROFILE_WAITING[user_id] = target_p.id
                await callback.message.answer(
                    f"✏️ *Зміна псевдоніма для «{target_p.name}»:*\n\n"
                    f"Введіть бажаний короткий псевдонім (наприклад: _Основний_, _ФОП_, _Водафон_).\n\n"
                    f"💡 Щоб повернути оригінальне ПІБ, надішліть `/reset_alias`, а щоб скасувати — `/cancel`.",
                    parse_mode="Markdown",
                )
                await callback.answer()
                return

            rename_kb = get_rename_profile_selection_keyboard(profiles)
            await callback.message.edit_text(
                "✏️ *Оберіть користувача, якому бажаєте надати або змінити псевдонім:*\n\n"
                "💡 Псевдоніми дозволяють легко розрізняти акаунти з однаковими прізвищами на кнопках та в повідомленнях.",
                parse_mode="Markdown",
                reply_markup=rename_kb,
            )
            await callback.answer()
            return

        if action == "rename_target":
            target_p = storage_manager.get_sender_profile(user_id, profile_id)
            if not target_p:
                await callback.answer("❌ Профіль не знайдено", show_alert=True)
                return
            USER_RENAME_PROFILE_WAITING[user_id] = target_p.id
            curr_alias = target_p.alias or target_p.name
            await callback.message.answer(
                f"✏️ *Введіть новий псевдонім для:* «{curr_alias}»\n"
                f"_(Офіційне ПІБ: {target_p.sender_name or target_p.name}, тел: {target_p.sender_phone or '—'})_\n\n"
                f"Наприклад: _Основний_, _ФОП_, _Водафон_, _Склад_.\n"
                f"💡 Щоб скинути псевдонім до офіційного ПІБ, надішліть `/reset_alias`, а щоб скасувати — `/cancel`.",
                parse_mode="Markdown",
            )
            await callback.answer()
            return

        if action == "delete_prompt":
            profiles = storage_manager.get_sender_profiles(user_id)
            active_p = storage_manager.get_active_profile(user_id)
            active_id = active_p.id if active_p else None
            del_kb = get_profile_delete_keyboard(profiles, active_id)
            await callback.message.edit_text(
                "🗑 *Оберіть користувача, якого бажаєте видалити:*\n\n"
                "_(Активного користувача видалити не можна; спочатку перемкніться на іншого)_",
                parse_mode="Markdown",
                reply_markup=del_kb,
            )
            await callback.answer()
            return

        if action == "delete":
            deleted = storage_manager.delete_sender_profile(user_id, profile_id)
            if deleted:
                await callback.answer("🗑 Користувача успішно видалено!")
            else:
                await callback.answer("❌ Не вдалося видалити (не можна видалити єдиного користувача)", show_alert=True)
            await _render_users_dashboard(callback, user_id)
            return

        if action == "add":
            USER_ADD_PROFILE_WAITING.add(user_id)
            await callback.message.answer(
                "➕ *Додавання нового користувача (відправника Нової Пошти):*\n\n"
                "Надішліть ваш API-ключ Нової Пошти для цього користувача прямо в чат (або введіть команду `/add_user КЛЮЧ [Назва]`).\n\n"
                "💡 Бот автоматично перевірить ключ, підтягне ПІБ, телефон і контрагента з бази Нової Пошти.",
                parse_mode="Markdown",
            )
            await callback.answer()
            return

    async def _send_settings_card(target, user_id: int, user_full_name: str):
        """Render and send or edit user profile settings card."""
        is_callback = isinstance(target, CallbackQuery)
        msg = target.message if is_callback else target

        u_settings = storage_manager.get_user_settings(user_id)
        is_cfg = storage_manager.is_user_configured(user_id)

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

        active_prof = storage_manager.get_active_profile(user_id)
        profiles = storage_manager.get_sender_profiles(user_id)
        active_prof_name = active_prof.name if active_prof else "Основний"
        prof_cnt_str = f"{len(profiles)} налаштовано" if profiles else "0"

        sum_lim_str = f"`{int(u_settings.cod_monthly_limit_sum)} грн`" if u_settings.cod_monthly_limit_sum else "_Без ліміту_"
        cnt_lim_str = f"`{u_settings.cod_monthly_limit_count} шт`" if u_settings.cod_monthly_limit_count else "_Без ліміту_"

        card = (
            f"⚙️ *Персональний профіль користувача:* [{user_full_name}]\n\n"
            f"📊 *Загальний статус:* {status_icon}\n\n"
            "📮 *Дані Нової Пошти:*\n"
            f"• 👥 *Активний відправник:* «{active_prof_name}» (всього: {prof_cnt_str}) | `/users`\n"
            f"• 💳 *Картка клієнта:* `/client_card`\n"
            f"• 🔑 *API-ключ НП:* {masked_np_key}\n"
            f"• 👤 *ПІБ відправника:* `{u_settings.sender_name or 'Не підтягнуто'}`\n"
            f"• 📞 *Телефон:* `{u_settings.sender_phone or 'Не підтягнуто'}`\n"
            f"• 🏙 *Місто відправки:* `{u_settings.sender_city_name or 'Не вказано'}`\n"
            f"• 📦 *Відділення відправки:* `{u_settings.sender_warehouse_name or 'Не вказано'}`\n"
            f"• 💰 *Місячний ліміт наложки:* {sum_lim_str} | {cnt_lim_str}\n\n"
            "🧠 *Дані AI-провайдера:*\n"
            f"• 🔑 *AI API-ключ:* {masked_ai_key}\n"
            f"• 🌐 *URL API:* `{ai_url_display}`\n"
            f"• 🤖 *Модель AI:* `{ai_model_display}`\n\n"
            "💡 *Команди для керування:*\n"
            "• `/client_card` — 💳 відкрити картку клієнта та штрих-код\n"
            "• `/users` — керування користувачами та лімітами післяплати\n"
            "• `/add_user КЛЮЧ [Назва]` — додати нового користувача\n"
            "• `/set_np_key ВАШ_КЛЮЧ` — прив'язати API-ключ НП\n"
            "• `/set_ai_key ВАШ_КЛЮЧ` — прив'язати AI API-ключ\n"
            "• `/set_ai_url URL` — змінити URL AI-провайдера\n"
            "• `/set_ai_model MODEL` — змінити модель AI\n"
            "• `/set_name ПІБ` — змінити ПІБ відправника\n"
            "• `/set_city НазваМіста` — змінити місто відправки\n"
            "• `/set_warehouse Номер` — змінити відділення відправки\n"
            "• `/set_cod_limit СУМА` — ліміт суми наложки на місяць\n"
            "• `/set_cod_count КІЛЬКІСТЬ` — ліміт кількості посилок на місяць\n"
            "• `/cod` — звіт та статистика накладеного платежу"
        )
        if is_callback:
            try:
                await msg.edit_text(card, parse_mode="Markdown", reply_markup=get_settings_keyboard())
                await target.answer()
                return
            except Exception:
                pass

        await msg.answer(card, parse_mode="Markdown", reply_markup=get_settings_keyboard())
        if is_callback:
            await target.answer()

    @router.message(Command("settings"))
    @router.message(Command("profile"))
    @router.message(F.text == "⚙️ Налаштування")
    async def cmd_settings(message: Message):
        """Show current user configuration status."""
        clear_user_active_session(message.from_user.id)
        await _send_settings_card(message, message.from_user.id, message.from_user.full_name)

    async def _render_client_card(
        target_msg_or_callback,
        user_id: int,
        mode: str = "card",
        profile_id: str = "active",
    ):
        """Render and send or edit Nova Poshta client loyalty card image with scannable barcode."""
        is_callback = isinstance(target_msg_or_callback, CallbackQuery)
        message = target_msg_or_callback.message if is_callback else target_msg_or_callback

        prof = None
        if profile_id and profile_id != "active":
            for p in storage_manager.get_sender_profiles(user_id):
                if p.id == profile_id:
                    prof = p
                    break
        if not prof:
            prof = storage_manager.get_active_profile(user_id)

        u_settings = storage_manager.get_user_settings(user_id)
        api_key = (
            (prof.nova_poshta_api_key if prof else None)
            or u_settings.nova_poshta_api_key
            or settings.nova_poshta_api_key
        )

        full_name = (prof.sender_name if prof else None) or u_settings.sender_name or ""
        phone = (prof.sender_phone if prof else None) or u_settings.sender_phone or settings.sender_phone or ""
        loyalty_card = ""
        user_login = ""

        if api_key:
            try:
                loyalty_data = await np_client.get_loyalty_info(api_key_override=api_key)
                if loyalty_data:
                    full_name = loyalty_data.get("full_name") or full_name
                    phone = loyalty_data.get("phone") or phone
                    loyalty_card = loyalty_data.get("loyalty_card") or ""
                    user_login = loyalty_data.get("user_login") or ""
            except Exception as e:
                logger.warning(f"Error getting loyalty data for client card: {e}")

        clean_phone = "".join(ch for ch in phone if ch.isdigit())
        card_id_str = loyalty_card or user_login

        if mode == "phone" or not card_id_str:
            barcode_val = clean_phone or "0000000000"
            barcode_lbl = f"+{clean_phone}" if clean_phone else barcode_val
            current_mode = "phone"
        else:
            barcode_val = card_id_str
            barcode_lbl = card_id_str
            current_mode = "card"

        card_bytes = generate_client_card_image(
            full_name=full_name or "Клієнт Нової Пошти",
            phone=phone,
            card_number=card_id_str,
            barcode_data=barcode_val,
            barcode_label=barcode_lbl,
        )

        prof_eff_id = prof.id if prof else "active"
        kb = get_client_card_keyboard(profile_id=prof_eff_id, current_mode=current_mode)
        barcode_type_desc = "номер телефону" if current_mode == "phone" else "картка лояльності / CID"
        caption = (
            f"💳 *Картка клієнта Нової Пошти*\n\n"
            f"👤 *Власник:* `{full_name or 'Не підтягнуто'}`\n"
            f"📞 *Телефон:* `{phone or 'Не вказано'}`\n"
        )
        if card_id_str:
            caption += f"🪪 *Номер картки / CID:* `{card_id_str}`\n"
        caption += (
            f"📊 *Штрих-код:* `{barcode_val}` ({barcode_type_desc})\n\n"
            "💡 _Покажіть цей штрих-код оператору у відділенні для швидкого зчитування сканером або скористайтеся терміналом самообслуговування._"
        )

        photo_file = BufferedInputFile(card_bytes, filename=f"client_card_{user_id}.png")

        if is_callback:
            try:
                await message.edit_media(
                    media=InputMediaPhoto(media=photo_file, caption=caption, parse_mode="Markdown"),
                    reply_markup=kb,
                )
                await target_msg_or_callback.answer()
                return
            except Exception as ex:
                logger.debug(f"edit_media failed (falling back to answer_photo): {ex}")

        await message.answer_photo(
            photo=photo_file,
            caption=caption,
            parse_mode="Markdown",
            reply_markup=kb,
        )
        if is_callback:
            await target_msg_or_callback.answer()

    @router.message(Command("client_card"))
    @router.message(Command("card"))
    @router.message(F.text == "💳 Картка клієнта")
    async def cmd_client_card(message: Message):
        """Show scannable Nova Poshta digital client card."""
        status_msg = await message.answer("⏳ *Генерація картки клієнта...*", parse_mode="Markdown")
        await _render_client_card(message, message.from_user.id, mode="card", profile_id="active")
        try:
            await status_msg.delete()
        except Exception:
            pass

    @router.callback_query(ClientCardCallback.filter())
    async def process_client_card_callback(callback: CallbackQuery, callback_data: ClientCardCallback):
        """Handle inline actions under the client card image."""
        user_id = callback.from_user.id
        action = callback_data.action
        profile_id = callback_data.profile_id
        mode = callback_data.mode

        if action == "settings":
            await callback.answer()
            await _send_settings_card(callback, user_id, callback.from_user.full_name)
            return

        if action in ("switch_phone", "switch_card", "refresh", "show"):
            await callback.answer("🔄 Оновлення штрих-коду...")
            target_mode = "phone" if action == "switch_phone" else ("card" if action == "switch_card" else mode)
            await _render_client_card(callback, user_id, mode=target_mode, profile_id=profile_id)
            return


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
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer("⚠️ *Використання:* `/set_city НазваМіста` (наприклад, `/set_city Київ`)", parse_mode="Markdown")
            return

        city_query = normalize_apostrophes(parts[1]).strip()
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
                sender_address_ref="",
                sender_warehouse_name="",
            )

            active_session_id = get_user_active_session_id(message.from_user.id)
            if active_session_id and active_session_id in PENDING_SESSIONS:
                await status_msg.edit_text(
                    f"✅ *Місто відправника успішно збережено:* `{city.description}`\n\n"
                    "Тепер вкажіть номер вашого відділення відправки: `/set_warehouse Номер`\n\n"
                    "💡 *Ваша поточна чернетка збережена!* Після вказання відділення створення ТТН буде продовжено автоматично.",
                    parse_mode="Markdown",
                )
            else:
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

            active_session_id = get_user_active_session_id(message.from_user.id)
            if active_session_id and active_session_id in PENDING_SESSIONS:
                session = PENDING_SESSIONS[active_session_id]
                session["updated_at"] = datetime.datetime.now().timestamp()
                card_text, markup = await _build_waybill_preview_message(active_session_id, message.from_user.id)

                prev_chat_id = session.get("chat_id")
                prev_msg_id = session.get("message_id")
                if prev_chat_id and prev_msg_id:
                    try:
                        await message.bot.edit_message_reply_markup(
                            chat_id=prev_chat_id,
                            message_id=prev_msg_id,
                            reply_markup=markup,
                        )
                    except Exception:
                        pass

                sent_msg = await message.answer(
                    f"✅ *Відділення відправника збережено:* `{wh.description}`\n\n"
                    f"{card_text}",
                    parse_mode="Markdown",
                    reply_markup=markup,
                )
                session["chat_id"] = sent_msg.chat.id
                session["message_id"] = sent_msg.message_id
                try:
                    await status_msg.delete()
                except Exception:
                    pass
            else:
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
                sender_city_name=profile.get("sender_city_name") or None,
                sender_address_ref=profile["sender_address_ref"] or None,
                sender_warehouse_name=profile.get("sender_warehouse_name") or None,
                api_sender_city_ref=profile.get("api_sender_city_ref") or None,
                api_sender_city_name=profile.get("api_sender_city_name") or None,
                api_sender_address_ref=profile.get("api_sender_address_ref") or None,
                api_sender_warehouse_name=profile.get("api_sender_warehouse_name") or None,
                sender_phone=profile["sender_phone"],
                sender_name=profile["sender_name"],
            )

            wh_text = ""
            if profile.get("sender_warehouse_name") and profile.get("sender_city_name"):
                wh_text = (
                    f"🏙 *Відділення відправки:* `{profile['sender_city_name']}, {profile['sender_warehouse_name']}` (підтягнуто з кабінету сайту)\n\n"
                    "💡 Ви можете будь-коли змінити його за допомогою `/set_city` та `/set_warehouse` (налаштовані в додатку дані мають пріоритет).\n\n"
                )
            else:
                wh_text = (
                    "🏙 Тепер вкажіть місто відправки командою `/set_city НазваМіста`\n"
                    "📦 Та номер відділення/поштомату: `/set_warehouse Номер`\n\n"
                )

            await status_msg.edit_text(
                "✅ *API-ключ Нової Пошти та профіль відправника успішно підв'язано!*\n\n"
                f"👤 *Відправник:* `{profile['sender_name']}`\n"
                f"📞 *Телефон:* `{profile['sender_phone'] or 'Не вказано'}`\n"
                f"🔑 *Ключ:* `{api_key[:6]}...{api_key[-4:]}`\n\n"
                f"{wh_text}"
                "🎉 Надішліть дані отримувача у повідомленні для створення ТТН!",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Failed to set Nova Poshta API key: {e}")
            await status_msg.edit_text(
                f"❌ *Помилка перевірки ключа Нової Пошти:* {str(e)}", parse_mode="Markdown"
            )

    @router.message(Command("sync_address"))
    @router.message(Command("sync_sender_address"))
    async def cmd_sync_address(message: Message):
        """Sync departure address from Nova Poshta website/API cabinet."""
        user_id = message.from_user.id
        status_msg = await message.answer(
            "⏳ *Запит адреси відправника з кабінету Нової Пошти...*", parse_mode="Markdown"
        )
        try:
            eff_settings = storage_manager.get_effective_settings(user_id, settings)
            user_np_client = NovaPoshtaClient(eff_settings)
            addr_info = await user_np_client.fetch_sender_address_from_api(
                counterparty_ref=eff_settings.sender_counterparty_ref
            )

            city_ref = addr_info.get("sender_city_ref")
            addr_ref = addr_info.get("sender_address_ref")
            city_name = addr_info.get("sender_city_name")
            wh_name = addr_info.get("sender_warehouse_name")

            if not city_ref or not addr_ref:
                await status_msg.edit_text(
                    "⚠️ *У вашому кабінеті Нової Пошти не знайдено налаштованої адреси відправлення.*\n\n"
                    "Ви можете налаштувати її безпосередньо у боті:\n"
                    "1️⃣ `/set_city НазваМіста` (наприклад, `/set_city Київ`)\n"
                    "2️⃣ `/set_warehouse Номер` (наприклад, `/set_warehouse 1`)\n\n"
                    "💡 *Налаштована в додатку адреса має пріоритет над API.*",
                    parse_mode="Markdown",
                )
                return

            storage_manager.update_user_settings(
                user_id,
                api_sender_city_ref=city_ref,
                api_sender_city_name=city_name,
                api_sender_address_ref=addr_ref,
                api_sender_warehouse_name=wh_name,
                sender_city_ref=city_ref,
                sender_city_name=city_name,
                sender_address_ref=addr_ref,
                sender_warehouse_name=wh_name,
            )

            active_session_id = get_user_active_session_id(user_id)
            if active_session_id and active_session_id in PENDING_SESSIONS:
                card_text, markup = await _build_waybill_preview_message(active_session_id, user_id)
                sent_msg = await message.answer(
                    "✅ *Адресу відправлення успішно підтягнуто з сайту Нової Пошти!*\n\n"
                    f"🏙 *Місто:* `{city_name or city_ref}`\n"
                    f"🏢 *Відділення/Адреса:* `{wh_name or addr_ref}`\n\n"
                    f"{card_text}",
                    parse_mode="Markdown",
                    reply_markup=markup,
                )
                session = PENDING_SESSIONS[active_session_id]
                session["chat_id"] = sent_msg.chat.id
                session["message_id"] = sent_msg.message_id
                try:
                    await status_msg.delete()
                except Exception:
                    pass
            else:
                await status_msg.edit_text(
                    "✅ *Адресу відправлення успішно синхронізовано з кабінетом Нової Пошти!*\n\n"
                    f"🏙 *Місто:* `{city_name or city_ref}`\n"
                    f"🏢 *Відділення/Адреса:* `{wh_name or addr_ref}`\n\n"
                    "💡 *Пріоритет:* Якщо ви вкажете іншу адресу командами `/set_city` та `/set_warehouse`, вона матиме вищий пріоритет над адресою з сайту.",
                    parse_mode="Markdown",
                )
        except Exception as e:
            logger.error(f"Failed to sync sender address from API: {e}")
            await status_msg.edit_text(
                f"❌ *Помилка синхронізації адреси з сайту:* {str(e)}", parse_mode="Markdown"
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
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                "⚠️ *Використання:* `/set_card 414932******1234` (вкажіть маску або номер вашої банківської картки)",
                parse_mode="Markdown",
            )
            return

        card_str = parts[1].strip()
        await _save_user_card_and_resume_session(message, message.from_user.id, card_str)

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

    @router.message(Command("cod"))
    @router.message(Command("stats"))
    @router.message(F.text == "💰 Накладений платіж")
    @router.message(F.text == "💰 Наложка")
    async def cmd_cod_dashboard(message: Message):
        """Show monthly Cash On Delivery dashboard with statistics, limits and progress."""
        clear_user_active_session(message.from_user.id)
        if not await ensure_user_configured(message):
            return

        user_id = message.from_user.id
        eff_settings = storage_manager.get_effective_settings(user_id, settings)
        user_np_client = NovaPoshtaClient(eff_settings)
        user_settings = storage_manager.get_user_settings(user_id)

        status_msg = await message.answer("⏳ *Розрахунок статистики накладеного платежу за поточний місяць...*", parse_mode="Markdown")

        try:
            stats = await user_np_client.get_monthly_cod_stats(
                user_phone=eff_settings.sender_phone,
                user_cp_ref=eff_settings.sender_counterparty_ref,
            )
            text = format_cod_dashboard(stats, user_settings)
            await status_msg.edit_text(
                text,
                parse_mode="Markdown",
                reply_markup=get_cod_stats_keyboard(),
            )
        except Exception as e:
            logger.error(f"Error fetching COD stats: {e}", exc_info=True)
            await status_msg.edit_text(f"❌ *Помилка отримання статистики:* {str(e)}", parse_mode="Markdown")

    @router.message(Command("set_cod_limit"))
    async def cmd_set_cod_limit(message: Message):
        """Set user's custom monthly COD sum limit."""
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                "⚠️ *Використання:* `/set_cod_limit СУМА_ГРН` (наприклад, `/set_cod_limit 50000` або `/set_cod_limit 0` щоб вимкнути)",
                parse_mode="Markdown",
            )
            return

        try:
            val = float(parts[1].strip())
            new_limit = val if val > 0 else None
            storage_manager.update_user_settings(message.from_user.id, cod_monthly_limit_sum=new_limit)
            limit_str = f"`{int(val)} грн`" if new_limit else "Вимкнено"
            await message.answer(
                f"✅ *Місячний ліміт суми накладеного платежу встановлено:* {limit_str}",
                parse_mode="Markdown",
                reply_markup=get_main_reply_keyboard(),
            )
        except ValueError:
            await message.answer("❌ *Будь ласка, вкажіть числове значення суми в гривнях.*", parse_mode="Markdown")

    @router.message(Command("set_cod_count"))
    async def cmd_set_cod_count(message: Message):
        """Set user's custom monthly COD parcels count limit."""
        clear_user_active_session(message.from_user.id)
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip().isdigit():
            await message.answer(
                "⚠️ *Використання:* `/set_cod_count КІЛЬКІСТЬ` (наприклад, `/set_cod_count 15` або `/set_cod_count 0` щоб вимкнути)",
                parse_mode="Markdown",
            )
            return

        val = int(parts[1].strip())
        new_limit = val if val > 0 else None
        storage_manager.update_user_settings(message.from_user.id, cod_monthly_limit_count=new_limit)
        limit_str = f"`{val} посилок`" if new_limit else "Вимкнено"
        await message.answer(
            f"✅ *Місячний ліміт кількості посилок з наложкою встановлено:* {limit_str}",
            parse_mode="Markdown",
            reply_markup=get_main_reply_keyboard(),
        )

    async def handle_track_document(
        message: Message,
        doc_number: str,
        user_id: int,
        status_msg: Optional[Message] = None,
    ):
        """Track express waybill and display formatted tracking card."""
        clean_num = "".join(filter(str.isdigit, str(doc_number)))
        if not clean_num or len(clean_num) < 11:
            err_text = (
                "❌ *Некоректний номер ТТН.*\n"
                "Номер накладної Нової Пошти містить 14 цифр (наприклад, `20450123456789`)."
            )
            if status_msg:
                await status_msg.edit_text(err_text, parse_mode="Markdown")
            else:
                await message.answer(err_text, parse_mode="Markdown")
            return

        if not status_msg:
            status_msg = await message.answer(
                f"🔍 *Отримання даних по ТТН `{clean_num}` з Нової Пошти...*",
                parse_mode="Markdown",
            )
        else:
            await status_msg.edit_text(
                f"🔍 *Отримання даних по ТТН `{clean_num}` з Нової Пошти...*",
                parse_mode="Markdown",
            )

        eff_settings = storage_manager.get_effective_settings(user_id, settings)
        user_np_client = NovaPoshtaClient(eff_settings)

        try:
            tracking_info = await user_np_client.track_document(clean_num)
        except Exception as e:
            logger.error(f"Error tracking document {clean_num}: {e}")
            tracking_info = None

        if not tracking_info:
            await status_msg.edit_text(
                f"❌ *Помилка запиту до API Нової Пошти або ТТН не знайдено.*\n"
                f"Номер: `{clean_num}`\n\n"
                f"💡 Перевірте правильність введеного номера або спробуйте пізніше.",
                parse_mode="Markdown",
                reply_markup=get_tracking_keyboard(clean_num),
            )
            return

        card_text = format_tracking_card(tracking_info)
        await status_msg.edit_text(
            card_text,
            parse_mode="Markdown",
            reply_markup=get_tracking_keyboard(clean_num),
        )

    @router.message(Command("track"))
    @router.message(Command("tracking"))
    @router.message(F.text == "🔍 Відстежити ТТН")
    async def cmd_track(message: Message):
        """Track express waybill by number or prompt user to enter one."""
        user_id = message.from_user.id
        clear_user_active_session(user_id)

        # Check if argument was passed with command: /track 20450123456789
        parts = message.text.strip().split()
        if len(parts) > 1:
            raw_target = parts[1]
            ttn = extract_ttn_from_text(raw_target) or "".join(filter(str.isdigit, raw_target))
            if ttn and len(ttn) >= 11:
                USER_TRACKING_WAITING.discard(user_id)
                await handle_track_document(message, ttn, user_id)
                return

        USER_TRACKING_WAITING.add(user_id)
        prompt_text = (
            "🔍 *Відстеження експрес-накладної (ТТН) Нової Пошти*\n\n"
            "Надішліть номер накладної (14 цифр) у повідомленні.\n"
            "Наприклад:\n"
            "• `20450123456789`\n"
            "• `2045 0123 4567 89`\n\n"
            "💡 _Ви також можете просто скинути номер накладної у будь-який момент без натискання кнопок!_"
        )
        await message.answer(
            prompt_text,
            parse_mode="Markdown",
            reply_markup=get_main_reply_keyboard(),
        )

    async def send_waybill_barcode(target_msg_or_callback, doc_number: str):
        """Generate and send Code128 barcode photo for a waybill."""
        clean_num = "".join(filter(str.isdigit, str(doc_number)))
        is_callback = isinstance(target_msg_or_callback, CallbackQuery) or (
            hasattr(target_msg_or_callback, "message")
            and not hasattr(target_msg_or_callback, "chat")
        )

        if not clean_num or len(clean_num) < 11:
            err_text = "❌ *Некоректний номер ТТН для генерації штрих-коду.*"
            if is_callback and hasattr(target_msg_or_callback, "answer"):
                try:
                    await target_msg_or_callback.answer("❌ Некоректний номер ТТН.", show_alert=True)
                except Exception:
                    pass
            elif hasattr(target_msg_or_callback, "answer"):
                try:
                    await target_msg_or_callback.answer(err_text, parse_mode="Markdown")
                except Exception:
                    pass
            return

        try:
            barcode_bytes = generate_code128_barcode(clean_num)
            photo_file = BufferedInputFile(barcode_bytes, filename=f"ttn_{clean_num}.png")
            caption = (
                f"📱 *Штрихкод для експрес-накладної (ТТН):*\n`{clean_num}`\n\n"
                f"Покажіть цей штрихкод оператору у відділенні Нової Пошти для швидкого сканування або скористайтеся поштоматом."
            )
            kb = get_barcode_keyboard(clean_num)
            if is_callback:
                await target_msg_or_callback.message.answer_photo(
                    photo=photo_file,
                    caption=caption,
                    parse_mode="Markdown",
                    reply_markup=kb,
                )
                if hasattr(target_msg_or_callback, "answer"):
                    try:
                        await target_msg_or_callback.answer("✅ Штрих-код згенеровано!")
                    except Exception:
                        pass
            else:
                await target_msg_or_callback.answer_photo(
                    photo=photo_file,
                    caption=caption,
                    parse_mode="Markdown",
                    reply_markup=kb,
                )
        except Exception as e:
            logger.error(f"Error generating waybill barcode: {e}", exc_info=True)
            err_msg = f"❌ *Не вдалося згенерувати штрих-код:* {e}"
            if is_callback and hasattr(target_msg_or_callback, "answer"):
                try:
                    await target_msg_or_callback.answer(f"❌ Помилка генерації: {e}", show_alert=True)
                except Exception:
                    pass
            elif hasattr(target_msg_or_callback, "answer"):
                try:
                    await target_msg_or_callback.answer(err_msg, parse_mode="Markdown")
                except Exception:
                    pass

    @router.message(Command("barcode"))
    @router.message(Command("code128"))
    @router.message(Command("штрихкод"))
    async def cmd_barcode(message: Message):
        """Generate barcode for a waybill by number or prompt user."""
        user_id = message.from_user.id
        clear_user_active_session(user_id)

        parts = message.text.strip().split()
        if len(parts) > 1:
            raw_target = parts[1]
            ttn = extract_ttn_from_text(raw_target) or "".join(filter(str.isdigit, raw_target))
            if ttn and len(ttn) >= 11:
                USER_BARCODE_WAITING.discard(user_id)
                await send_waybill_barcode(message, ttn)
                return

        USER_BARCODE_WAITING.add(user_id)
        prompt_text = (
            "📱 *Генерація штрих-коду для накладної (ТТН)*\n\n"
            "Надішліть номер накладної (14 цифр) у повідомленні.\n"
            "Бот згенерує штрих-код для швидкого сканування оператором на касі або в поштоматі."
        )
        await message.answer(
            prompt_text,
            parse_mode="Markdown",
            reply_markup=get_main_reply_keyboard(),
        )

    @router.callback_query(CODActionCallback.filter())
    async def process_cod_action_callback(
        callback: CallbackQuery, callback_data: CODActionCallback
    ):
        """Handle inline actions for COD statistics dashboard."""
        action = callback_data.action
        page = callback_data.page
        user_id = callback.from_user.id

        if action == "noop":
            await callback.answer()
            return

        eff_settings = storage_manager.get_effective_settings(user_id, settings)
        user_np_client = NovaPoshtaClient(eff_settings)
        user_settings = storage_manager.get_user_settings(user_id)

        if action in ("refresh", "back"):
            await callback.answer("Оновлення даних...")
            try:
                stats = await user_np_client.get_monthly_cod_stats(
                    user_phone=eff_settings.sender_phone,
                    user_cp_ref=eff_settings.sender_counterparty_ref,
                )
                text = format_cod_dashboard(stats, user_settings)
                await callback.message.edit_text(
                    text,
                    parse_mode="Markdown",
                    reply_markup=get_cod_stats_keyboard(),
                )
            except Exception as e:
                logger.error(f"Error refreshing COD stats: {e}", exc_info=True)
                await callback.answer(f"Помилка оновлення: {e}", show_alert=True)
            return

        if action == "settings":
            await callback.answer()
            settings_text = (
                "⚙️ *Налаштування місячних лімітів накладеного платежу*\n\n"
                "Оберіть бажані ліміти або скористайтесь швидкими кнопками нижче:\n"
                "• *Ліміт суми:* поріг фінансового моніторингу NovaPay\n"
                "• *Ліміт посилок:* поріг ризик-орієнтованого контролю\n\n"
                "Встановити довільне значення вручну:\n"
                "`/set_cod_limit 50000`\n"
                "`/set_cod_count 15`"
            )
            await callback.message.edit_text(
                settings_text,
                parse_mode="Markdown",
                reply_markup=get_cod_settings_keyboard(
                    current_sum_limit=user_settings.cod_monthly_limit_sum,
                    current_count_limit=user_settings.cod_monthly_limit_count,
                    warning_enabled=getattr(user_settings, "cod_warning_enabled", True),
                ),
            )
            return

        if action == "list":
            await callback.answer("Завантаження списку...")
            try:
                stats = await user_np_client.get_monthly_cod_stats(
                    user_phone=eff_settings.sender_phone,
                    user_cp_ref=eff_settings.sender_counterparty_ref,
                )
                page_size = 5
                total_items = len(stats.items)
                total_pages = max(1, (total_items + page_size - 1) // page_size)
                text = format_cod_shipments_page(stats, page=page, page_size=page_size)
                await callback.message.edit_text(
                    text,
                    parse_mode="Markdown",
                    reply_markup=get_cod_shipments_keyboard(
                        current_page=page,
                        total_pages=total_pages,
                    ),
                )
            except Exception as e:
                logger.error(f"Error loading COD shipments list: {e}", exc_info=True)
                await callback.answer(f"Помилка завантаження списку: {e}", show_alert=True)
            return

    @router.callback_query(CODSettingsCallback.filter())
    async def process_cod_settings_callback(
        callback: CallbackQuery, callback_data: CODSettingsCallback
    ):
        """Handle inline adjustments to COD limits and warnings."""
        user_id = callback.from_user.id
        s_type = callback_data.setting_type
        val_str = callback_data.value

        if s_type == "sum":
            val_f = float(val_str)
            new_sum = val_f if val_f > 0 else None
            storage_manager.update_user_settings(user_id, cod_monthly_limit_sum=new_sum)
            msg = f"Ліміт суми встановлено: {int(val_f)} грн" if new_sum else "Ліміт суми вимкнено"
            await callback.answer(msg)
        elif s_type == "count":
            val_i = int(val_str)
            new_cnt = val_i if val_i > 0 else None
            storage_manager.update_user_settings(user_id, cod_monthly_limit_count=new_cnt)
            msg = f"Ліміт посилок встановлено: {val_i} шт" if new_cnt else "Ліміт кількості вимкнено"
            await callback.answer(msg)
        elif s_type == "toggle_warn":
            new_warn = val_str == "1"
            storage_manager.update_user_settings(user_id, cod_warning_enabled=new_warn)
            msg = "Попередження увімкнено" if new_warn else "Попередження вимкнено"
            await callback.answer(msg)

        updated_settings = storage_manager.get_user_settings(user_id)
        settings_text = (
            "⚙️ *Налаштування місячних лімітів накладеного платежу*\n\n"
            "Оберіть бажані ліміти або скористайтесь швидкими кнопками нижче:\n"
            "• *Ліміт суми:* поріг фінансового моніторингу NovaPay\n"
            "• *Ліміт посилок:* поріг ризик-орієнтованого контролю\n\n"
            "Встановити довільне значення вручну:\n"
            "`/set_cod_limit 50000`\n"
            "`/set_cod_count 15`"
        )
        try:
            await callback.message.edit_text(
                settings_text,
                parse_mode="Markdown",
                reply_markup=get_cod_settings_keyboard(
                    current_sum_limit=updated_settings.cod_monthly_limit_sum,
                    current_count_limit=updated_settings.cod_monthly_limit_count,
                    warning_enabled=getattr(updated_settings, "cod_warning_enabled", True),
                ),
            )
        except Exception:
            pass

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
            storage_manager.cleanup_invalid_scansheets(user_id)
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

            # Filter API sheets by recent date, non-empty number/ref & non-printed status
            recent_api_sheets = [
                s for s in raw_api_sheets
                if s.ref and s.number and str(s.number).strip()
                and not s.is_printed and _is_recent_scansheet(s.date_created, max_days=2) and s.count_of_documents > 0
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

            # Filter saved sheets: purge sheets where all TTNs are shipped or missing valid numbers
            recent_saved_sheets = []
            sent_saved_refs = []
            for sheet in raw_saved_sheets:
                if not sheet.ref or not sheet.number or not str(sheet.number).strip():
                    sent_saved_refs.append(sheet.ref)
                    continue
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

            # Map live API sheets by ref and number to synchronize counts
            api_sheet_map = {s.ref: s for s in raw_api_sheets if s.ref}
            api_sheet_map.update({s.number: s for s in raw_api_sheets if s.number})

            displayed_refs = set()
            for sheet in recent_saved_sheets:
                if not sheet.ref or not sheet.number or not str(sheet.number).strip():
                    continue
                displayed_refs.add(sheet.ref)
                displayed_refs.add(sheet.number)
                api_s = api_sheet_map.get(sheet.ref) or api_sheet_map.get(sheet.number)
                eff_count = api_s.count_of_documents if api_s else sheet.count_of_documents
                card = (
                    f"📋 *Реєстр №* `{sheet.number}`\n"
                    f"📅 *Дата:* {sheet.date_created}\n"
                    f"📦 *Кількість накладних:* {eff_count}\n"
                )
                if sheet.document_numbers:
                    card += f"📄 *ТТН у реєстрі:* {', '.join(sheet.document_numbers)}\n"

                await message.answer(
                    card,
                    parse_mode="Markdown",
                    reply_markup=get_register_keyboard(ref=sheet.ref),
                )

            for a_sheet in recent_api_sheets:
                if a_sheet.ref not in displayed_refs and a_sheet.number not in displayed_refs:
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
                    "🔍 *Аналіз чернеток та реєстрів через AI...*",
                    parse_mode="Markdown",
                )

                active_drafts = await fetch_user_active_drafts(actual_user_id, user_np_client, storage_manager)

                # Prepare active register context if available
                last_scansheet = USER_LAST_SCANSHEET_CONTEXT.get(actual_user_id)
                if not last_scansheet:
                    storage_manager.cleanup_invalid_scansheets(actual_user_id)
                    user_saved_sheets = storage_manager.get_user_scansheets(actual_user_id)
                    if user_saved_sheets:
                        recent = user_saved_sheets[0]

                        # Fetch live documents from Nova Poshta API for exact document refs if available
                        doc_refs_map = {}
                        live_doc_numbers = []
                        if recent.ref:
                            try:
                                api_docs = await user_np_client.get_scan_sheet_documents(recent.ref)
                                for ad in api_docs:
                                    n = str(ad.get("Number", "")).strip()
                                    r = str(ad.get("Ref", "")).strip()
                                    if n:
                                        live_doc_numbers.append(n)
                                        if r:
                                            doc_refs_map[n] = r
                            except Exception as e:
                                logger.warning(f"Failed to fetch live docs for scansheet {recent.ref}: {e}")

                        eff_doc_numbers = live_doc_numbers if live_doc_numbers else recent.document_numbers
                        drafts_by_num = {str(d.get("int_doc_number")): d for d in active_drafts}
                        built_items = []
                        for idx, num in enumerate(eff_doc_numbers, 1):
                            d_info = drafts_by_num.get(num, {})
                            doc_ref_val = doc_refs_map.get(num) or d_info.get("ref", num)
                            built_items.append({
                                "index": idx,
                                "int_doc_number": num,
                                "ref": doc_ref_val,
                                "recipient_name": d_info.get("recipient_name", ""),
                                "city_description": d_info.get("city_description", ""),
                                "warehouse_description": d_info.get("warehouse_description", ""),
                                "cargo_description": d_info.get("cargo_description", ""),
                                "declared_value": d_info.get("declared_value", 500),
                                "cod_amount": d_info.get("cod_amount", 0),
                            })
                        last_scansheet = {
                            "ref": recent.ref,
                            "number": recent.number,
                            "date_created": recent.date_created,
                            "count_of_documents": len(built_items),
                            "items": built_items,
                        }

                if not active_drafts and not last_scansheet:
                    await status_msg.edit_text(
                        "📝 *Активних чернеток ТТН та реєстрів не знайдено.*\n"
                        "Усі ваші накладні вже відправлені або ще не створені.",
                        parse_mode="Markdown",
                    )
                    return

                ai_reg_result = await user_ai_extractor.filter_drafts_for_register(
                    user_prompt=text, drafts=active_drafts, active_scansheet=last_scansheet
                )

                if ai_reg_result.action == "list_registers":
                    try:
                        await status_msg.delete()
                    except Exception:
                        pass
                    await cmd_registers(message)
                    return

                if ai_reg_result.action == "delete_register":
                    target_ref = None
                    target_num = None
                    if last_scansheet:
                        target_ref = last_scansheet.get("ref")
                        target_num = last_scansheet.get("number")
                    else:
                        user_sheets = storage_manager.get_user_scansheets(actual_user_id)
                        if user_sheets:
                            target_ref = user_sheets[0].ref
                            target_num = user_sheets[0].number

                    if not target_ref:
                        await status_msg.edit_text(
                            "ℹ️ *У вас немає активних реєстрів для видалення.*",
                            parse_mode="Markdown",
                        )
                        return

                    await status_msg.edit_text(
                        f"⏳ *Розформування та видалення реєстру № `{target_num or target_ref}`...*",
                        parse_mode="Markdown",
                    )
                    deleted = await user_np_client.delete_scan_sheet(target_ref)
                    storage_manager.delete_user_scansheet(actual_user_id, target_ref)
                    USER_LAST_SCANSHEET_CONTEXT.pop(actual_user_id, None)

                    del_msg = (
                        f"🗑 *Реєстр (ScanSheet) № `{target_num or target_ref}` успішно розформовано та видалено з бази Нової Пошти.*\n"
                        "Усі накладні повернуто до ваших активних чернеток."
                        if deleted
                        else f"🗑 *Реєстр № `{target_num or target_ref}` видалено з локальної бази.*"
                    )
                    await status_msg.edit_text(del_msg, parse_mode="Markdown")
                    return

                if ai_reg_result.action == "remove_waybill":
                    # If user specified a specific register number or ref, switch to it
                    if ai_reg_result.register_number_or_ref:
                        reg_q = str(ai_reg_result.register_number_or_ref).strip()
                        user_saved_sheets = storage_manager.get_user_scansheets(actual_user_id)
                        matched_s = next(
                            (s for s in user_saved_sheets if s.number == reg_q or s.ref == reg_q),
                            None
                        )
                        if matched_s:
                            doc_refs_map = {}
                            live_doc_numbers = []
                            if matched_s.ref:
                                try:
                                    api_docs = await user_np_client.get_scan_sheet_documents(matched_s.ref)
                                    for ad in api_docs:
                                        n = str(ad.get("Number", "")).strip()
                                        r = str(ad.get("Ref", "")).strip()
                                        if n:
                                            live_doc_numbers.append(n)
                                            if r:
                                                doc_refs_map[n] = r
                                except Exception as e:
                                    logger.warning(f"Failed to fetch live docs for scansheet {matched_s.ref}: {e}")

                            eff_doc_numbers = live_doc_numbers if live_doc_numbers else matched_s.document_numbers
                            drafts_by_num = {str(d.get("int_doc_number")): d for d in active_drafts}
                            built_items = []
                            for idx, num in enumerate(eff_doc_numbers, 1):
                                d_info = drafts_by_num.get(num, {})
                                doc_ref_val = doc_refs_map.get(num) or d_info.get("ref", num)
                                built_items.append({
                                    "index": idx,
                                    "int_doc_number": num,
                                    "ref": doc_ref_val,
                                    "recipient_name": d_info.get("recipient_name", ""),
                                    "city_description": d_info.get("city_description", ""),
                                    "warehouse_description": d_info.get("warehouse_description", ""),
                                    "cargo_description": d_info.get("cargo_description", ""),
                                    "declared_value": d_info.get("declared_value", 500),
                                    "cod_amount": d_info.get("cod_amount", 0),
                                })
                            last_scansheet = {
                                "ref": matched_s.ref,
                                "number": matched_s.number,
                                "date_created": matched_s.date_created,
                                "count_of_documents": len(built_items),
                                "items": built_items,
                            }

                    if not last_scansheet or not last_scansheet.get("items"):
                        await status_msg.edit_text(
                            "❌ *Не знайдено активного реєстру, з якого можна вилучити накладну.*",
                            parse_mode="Markdown",
                        )
                        return

                    target_item = None
                    items = last_scansheet["items"]

                    # Match by 1-based index (e.g. 2 for №2)
                    if ai_reg_result.target_item_index and 1 <= ai_reg_result.target_item_index <= len(items):
                        target_item = items[ai_reg_result.target_item_index - 1]

                    # Match by explicit target_doc_number
                    if not target_item and ai_reg_result.target_doc_number:
                        clean_num = str(ai_reg_result.target_doc_number).strip()
                        target_item = next((it for it in items if it.get("int_doc_number") == clean_num), None)

                    # Match by selected_doc_numbers
                    if not target_item and ai_reg_result.selected_doc_numbers:
                        sel_set = set(ai_reg_result.selected_doc_numbers)
                        target_item = next((it for it in items if str(it.get("int_doc_number")) in sel_set), None)

                    # Match by recipient name
                    if not target_item and ai_reg_result.target_recipient:
                        rec_query = ai_reg_result.target_recipient.lower().strip()
                        target_item = next((it for it in items if rec_query in it.get("recipient_name", "").lower()), None)

                    if not target_item:
                        await status_msg.edit_text(
                            f"❌ *Накладну не знайдено у поточному реєстрі № `{last_scansheet.get('number')}`.*\n"
                            "Перевірте номер накладної або порядковий номер у списку реєстру.",
                            parse_mode="Markdown",
                        )
                        return

                    doc_to_remove_num = str(target_item["int_doc_number"])
                    doc_to_remove_ref = str(target_item.get("ref", doc_to_remove_num))
                    recipient_name = target_item.get("recipient_name", "")
                    reg_number = last_scansheet.get("number", "")
                    reg_ref = last_scansheet.get("ref", "")

                    await status_msg.edit_text(
                        f"⏳ *Вилучення накладної `{doc_to_remove_num}` ({recipient_name}) з реєстру № `{reg_number}`...*",
                        parse_mode="Markdown",
                    )

                    # Call Nova Poshta removeDocuments API
                    remove_refs = list(set([doc_to_remove_ref, doc_to_remove_num]))
                    try:
                        await user_np_client.remove_documents_from_scan_sheet(remove_refs, scan_sheet_ref=reg_ref)
                    except Exception as rem_err:
                        logger.warning(f"Nova Poshta removeDocuments returned error: {rem_err}")

                    # Update storage
                    storage_manager.remove_document_from_user_scansheet(
                        actual_user_id, reg_ref or reg_number, doc_to_remove_num
                    )

                    # Update context items
                    remaining_items = [it for it in items if it.get("int_doc_number") != doc_to_remove_num]
                    for idx, it in enumerate(remaining_items, 1):
                        it["index"] = idx

                    last_scansheet["items"] = remaining_items
                    last_scansheet["count_of_documents"] = len(remaining_items)
                    USER_LAST_SCANSHEET_CONTEXT[actual_user_id] = last_scansheet

                    # If no items left, auto disband
                    if not remaining_items:
                        try:
                            await user_np_client.delete_scan_sheet(reg_ref)
                        except Exception:
                            pass
                        storage_manager.delete_user_scansheet(actual_user_id, reg_ref)
                        USER_LAST_SCANSHEET_CONTEXT.pop(actual_user_id, None)
                        await status_msg.edit_text(
                            f"✅ *Накладну `{doc_to_remove_num}` успішно вилучено з реєстру!*\n\n"
                            f"Оскільки в реєстрі більше не залишилося накладних, реєстр № `{reg_number}` автоматично розформовано.",
                            parse_mode="Markdown",
                        )
                        return

                    try:
                        await status_msg.delete()
                    except Exception:
                        pass

                    details_lines = []
                    for idx, it in enumerate(remaining_items, 1):
                        cod_str = f" | 💵 Наложка: {int(it.get('cod_amount', 0))} грн" if it.get("cod_amount") else ""
                        line = (
                            f"*{idx}. ТТН:* `{it['int_doc_number']}` | {it['recipient_name']}\n"
                            f"   🏙 {it['city_description']}, {it['warehouse_description']}\n"
                            f"   📝 {it['cargo_description']} | 💰 {int(it.get('declared_value', 500))} грн{cod_str}"
                        )
                        details_lines.append(line)
                    details_block = "\n\n".join(details_lines)

                    caption_text = (
                        f"✅ *Накладну `{doc_to_remove_num}` ({recipient_name}) успішно вилучено з реєстру!*\n\n"
                        f"📋 *Оновлений реєстр (ScanSheet):* `{reg_number}`\n"
                        f"📦 *Залишилось накладних:* {len(remaining_items)}\n\n"
                        f"📄 *Накладні у реєстрі:*\n{details_block}\n\n"
                        "📱 *Покажіть цей штрихкод оператору Нової Пошти для сканування!*"
                    )

                    try:
                        barcode_bytes = generate_code128_barcode(reg_number)
                        photo_file = BufferedInputFile(barcode_bytes, filename=f"scansheet_{reg_number}.png")
                        await message.answer_photo(
                            photo=photo_file,
                            caption=caption_text,
                            parse_mode="Markdown",
                            reply_markup=get_register_keyboard(ref=reg_ref),
                        )
                    except Exception as bc_err:
                        logger.error(f"Error generating updated barcode photo: {bc_err}")
                        await message.answer(
                            caption_text,
                            parse_mode="Markdown",
                            reply_markup=get_register_keyboard(ref=reg_ref),
                        )
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

                    try:
                        scansheet_info = await user_np_client.create_scan_sheet(doc_refs)
                    except Exception as ss_err:
                        logger.error(f"Error creating scan sheet via Nova Poshta API: {ss_err}", exc_info=True)
                        await status_msg.edit_text(
                            f"❌ *Помилка створення реєстру:* {ss_err}",
                            parse_mode="Markdown",
                        )
                        return

                    # Filter matched_drafts to only those that actually succeeded in Nova Poshta API
                    success_set = set(str(s).strip() for s in scansheet_info.success_documents)
                    truly_added_drafts = [
                        d for d in matched_drafts
                        if str(d.get("int_doc_number", "")).strip() in success_set or str(d.get("ref", "")).strip() in success_set
                    ]
                    if not truly_added_drafts and matched_drafts:
                        truly_added_drafts = matched_drafts

                    actual_doc_nums = [d["int_doc_number"] for d in truly_added_drafts]

                    saved_scansheet = SavedScanSheet(
                        ref=scansheet_info.ref,
                        number=scansheet_info.number,
                        date_created=scansheet_info.date_created,
                        count_of_documents=scansheet_info.count_of_documents,
                        document_numbers=actual_doc_nums,
                    )
                    storage_manager.add_user_scansheet(actual_user_id, saved_scansheet)
                    storage_manager.update_drafts_scansheet(
                        actual_user_id, actual_doc_nums, scansheet_info.number
                    )

                    # Update context for follow-up actions (like removal of waybills)
                    scansheet_items = []
                    for idx, d in enumerate(truly_added_drafts, 1):
                        scansheet_items.append({
                            "index": idx,
                            "int_doc_number": str(d.get("int_doc_number")),
                            "ref": str(d.get("ref")),
                            "recipient_name": d.get("recipient_name", ""),
                            "city_description": d.get("city_description", ""),
                            "warehouse_description": d.get("warehouse_description", ""),
                            "cargo_description": d.get("cargo_description", ""),
                            "declared_value": d.get("declared_value", 500),
                            "cod_amount": d.get("cod_amount", 0),
                        })
                    USER_LAST_SCANSHEET_CONTEXT[actual_user_id] = {
                        "ref": scansheet_info.ref,
                        "number": scansheet_info.number,
                        "date_created": scansheet_info.date_created,
                        "count_of_documents": scansheet_info.count_of_documents,
                        "items": scansheet_items,
                    }

                    # Construct detailed list of included TTNs
                    details_lines = []
                    for idx, d in enumerate(truly_added_drafts, 1):
                        cod_str = f" | 💵 Наложка: {int(d.get('cod_amount', 0))} грн" if d.get("cod_amount") else ""
                        line = (
                            f"*{idx}. ТТН:* `{d['int_doc_number']}` | {d['recipient_name']}\n"
                            f"   🏙 {d['city_description']}, {d['warehouse_description']}\n"
                            f"   📝 {d['cargo_description']} | 💰 {int(d.get('declared_value', 500))} грн{cod_str}"
                        )
                        details_lines.append(line)
                    details_block = "\n\n".join(details_lines)

                    # Warnings for rejected / skipped documents
                    rejected_block = ""
                    if scansheet_info.error_documents:
                        rejected_lines = []
                        for item in scansheet_info.error_documents:
                            num = item.get("number") or item.get("ref") or ""
                            err_txt = item.get("error") or "Не вдалося додати"
                            ss_num = item.get("scansheet_number")
                            ss_info = f" (вже у реєстрі `{ss_num}`)" if ss_num else ""
                            if num:
                                rejected_lines.append(f"• `{num}`: {err_txt}{ss_info}")
                            elif err_txt:
                                rejected_lines.append(f"• {err_txt}")
                        if rejected_lines:
                            rejected_block = "\n\n⚠️ *Не додано до цього реєстру:*\n" + "\n".join(rejected_lines)

                    try:
                        await status_msg.delete()
                    except Exception:
                        pass

                    caption_text = (
                        f"✅ *Реєстр (ScanSheet) успішно створено!*\n\n"
                        f"📋 *Номер реєстру:* `{scansheet_info.number}`\n"
                        f"📅 *Дата створення:* {scansheet_info.date_created}\n"
                        f"📦 *Кількість накладних:* {scansheet_info.count_of_documents}\n\n"
                        f"📄 *Накладні у реєстрі:*\n{details_block}{rejected_block}\n\n"
                        "📱 *Покажіть цей штрихкод оператору Нової Пошти для сканування!*"
                    )

                    try:
                        barcode_bytes = generate_code128_barcode(scansheet_info.number)
                        photo_file = BufferedInputFile(barcode_bytes, filename=f"scansheet_{scansheet_info.number}.png")
                        await message.answer_photo(
                            photo=photo_file,
                            caption=caption_text,
                            parse_mode="Markdown",
                            reply_markup=get_register_keyboard(ref=scansheet_info.ref),
                        )
                    except Exception as bc_err:
                        logger.error(f"Error generating barcode photo for scansheet {scansheet_info.number}: {bc_err}")
                        await message.answer(
                            caption_text,
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

            # Track conversation history and initial message
            existing_init = existing_session.get("initial_raw_text") if existing_session else None
            existing_all = existing_session.get("all_raw_texts", []) if existing_session else []

            if prev_parsed_info or existing_init:
                init_text = existing_init or USER_INITIAL_MESSAGE_TEXT.get(actual_user_id) or text
            else:
                init_text = text
            USER_INITIAL_MESSAGE_TEXT[actual_user_id] = init_text

            all_texts = list(existing_all)
            if text and text not in all_texts:
                all_texts.append(text)
            if init_text and init_text not in all_texts:
                all_texts.insert(0, init_text)

            if parsed_info.has_address_suspicion and not address_choice_made:
                PENDING_SESSIONS[session_id] = {
                    "parsed_info": parsed_info,
                    "user_id": actual_user_id,
                    "updated_at": datetime.datetime.now().timestamp(),
                    "initial_raw_text": init_text,
                    "all_raw_texts": all_texts,
                    "raw_text": text,
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
                raw_text=text,
                initial_raw_text=init_text,
                all_raw_texts=all_texts,
            )
        except Exception as e:
            logger.error(f"Error processing text message: {e}", exc_info=True)
            await status_msg.edit_text(f"❌ *Сталася помилка:* {str(e)}", parse_mode="Markdown")

    _evaluate_cod_limits = evaluate_cod_limits

    async def _check_cod_warning(
        user_id: int,
        cod_val: float,
        user_np_client: NovaPoshtaClient,
        storage_manager: UserSettingsManager,
        eff_settings: Settings,
        editing_ref: Optional[str] = None,
    ) -> str:
        """Return warning and status message for COD limits."""
        res = await evaluate_cod_limits(
            user_id=user_id,
            cod_val=cod_val,
            user_np_client=user_np_client,
            storage_manager=storage_manager,
            eff_settings=eff_settings,
            editing_ref=editing_ref,
        )
        return res.get("warn_line", "")


    async def _continue_processing_recipient_info(
        message: Message,
        user_id: int,
        session_id: str,
        parsed_info: ParsedRecipientInfo,
        status_msg: Message,
        prev_parsed_info: Optional[ParsedRecipientInfo] = None,
        raw_text: Optional[str] = None,
        initial_raw_text: Optional[str] = None,
        all_raw_texts: Optional[List[str]] = None,
    ):
        """Resolve city, warehouse or street address, and display verification confirmation card."""
        eff_settings = storage_manager.get_effective_settings(user_id, settings)
        user_np_client = NovaPoshtaClient(eff_settings)
        now_ts = datetime.datetime.now().timestamp()

        existing_session = PENDING_SESSIONS.get(session_id, {})
        if not raw_text:
            raw_text = existing_session.get("raw_text") or (
                message.text if hasattr(message, "text") and message.text else None
            )

        if not initial_raw_text:
            initial_raw_text = (
                existing_session.get("initial_raw_text")
                or USER_INITIAL_MESSAGE_TEXT.get(user_id)
                or raw_text
            )

        if not all_raw_texts:
            all_raw_texts = list(existing_session.get("all_raw_texts", []))
            if raw_text and raw_text not in all_raw_texts:
                all_raw_texts.append(raw_text)
            if initial_raw_text and initial_raw_text not in all_raw_texts:
                all_raw_texts.insert(0, initial_raw_text)

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
                "raw_text": raw_text,
                "initial_raw_text": initial_raw_text,
                "all_raw_texts": all_raw_texts,
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
            return is_city_matched(p_city, e_city)

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
                streets = await user_np_client.search_street(
                    city_ref=matched_city.ref,
                    street_name=street_query,
                    settlement_ref=getattr(matched_city, "settlement_ref", None),
                    city_name=matched_city.description,
                )
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

                    card_in_text = CARD_BLOCK_REGEX.search(raw_text) if isinstance(raw_text, str) else None
                    if card_in_text:
                        c_digits = "".join(filter(str.isdigit, card_in_text.group(0)))
                        if len(c_digits) in (16, 18, 19) and not c_digits.startswith("380"):
                            masked_c = f"{c_digits[:6]}******{c_digits[-4:]}"
                            storage_manager.update_user_settings(user_id, sender_card_mask=masked_c)
                            cod_type = "card"

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
                        "raw_text": raw_text,
                        "initial_raw_text": initial_raw_text,
                        "all_raw_texts": all_raw_texts,
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
                    # Attempt AI and heuristic candidate disambiguation if initial_raw_text, raw_text, or any accumulated messages contain address details
                    texts_for_disambig = []
                    for t in [initial_raw_text, raw_text] + (all_raw_texts or []):
                        if t and t.strip() and t.strip() not in texts_for_disambig:
                            texts_for_disambig.append(t.strip())
                    combined_disambig_text = "\n\n".join(texts_for_disambig)

                    chosen_idx = None
                    if combined_disambig_text:
                        logger.info(
                            f"Attempting candidate disambiguation across {len(matching_candidates)} candidates with query text: {combined_disambig_text!r}"
                        )
                        try:
                            user_ai_extractor = AIExtractor(eff_settings)
                            chosen_idx = await user_ai_extractor.disambiguate_candidates(
                                text=combined_disambig_text, candidates=matching_candidates
                            )
                        except Exception as disambig_err:
                            logger.warning(f"Error during candidate disambiguation: {disambig_err}")
                            chosen_idx = AIExtractor.heuristic_disambiguate_candidates(
                                text=combined_disambig_text, candidates=matching_candidates
                            )
                        logger.info(
                            f"Candidate disambiguation result: chosen_idx={chosen_idx} (None = fallback to keyboard)"
                        )

                    if chosen_idx is not None and 0 <= chosen_idx < len(matching_candidates):
                        matched_city, warehouse = matching_candidates[chosen_idx]
                        dest_desc = warehouse.description
                        logger.info(
                            f"Automatically selected candidate {chosen_idx + 1} "
                            f"({matched_city.description}, {dest_desc}) based on address in user text"
                        )
                    else:
                        # Save candidates in session and present city disambiguation keyboard
                        existing_session = PENDING_SESSIONS.get(session_id, {})
                        session_payload = {
                            "parsed_info": parsed_info,
                            "candidates": matching_candidates,
                            "user_id": user_id,
                            "updated_at": now_ts,
                            "raw_text": raw_text,
                            "initial_raw_text": initial_raw_text,
                            "all_raw_texts": all_raw_texts,
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

        card_in_text = CARD_BLOCK_REGEX.search(raw_text) if isinstance(raw_text, str) else None
        if card_in_text:
            c_digits = "".join(filter(str.isdigit, card_in_text.group(0)))
            if len(c_digits) in (16, 18, 19) and not c_digits.startswith("380"):
                masked_c = f"{c_digits[:6]}******{c_digits[-4:]}"
                storage_manager.update_user_settings(user_id, sender_card_mask=masked_c)
                cod_type = "card"

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
            "raw_text": raw_text,
            "initial_raw_text": initial_raw_text,
            "all_raw_texts": all_raw_texts,
        }
        if editing_ref:
            session_payload["editing_draft_ref"] = editing_ref

        PENDING_SESSIONS[session_id] = session_payload
        USER_ACTIVE_SESSIONS[user_id] = session_id
        USER_LAST_PARSED_INFO[user_id] = parsed_info

        cod_str = "❌ Немає" if cod_val <= 0 else f"{int(cod_val)} грн ({'Картка' if cod_type == 'card' else 'Готівка'})"

        eval_res = await evaluate_cod_limits(
            user_id=user_id,
            cod_val=cod_val,
            user_np_client=user_np_client,
            storage_manager=storage_manager,
            eff_settings=eff_settings,
            editing_ref=editing_ref,
        )
        warn_line = eval_res.get("warn_line", "")
        suggested_prof = eval_res.get("suggested_profile")

        profiles = storage_manager.get_sender_profiles(user_id)
        active_p = storage_manager.get_active_profile(user_id)
        has_multiple_profiles = len(profiles) > 1
        active_name = active_p.name if active_p else None
        sender_prefix = f"👤 *Відправник:* {active_name}\n" if (active_name and has_multiple_profiles) else ""

        card_text = (
            "📋 *Розпарсені дані отримувача для перевірки:*\n\n"
            f"{sender_prefix}"
            f"👤 *Отримувач:* {parsed_info.full_name}\n"
            f"📞 *Телефон:* `{parsed_info.phone}`\n"
            f"🏙 *Місто:* {matched_city.description}\n"
            f"📦 *Пункт призначення:* {dest_desc}\n"
            f"📝 *Опис вантажу:* {cargo_desc}\n"
            f"💰 *Оціночна вартість:* {int(declared_val)} грн (Мін. 500 грн)\n"
            f"💵 *Накладений платіж:* {cod_str}\n"
            f"{warn_line}\n"
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
                suggested_profile=suggested_prof,
                active_profile_name=active_name,
                has_multiple_profiles=has_multiple_profiles,
            ),
        )
        if session_id in PENDING_SESSIONS:
            PENDING_SESSIONS[session_id]["chat_id"] = status_msg.chat.id
            PENDING_SESSIONS[session_id]["message_id"] = status_msg.message_id

    async def _debounce_and_dispatch(user_id: int):
        """Wait for rapid forwarded messages to accumulate before dispatching for processing."""
        try:
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return
        finally:
            if USER_DEBOUNCE_TASKS.get(user_id) is asyncio.current_task():
                USER_DEBOUNCE_TASKS.pop(user_id, None)

        buffered_texts = USER_MESSAGE_BUFFERS.pop(user_id, [])
        last_message = USER_LAST_MESSAGES.pop(user_id, None)

        if not buffered_texts or not last_message:
            return

        combined_text = "\n".join(buffered_texts)
        await _process_user_accumulated_messages(user_id, last_message, combined_text)

    async def _process_user_accumulated_messages(
        user_id: int,
        last_message: Optional[Message] = None,
        combined_text: Optional[str] = None,
    ):
        """Sequentially process accumulated messages under user lock without cancellation risk."""
        lock = get_user_processing_lock(user_id)
        async with lock:
            if last_message is None or combined_text is None:
                buffered_texts = USER_MESSAGE_BUFFERS.pop(user_id, [])
                last_message = USER_LAST_MESSAGES.pop(user_id, None)
                if not buffered_texts or not last_message:
                    return
                combined_text = "\n".join(buffered_texts)

            try:
                await _handle_combined_text_message(last_message, combined_text)
            except Exception as e:
                logger.error(
                    f"Error handling combined text message for user {user_id}: {e}",
                    exc_info=True,
                )

    @router.message(F.text)
    async def process_text_message(message: Message):
        """Handle raw user text with debouncing for rapid forwarded messages."""
        text = message.text.strip()
        if text.startswith("/"):
            return

        user_id = message.from_user.id

        # Check if user sent a standalone 16-digit bank card number or is waiting for card input
        is_waiting_card = user_id in USER_CARD_WAITING
        detected_card = extract_standalone_bank_card(text, is_waiting=is_waiting_card)
        if detected_card:
            await _save_user_card_and_resume_session(message, user_id, detected_card)
            return

        # If user was in card waiting mode, but sent other text (e.g. a waybill), clear waiting state
        if is_waiting_card:
            USER_CARD_WAITING.discard(user_id)

        # Check if user is waiting to rename a sender profile with a custom alias
        if user_id in USER_RENAME_PROFILE_WAITING:
            target_profile_id = USER_RENAME_PROFILE_WAITING.pop(user_id)
            new_alias = text.strip()
            if new_alias.lower() in ("/cancel", "скасувати", "відміна"):
                await message.answer("❌ Зміну псевдоніма скасовано.")
                return
            if new_alias.startswith("/reset"):
                target_p = storage_manager.get_sender_profile(user_id, target_profile_id)
                orig_name = target_p.sender_name if target_p and target_p.sender_name else "Користувач"
                storage_manager.rename_sender_profile(user_id, target_profile_id, "")
                await message.answer(
                    f"🔄 *Псевдонім скинуто до офіційного імені:* «{orig_name}»",
                    parse_mode="Markdown",
                )
                await _render_users_dashboard(message, user_id)
                return

            if len(new_alias) > 40 or "\n" in new_alias:
                await message.answer("⚠️ Псевдонім має бути коротким (до 40 символів, в один рядок). Спробуйте ще раз або введіть /cancel.")
                USER_RENAME_PROFILE_WAITING[user_id] = target_profile_id
                return

            updated = storage_manager.rename_sender_profile(user_id, target_profile_id, new_alias)
            if updated:
                await message.answer(
                    f"✅ *Псевдонім успішно встановлено!*\n\n"
                    f"👤 *Псевдонім:* «{updated.name}»\n"
                    f"📋 *Офіційне ПІБ:* `{updated.sender_name or updated.name}`\n"
                    f"📱 *Телефон:* `{updated.sender_phone or '—'}`\n\n"
                    "Тепер цей псевдонім відображатиметься на кнопках та в кабінеті користувачів.",
                    parse_mode="Markdown",
                )
                await _render_users_dashboard(message, user_id)
                return
            else:
                await message.answer("❌ Профіль не знайдено або сталася помилка.")
                return

        # Check if user is waiting to add a sender profile with an NP API key
        if user_id in USER_ADD_PROFILE_WAITING:
            clean_key = text.strip()
            if len(clean_key) >= 20 and " " not in clean_key:
                await _handle_add_profile_with_key(message, user_id, clean_key)
                return
            else:
                USER_ADD_PROFILE_WAITING.discard(user_id)

        # Check if user sent a TTN or asked to track a parcel / generate barcode
        ttn = extract_ttn_from_text(text)

        # 1. Waiting for barcode
        if user_id in USER_BARCODE_WAITING and ttn is not None:
            USER_BARCODE_WAITING.discard(user_id)
            await send_waybill_barcode(message, ttn)
            return

        # 2. Explicit barcode request with TTN
        if ttn is not None and is_barcode_intent(text):
            USER_BARCODE_WAITING.discard(user_id)
            await send_waybill_barcode(message, ttn)
            return

        # 3. Explicit tracking request or waiting for tracking
        is_explicit_track = (
            (user_id in USER_TRACKING_WAITING and ttn is not None)
            or (ttn is not None and is_tracking_intent(text))
        )
        if is_explicit_track and ttn:
            USER_TRACKING_WAITING.discard(user_id)
            await handle_track_document(message, ttn, user_id)
            return

        # 4. User dropped / sent just a waybill number -> present both Track & Barcode buttons
        if (
            ttn is not None
            and len(text.strip()) <= 35
            and not any(w in text.lower() for w in ["реєстр", "scansheet", "чернетк", "створ"])
        ):
            USER_TRACKING_WAITING.discard(user_id)
            USER_BARCODE_WAITING.discard(user_id)
            kb = get_waybill_action_keyboard(ttn)
            await message.answer(
                f"📦 *Отримано номер накладної (ТТН):*\n`{ttn}`\n\n"
                "Оберіть потрібну дію:",
                parse_mode="Markdown",
                reply_markup=kb,
            )
            return

        # If user was in barcode waiting mode but input wasn't recognized as TTN
        if user_id in USER_BARCODE_WAITING:
            alpha_chars = [c for c in text if c.isalpha()]
            if len(alpha_chars) < 10:
                await message.answer(
                    "⚠️ *Номер ТТН не розпізнано.*\n"
                    "Номер накладної Нової Пошти зазвичай містить 14 цифр (наприклад, `20450123456789`).\n"
                    "Спробуйте ще раз або скористайтеся меню нижче.",
                    parse_mode="Markdown",
                    reply_markup=get_main_reply_keyboard(),
                )
                return
            else:
                USER_BARCODE_WAITING.discard(user_id)

        # If user was in tracking waiting mode but input wasn't recognized as TTN
        if user_id in USER_TRACKING_WAITING:
            alpha_chars = [c for c in text if c.isalpha()]
            # If user entered something short that is not a recipient address
            if len(alpha_chars) < 10:
                await message.answer(
                    "⚠️ *Номер ТТН не розпізнано.*\n"
                    "Номер накладної Нової Пошти зазвичай містить 14 цифр (наприклад, `20450123456789`).\n"
                    "Спробуйте ще раз або скористайтеся меню нижче.",
                    parse_mode="Markdown",
                    reply_markup=get_main_reply_keyboard(),
                )
                return
            else:
                # User sent full recipient data instead, discard tracking state
                USER_TRACKING_WAITING.discard(user_id)

        if user_id not in USER_MESSAGE_BUFFERS:
            USER_MESSAGE_BUFFERS[user_id] = []

        USER_MESSAGE_BUFFERS[user_id].append(text)
        USER_LAST_MESSAGES[user_id] = message

        # Cancel existing pending debounce task if running in sleep phase, restart 1.0s timer
        if user_id in USER_DEBOUNCE_TASKS and not USER_DEBOUNCE_TASKS[user_id].done():
            USER_DEBOUNCE_TASKS[user_id].cancel()

        USER_DEBOUNCE_TASKS[user_id] = asyncio.create_task(
            _debounce_and_dispatch(user_id)
        )

    @router.callback_query(TrackActionCallback.filter())
    async def process_track_callback(
        callback: CallbackQuery, callback_data: TrackActionCallback
    ):
        """Handle inline actions for tracking cards."""
        action = callback_data.action
        doc_number = callback_data.doc_number
        user_id = callback.from_user.id

        eff_settings = storage_manager.get_effective_settings(user_id, settings)
        user_np_client = NovaPoshtaClient(eff_settings)

        if action == "track":
            await callback.answer("⏳ Отримання даних ТТН...")
            await handle_track_document(
                callback.message, doc_number, user_id, status_msg=callback.message
            )
            return

        elif action == "refresh":
            await callback.answer("⏳ Оновлення статусу ТТН...")
            try:
                tracking_info = await user_np_client.track_document(doc_number)
            except Exception as e:
                logger.error(f"Error refreshing tracking for {doc_number}: {e}")
                tracking_info = None

            if not tracking_info:
                await callback.answer("❌ Не вдалося оновити дані або накладну не знайдено.", show_alert=True)
                return

            new_text = format_tracking_card(tracking_info)
            try:
                await callback.message.edit_text(
                    new_text,
                    parse_mode="Markdown",
                    reply_markup=get_tracking_keyboard(doc_number),
                )
                await callback.answer("✅ Статус оновлено!")
            except Exception:
                await callback.answer("✅ Дані актуальні.")

        elif action == "barcode":
            await callback.answer("⏳ Генерація штрих-коду...")
            await send_waybill_barcode(callback, doc_number)

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

        eff_settings = storage_manager.get_effective_settings(user_id, settings)
        user_np_client = NovaPoshtaClient(eff_settings)

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

            eval_res = await _evaluate_cod_limits(
                user_id=user_id,
                cod_val=cod_val,
                user_np_client=user_np_client,
                storage_manager=storage_manager,
                eff_settings=eff_settings,
                editing_ref=session.get("editing_draft_ref"),
            )
            warn_line = eval_res.get("warn_line", "")
            s_prof = eval_res.get("suggested_profile")

            profiles = storage_manager.get_sender_profiles(user_id)
            active_p = storage_manager.get_active_profile(user_id)
            has_multiple = len(profiles) > 1
            active_name = active_p.name if active_p else None
            sender_prefix = f"👤 *Відправник:* {active_name}\n" if (active_name and has_multiple) else ""

            card_text = (
                "📋 *Розпарсені дані отримувача для перевірки:*\n\n"
                f"{sender_prefix}"
                f"👤 *Отримувач:* {parsed_info.full_name}\n"
                f"📞 *Телефон:* `{parsed_info.phone}`\n"
                f"🏙 *Місто:* {city.description}\n"
                f"📦 *Пункт призначення:* {dest_desc}\n"
                f"📝 *Опис вантажу:* {cargo_desc}\n"
                f"💰 *Оціночна вартість:* {int(next_val)} грн (Мін. 500 грн)\n"
                f"💵 *Накладений платіж:* {cod_str}\n"
                f"{warn_line}\n"
                "Перевірте дані та оберіть дію нижче:"
            )

            u_custom = storage_manager.get_user_settings(user_id)
            card_mask = u_custom.sender_card_mask

            await callback.message.edit_text(
                card_text,
                parse_mode="Markdown",
                reply_markup=get_confirmation_keyboard(
                    payer_type=session["payer_type"],
                    cargo_type=session["cargo_type"],
                    declared_value=next_val,
                    cod_amount=cod_val,
                    cod_payment_type=cod_type,
                    sender_card_mask=card_mask,
                    session_id=session_id,
                    suggested_profile=s_prof,
                    active_profile_name=active_name,
                    has_multiple_profiles=has_multiple,
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

            eval_res = await _evaluate_cod_limits(
                user_id=user_id,
                cod_val=next_cod,
                user_np_client=user_np_client,
                storage_manager=storage_manager,
                eff_settings=eff_settings,
                editing_ref=session.get("editing_draft_ref"),
            )
            warn_line = eval_res.get("warn_line", "")
            s_prof = eval_res.get("suggested_profile")

            profiles = storage_manager.get_sender_profiles(user_id)
            active_p = storage_manager.get_active_profile(user_id)
            has_multiple = len(profiles) > 1
            active_name = active_p.name if active_p else None
            sender_prefix = f"👤 *Відправник:* {active_name}\n" if (active_name and has_multiple) else ""

            card_text = (
                "📋 *Розпарсені дані отримувача для перевірки:*\n\n"
                f"{sender_prefix}"
                f"👤 *Отримувач:* {parsed_info.full_name}\n"
                f"📞 *Телефон:* `{parsed_info.phone}`\n"
                f"🏙 *Місто:* {city.description}\n"
                f"📦 *Пункт призначення:* {dest_desc}\n"
                f"📝 *Опис вантажу:* {cargo_desc}\n"
                f"💰 *Оціночна вартість:* {int(declared_val)} грн (Мін. 500 грн)\n"
                f"💵 *Накладений платіж:* {cod_str}\n"
                f"{warn_line}\n"
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
                    suggested_profile=s_prof,
                    active_profile_name=active_name,
                    has_multiple_profiles=has_multiple,
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
                try:
                    cards = await user_np_client.get_payment_cards(eff_settings.sender_counterparty_ref)
                    if cards:
                        first_card = cards[0]
                        c_num = first_card.get("Number") or first_card.get("Description") or ""
                        c_ref = first_card.get("Ref") or ""
                        if c_num:
                            card_mask = c_num
                            storage_manager.update_user_settings(user_id, sender_card_mask=c_num, sender_card_ref=c_ref)
                            await callback.answer(f"💳 Автоматично підключено картку з кабінету: {c_num}")
                except Exception as e:
                    logger.debug(f"Could not auto-fetch cards from NP: {e}")

            if new_type == "card" and not card_mask:
                USER_CARD_WAITING.add(user_id)
                await callback.answer(
                    "⚠️ Введіть 16 цифр картки або команду /set_card НомерКартки",
                    show_alert=True,
                )
            else:
                type_ua = "На картку" if new_type == "card" else "Готівкою у відділенні"
                await callback.answer(f"Виплату змінено на: {type_ua}")

            session["chat_id"] = callback.message.chat.id
            session["message_id"] = callback.message.message_id
            card_text, reply_markup = await _build_waybill_preview_message(session_id, user_id)
            await callback.message.edit_text(
                card_text,
                parse_mode="Markdown",
                reply_markup=reply_markup,
            )
            return

        if action == "switch_profile":
            eval_res = await _evaluate_cod_limits(
                user_id=user_id,
                cod_val=session.get("cod_amount", 0.0),
                user_np_client=user_np_client,
                storage_manager=storage_manager,
                eff_settings=eff_settings,
                editing_ref=session.get("editing_draft_ref"),
            )
            s_prof = eval_res.get("suggested_profile")
            target_pid = s_prof["id"] if s_prof else None

            profiles = storage_manager.get_sender_profiles(user_id)
            active_p = storage_manager.get_active_profile(user_id)
            if not target_pid and len(profiles) > 1 and active_p:
                curr_idx = next((i for i, p in enumerate(profiles) if p.id == active_p.id), 0)
                target_pid = profiles[(curr_idx + 1) % len(profiles)].id

            if target_pid:
                switched = storage_manager.set_active_profile(user_id, target_pid)
                eff_settings = storage_manager.get_effective_settings(user_id, settings)
                user_np_client = NovaPoshtaClient(eff_settings)
                switched_name = switched.name if switched else "іншого"
                ans_text = f"✅ Відправника перемкнуто на «{switched_name}»! Баланс оновлено."
            else:
                ans_text = "⚠️ Немає інших користувачів"

            parsed_info = session["parsed_info"]
            city = session["city"]
            dest_desc = session.get("destination_description")
            cargo_desc = session["cargo_description"]
            declared_val = session["declared_value"]
            cod_val = session.get("cod_amount", 0.0)
            cod_type = session.get("cod_payment_type", "cash")
            cod_str = "❌ Немає" if cod_val <= 0 else f"{int(cod_val)} грн ({'Картка' if cod_type == 'card' else 'Готівка'})"

            eval_res2 = await _evaluate_cod_limits(
                user_id=user_id,
                cod_val=cod_val,
                user_np_client=user_np_client,
                storage_manager=storage_manager,
                eff_settings=eff_settings,
                editing_ref=session.get("editing_draft_ref"),
            )
            warn_line = eval_res2.get("warn_line", "")
            s_prof2 = eval_res2.get("suggested_profile")

            profiles2 = storage_manager.get_sender_profiles(user_id)
            active_p2 = storage_manager.get_active_profile(user_id)
            has_multiple2 = len(profiles2) > 1
            active_name2 = active_p2.name if active_p2 else None
            sender_prefix = f"👤 *Відправник:* {active_name2}\n" if (active_name2 and has_multiple2) else ""

            card_text = (
                "📋 *Розпарсені дані отримувача для перевірки:*\n\n"
                f"{sender_prefix}"
                f"👤 *Отримувач:* {parsed_info.full_name}\n"
                f"📞 *Телефон:* `{parsed_info.phone}`\n"
                f"🏙 *Місто:* {city.description}\n"
                f"📦 *Пункт призначення:* {dest_desc}\n"
                f"📝 *Опис вантажу:* {cargo_desc}\n"
                f"💰 *Оціночна вартість:* {int(declared_val)} грн (Мін. 500 грн)\n"
                f"💵 *Накладений платіж:* {cod_str}\n"
                f"{warn_line}\n"
                "Перевірте дані та оберіть дію нижче:"
            )

            u_custom = storage_manager.get_user_settings(user_id)
            card_mask = u_custom.sender_card_mask

            await callback.message.edit_text(
                card_text,
                parse_mode="Markdown",
                reply_markup=get_confirmation_keyboard(
                    payer_type=session["payer_type"],
                    cargo_type=session["cargo_type"],
                    declared_value=declared_val,
                    cod_amount=cod_val,
                    cod_payment_type=cod_type,
                    sender_card_mask=card_mask,
                    session_id=session_id,
                    suggested_profile=s_prof2,
                    active_profile_name=active_name2,
                    has_multiple_profiles=has_multiple2,
                ),
            )
            await callback.answer(ans_text)
            return

        if action == "cycle_sender":
            profiles = storage_manager.get_sender_profiles(user_id)
            active_p = storage_manager.get_active_profile(user_id)
            if len(profiles) > 1 and active_p:
                curr_idx = next((i for i, p in enumerate(profiles) if p.id == active_p.id), 0)
                next_p = profiles[(curr_idx + 1) % len(profiles)]
                storage_manager.set_active_profile(user_id, next_p.id)
                eff_settings = storage_manager.get_effective_settings(user_id, settings)
                user_np_client = NovaPoshtaClient(eff_settings)
                ans_text = f"👤 Обрано відправника: «{next_p.name}»"
            else:
                ans_text = "⚠️ Лише один користувач"

            parsed_info = session["parsed_info"]
            city = session["city"]
            dest_desc = session.get("destination_description")
            cargo_desc = session["cargo_description"]
            declared_val = session["declared_value"]
            cod_val = session.get("cod_amount", 0.0)
            cod_type = session.get("cod_payment_type", "cash")
            cod_str = "❌ Немає" if cod_val <= 0 else f"{int(cod_val)} грн ({'Картка' if cod_type == 'card' else 'Готівка'})"

            eval_res = await _evaluate_cod_limits(
                user_id=user_id,
                cod_val=cod_val,
                user_np_client=user_np_client,
                storage_manager=storage_manager,
                eff_settings=eff_settings,
                editing_ref=session.get("editing_draft_ref"),
            )
            warn_line = eval_res.get("warn_line", "")
            s_prof = eval_res.get("suggested_profile")

            profiles2 = storage_manager.get_sender_profiles(user_id)
            active_p2 = storage_manager.get_active_profile(user_id)
            has_multiple2 = len(profiles2) > 1
            active_name2 = active_p2.name if active_p2 else None
            sender_prefix = f"👤 *Відправник:* {active_name2}\n" if (active_name2 and has_multiple2) else ""

            card_text = (
                "📋 *Розпарсені дані отримувача для перевірки:*\n\n"
                f"{sender_prefix}"
                f"👤 *Отримувач:* {parsed_info.full_name}\n"
                f"📞 *Телефон:* `{parsed_info.phone}`\n"
                f"🏙 *Місто:* {city.description}\n"
                f"📦 *Пункт призначення:* {dest_desc}\n"
                f"📝 *Опис вантажу:* {cargo_desc}\n"
                f"💰 *Оціночна вартість:* {int(declared_val)} грн (Мін. 500 грн)\n"
                f"💵 *Накладений платіж:* {cod_str}\n"
                f"{warn_line}\n"
                "Перевірте дані та оберіть дію нижче:"
            )

            u_custom = storage_manager.get_user_settings(user_id)
            card_mask = u_custom.sender_card_mask

            await callback.message.edit_text(
                card_text,
                parse_mode="Markdown",
                reply_markup=get_confirmation_keyboard(
                    payer_type=session["payer_type"],
                    cargo_type=session["cargo_type"],
                    declared_value=declared_val,
                    cod_amount=cod_val,
                    cod_payment_type=cod_type,
                    sender_card_mask=card_mask,
                    session_id=session_id,
                    suggested_profile=s_prof,
                    active_profile_name=active_name2,
                    has_multiple_profiles=has_multiple2,
                ),
            )
            await callback.answer(ans_text)
            return

        if action == "back_to_card":
            parsed_info = session["parsed_info"]
            city = session["city"]
            dest_desc = session.get("destination_description")
            cargo_desc = session["cargo_description"]
            declared_val = session["declared_value"]
            cod_val = session.get("cod_amount", 0.0)
            cod_type = session.get("cod_payment_type", "cash")
            cod_str = "❌ Немає" if cod_val <= 0 else f"{int(cod_val)} грн ({'Картка' if cod_type == 'card' else 'Готівка'})"

            eval_res = await _evaluate_cod_limits(
                user_id=user_id,
                cod_val=cod_val,
                user_np_client=user_np_client,
                storage_manager=storage_manager,
                eff_settings=eff_settings,
                editing_ref=session.get("editing_draft_ref"),
            )
            warn_line = eval_res.get("warn_line", "")
            s_prof = eval_res.get("suggested_profile")

            profiles = storage_manager.get_sender_profiles(user_id)
            active_p = storage_manager.get_active_profile(user_id)
            has_multiple = len(profiles) > 1
            active_name = active_p.name if active_p else None
            sender_prefix = f"👤 *Відправник:* {active_name}\n" if (active_name and has_multiple) else ""

            card_text = (
                "📋 *Розпарсені дані отримувача для перевірки:*\n\n"
                f"{sender_prefix}"
                f"👤 *Отримувач:* {parsed_info.full_name}\n"
                f"📞 *Телефон:* `{parsed_info.phone}`\n"
                f"🏙 *Місто:* {city.description}\n"
                f"📦 *Пункт призначення:* {dest_desc}\n"
                f"📝 *Опис вантажу:* {cargo_desc}\n"
                f"💰 *Оціночна вартість:* {int(declared_val)} грн (Мін. 500 грн)\n"
                f"💵 *Накладений платіж:* {cod_str}\n"
                f"{warn_line}\n"
                "Перевірте дані та оберіть дію нижче:"
            )

            u_custom = storage_manager.get_user_settings(user_id)
            card_mask = u_custom.sender_card_mask

            await callback.message.edit_text(
                card_text,
                parse_mode="Markdown",
                reply_markup=get_confirmation_keyboard(
                    payer_type=session["payer_type"],
                    cargo_type=session["cargo_type"],
                    declared_value=declared_val,
                    cod_amount=cod_val,
                    cod_payment_type=cod_type,
                    sender_card_mask=card_mask,
                    session_id=session_id,
                    suggested_profile=s_prof,
                    active_profile_name=active_name,
                    has_multiple_profiles=has_multiple,
                ),
            )
            await callback.answer()
            return

        if action in ("confirm", "force_confirm"):
            editing_ref = session.get("editing_draft_ref")
            cod_amount = session.get("cod_amount", 0.0)

            # Check for COD limit exceeded unless user already force-confirmed
            if action == "confirm" and cod_amount and cod_amount > 0:
                eval_res = await _evaluate_cod_limits(
                    user_id=user_id,
                    cod_val=cod_amount,
                    user_np_client=user_np_client,
                    storage_manager=storage_manager,
                    eff_settings=eff_settings,
                    editing_ref=editing_ref,
                )
                if eval_res.get("is_exceeded"):
                    s_prof = eval_res.get("suggested_profile")
                    warn_card = (
                        "🚨 *УВАГА: Створення цієї ТТН перетне встановлену межу післяплати!*\n\n"
                        f"{eval_res['summary_text']}\n\n"
                        "⚠️ *Фінансовий моніторинг:* Перевищення встановленої безпечної межі може призвести до блокування або перевірки картки/рахунку у системі NovaPay.\n\n"
                        "Ви дійсно бажаєте зареєструвати накладну попри перевищення встановленого ліміту?"
                    )
                    await callback.message.edit_text(
                        warn_card,
                        parse_mode="Markdown",
                        reply_markup=get_limit_exceeded_confirmation_keyboard(
                            session_id=session_id,
                            suggested_profile=s_prof,
                        ),
                    )
                    await callback.answer("🚨 Увага: ліміт післяплати буде перевищено!", show_alert=True)
                    return

            # Pre-flight check: ensure departure city and warehouse are configured before calling NP API
            if not eff_settings.sender_city_ref or not eff_settings.sender_address_ref:
                await callback.answer("⚠️ Не вказано місто або відділення відправки!", show_alert=True)
                missing_info_msg = (
                    "⚠️ *Неможливо створити ТТН: не вказано пункт відправки!*\n\n"
                    "Для реєстрації накладної у Новій Пошті необхідно зазначити, звідки відправляється посилка.\n\n"
                    "Будь ласка, вкажіть ваше місто та відділення відправки:\n"
                    "1️⃣ `/set_city НазваМіста` (наприклад, `/set_city Київ`)\n"
                    "2️⃣ `/set_warehouse Номер` (наприклад, `/set_warehouse 1`)\n\n"
                    "💡 *Ваша поточна чернетка збережена!* Після вказання відділення створення накладної буде продовжено автоматично."
                )
                try:
                    await callback.message.edit_text(
                        missing_info_msg,
                        parse_mode="Markdown",
                        reply_markup=get_missing_sender_address_keyboard(session_id),
                    )
                except Exception:
                    pass
                return

            action_title = "Оновлення" if editing_ref else "Генерація"
            await callback.answer(f"{action_title} express-накладної...")
            await callback.message.edit_text(
                f"⏳ *Реєстрація отримувача та {action_title.lower()} express-накладної у базі Нової Пошти...*",
                parse_mode="Markdown",
            )

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
                            settlement_ref=getattr(city, "settlement_ref", None),
                            city_name=getattr(city, "description", None),
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
                    card_to_use = eff_settings.sender_card_ref or eff_settings.sender_card_mask
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
                        cod_payment_type=cod_payment_type,
                        payment_card=card_to_use if cod_payment_type == "card" else None,
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
                success_title = " успішно оновлено" if editing_ref else " успішно створено"
                cod_str = "❌ Немає" if not cod_amount or cod_amount <= 0 else f"{int(cod_amount)} грн"

                cod_limit_line = ""
                if cod_amount and cod_amount > 0:
                    eval_post = await _evaluate_cod_limits(
                        user_id=user_id,
                        cod_val=cod_amount,
                        user_np_client=user_np_client,
                        storage_manager=storage_manager,
                        eff_settings=eff_settings,
                        editing_ref=editing_ref,
                    )
                    new_tot = int(eval_post.get("new_total_sum", 0))
                    safe_l = eval_post.get("safe_limit")
                    if safe_l and safe_l > 0:
                        rem_val = max(0, int(safe_l - new_tot))
                        cod_limit_line = f"📊 *Місячний обсяг післяплати:* `{new_tot} грн` із {int(safe_l)} грн (залишок ліміту: `{rem_val} грн`)\n"

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
                    f"{cod_limit_line}"
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
                "raw_text": dummy_text,
                "initial_raw_text": dummy_text,
                "all_raw_texts": [dummy_text],
            }
            USER_ACTIVE_SESSIONS[user_id] = session_id
            USER_INITIAL_MESSAGE_TEXT[user_id] = dummy_text

            await _continue_processing_recipient_info(
                message=callback.message,
                user_id=user_id,
                session_id=session_id,
                parsed_info=parsed_info,
                status_msg=status_msg,
                raw_text=dummy_text,
                initial_raw_text=dummy_text,
                all_raw_texts=[dummy_text],
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
            USER_LAST_SCANSHEET_CONTEXT.pop(user_id, None)
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
            raw_text=session.get("raw_text"),
            initial_raw_text=session.get("initial_raw_text"),
            all_raw_texts=session.get("all_raw_texts"),
        )

    router._handle_combined_text_message = _handle_combined_text_message

