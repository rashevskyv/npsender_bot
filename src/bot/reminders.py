"""Money-transfer report ("💸" button) and the "📬 Що забрати" digest (button + once-a-day reminder).

A "грошовий переказ" here is the money from a cash-on-delivery (накладений платіж)
shipment coming back to the sender. The important state is "waiting" — cash the
sender has not collected yet. See client.classify_transfer_status.

The digest groups, per account: client card + phone, incoming parcels that arrived and are
not picked up, and uncollected transfers; it adds a passport reminder from 30 000 грн.
"""

import asyncio
import datetime
import logging
import re
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

from aiogram.types import BufferedInputFile

from src.config import Settings
from src.storage import UserSettingsManager
from src.nova_poshta.client import NovaPoshtaClient, classify_transfer_status
from src.nova_poshta.models import CODItemInfo, WaybillItemInfo
from src.bot.keyboards import format_profile_display_name
from src.utils.barcode_gen import generate_client_card_image

logger = logging.getLogger(__name__)

# Nova Poshta asks for a passport / an active phone number at pickup from this sum (inclusive).
PASSPORT_THRESHOLD = 30000.0
# Max lines per list in one message (keeps the text under Telegram's 4096-char limit).
_MAX_LINES = 15
_MAX_PARCELS = 30
_MSG_LIMIT = 3800  # a body longer than this is sent as several messages

# (profile display name, its COD shipments for the period)
ProfileReport = Tuple[str, List[CODItemInfo]]


def _recent_year_months(months: int) -> List[Tuple[int, int]]:
    """Return [(year, month), ...] for the current month and `months-1` before it."""
    today = datetime.date.today()
    y, m = today.year, today.month
    out: List[Tuple[int, int]] = []
    for _ in range(max(1, months)):
        out.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    return out


async def gather_profiles_transfers(
    user_id: int,
    storage_manager: UserSettingsManager,
    global_settings: Settings,
    months: int = 1,
) -> List[ProfileReport]:
    """Fetch COD shipments for every sender profile of a user over the last `months`.

    The uncollected list must not be month-bound (a transfer from the 30th stays
    waiting into the next month), so the daily job passes months=2 to span the boundary.
    """
    profiles = storage_manager.get_sender_profiles(user_id)
    ym_list = _recent_year_months(months)

    async def _one(p) -> Optional[ProfileReport]:
        eff = storage_manager.get_effective_settings(user_id, global_settings, profile_id=p.id)
        if not eff.nova_poshta_api_key or not eff.sender_phone:
            return None
        items = await _profile_transfers(NovaPoshtaClient(eff), eff, ym_list, p.id)
        return format_profile_display_name(p, profiles), items

    results = await asyncio.gather(*[_one(p) for p in profiles])
    return [r for r in results if r]


async def _profile_transfers(client: NovaPoshtaClient, eff: Settings, ym_list, profile_id: str = "") -> List[CODItemInfo]:
    """COD shipments of one profile over the given (year, month) list, deduped by waybill number."""
    merged: dict = {}
    for (yy, mm) in ym_list:
        try:
            stats = await client.get_monthly_cod_stats(
                year=yy, month=mm,
                user_phone=eff.sender_phone,
                user_cp_ref=eff.sender_counterparty_ref,
            )
        except Exception as e:
            logger.warning(f"Transfer fetch failed for profile {profile_id} {yy}-{mm}: {e}")
            continue
        for it in stats.items:
            merged[it.int_doc_number] = it
    return list(merged.values())


def _waiting_items(items: List[CODItemInfo]) -> List[CODItemInfo]:
    waiting = [it for it in items if classify_transfer_status(it) == "waiting"]
    waiting.sort(key=lambda it: it.cod_amount, reverse=True)
    return waiting


def format_transfers_report(reports: List[ProfileReport], title: str) -> str:
    """Build the Ukrainian report for the "Грошові перекази" button.

    Highlights НЕ ЗАБРАНО (cash waiting). Collected / card / in-transit are one-line summaries.
    """
    lines = [f"💸 *Грошові перекази — {title}*", ""]
    grand_wait_sum = 0.0
    grand_wait_cnt = 0

    if not reports:
        return "💸 *Грошові перекази*\n\nНемає налаштованих користувачів з ключем Нової Пошти."

    for name, items in reports:
        lines.append(f"👤 *{name}*")
        if not items:
            lines.append("  _немає переказів за період_\n")
            continue

        buckets: dict = {"waiting": [], "collected": [], "card": [], "pending": [], "refused": []}
        for it in items:
            buckets[classify_transfer_status(it)].append(it)

        waiting = _waiting_items(items)
        if waiting:
            w_sum = sum(it.cod_amount for it in waiting)
            grand_wait_sum += w_sum
            grand_wait_cnt += len(waiting)
            lines.append(f"🔴 *Не забрано — {int(w_sum)} грн ({len(waiting)} шт):*")
            for it in waiting[:20]:
                city = it.city_recipient or "—"
                rec = it.recipient_name or "—"
                lines.append(f"  • `{int(it.cod_amount)} грн` → {city}, {rec} (ТТН `{it.int_doc_number}`)")
            if len(waiting) > 20:
                lines.append(f"  … ще {len(waiting) - 20} шт")
        else:
            lines.append("🔴 Не забрано: немає ✅")

        def _sum(key: str) -> Tuple[int, int]:
            b = buckets[key]
            return len(b), int(sum(it.cod_amount for it in b))

        c_cnt, c_sum = _sum("collected")
        card_cnt, card_sum = _sum("card")
        p_cnt, p_sum = _sum("pending")
        r_cnt, r_sum = _sum("refused")
        if c_cnt:
            lines.append(f"🟢 Забрано: {c_cnt} шт, {c_sum} грн")
        if card_cnt:
            lines.append(f"💳 На картку: {card_cnt} шт, {card_sum} грн")
        if p_cnt:
            lines.append(f"🚚 Ще їдуть: {p_cnt} шт, {p_sum} грн")
        if r_cnt:
            lines.append(f"↩️ Відмови: {r_cnt} шт, {r_sum} грн")
        lines.append("")

    lines.append("──────────────")
    lines.append(f"🔴 *Разом не забрано: {int(grand_wait_sum)} грн ({grand_wait_cnt} переказів)*")
    return "\n".join(lines)


@dataclass
class AccountDigest:
    """Everything that waits to be collected on ONE sender account (profile)."""

    name: str
    eff: Settings
    transfers: List[CODItemInfo] = field(default_factory=list)  # cash payouts not collected yet
    incoming: List[WaybillItemInfo] = field(default_factory=list)  # arrived, not picked up yet

    @property
    def is_empty(self) -> bool:
        return not self.transfers and not self.incoming

    @property
    def needs_passport(self) -> bool:
        """Any waiting transfer or incoming amount due of PASSPORT_THRESHOLD or more."""
        return any(t.cod_amount >= PASSPORT_THRESHOLD for t in self.transfers) or any(
            w.amount_due >= PASSPORT_THRESHOLD for w in self.incoming
        )


async def gather_account_digests(
    user_id: int,
    storage_manager: UserSettingsManager,
    global_settings: Settings,
    months: int = 2,
) -> List[AccountDigest]:
    """One pass per account: money transfers and arrived incoming parcels are fetched together."""
    profiles = storage_manager.get_sender_profiles(user_id)
    ym_list = _recent_year_months(months)

    async def _one(p) -> Optional[AccountDigest]:
        eff = storage_manager.get_effective_settings(user_id, global_settings, profile_id=p.id)
        if not eff.nova_poshta_api_key or not eff.sender_phone:
            return None
        client = NovaPoshtaClient(eff)
        transfers, incoming = await asyncio.gather(
            _profile_transfers(client, eff, ym_list, p.id),
            client.get_uncollected_incoming(user_phone=eff.sender_phone),
            return_exceptions=True,
        )
        if isinstance(transfers, Exception):
            logger.warning(f"Transfers failed for profile {p.id}: {transfers}")
            transfers = []
        if isinstance(incoming, Exception):
            logger.warning(f"Incoming failed for profile {p.id}: {incoming}")
            incoming = []
        return AccountDigest(
            name=format_profile_display_name(p, profiles),
            eff=eff,
            transfers=_waiting_items(transfers),
            incoming=incoming,
        )

    # One profile at a time: parallel profiles trip NP's "To many requests" limit and could drop data.
    results = [await _one(p) for p in profiles]
    return [r for r in results if r]


async def build_account_card(eff: Settings) -> Optional[bytes]:
    """Client-card PNG with a scannable barcode for the account (loyalty card, else phone). None on failure."""
    try:
        full_name = eff.sender_name or ""
        phone = eff.sender_phone or ""
        card_id = ""
        info = await NovaPoshtaClient(eff).get_loyalty_info(api_key_override=eff.nova_poshta_api_key)
        if info:
            full_name = info.get("full_name") or full_name
            phone = info.get("phone") or phone
            card_id = info.get("loyalty_card") or info.get("user_login") or ""
        clean_phone = "".join(ch for ch in phone if ch.isdigit())
        if card_id:
            barcode_val, barcode_lbl = card_id, card_id
        else:
            barcode_val = clean_phone or "0000000000"
            barcode_lbl = f"+{clean_phone}" if clean_phone else barcode_val
        return generate_client_card_image(
            full_name=full_name or "Клієнт Нової Пошти",
            phone=phone,
            card_number=card_id,
            barcode_data=barcode_val,
            barcode_label=barcode_lbl,
        )
    except Exception as e:
        logger.warning(f"Client card build failed: {e}")
        return None


def format_digest_header(d: AccountDigest) -> str:
    """Photo caption: the account name and its phone as plain text (the card image carries the barcode)."""
    return f"👤 *{d.name}*\n📞 `{d.eff.sender_phone}`"


def _branch_label(address: Optional[str]) -> str:
    """Branch for grouping: NP address without the weight limit, e.g. 'м. Київ, Відділення №5: вул. X, 1'."""
    addr = re.sub(r"\s*\(до [^)]*\)", "", address or "").strip()
    return addr if addr and addr != "N/A" else "Відділення не вказано"


def _parcel_line(w: WaybillItemInfo) -> str:
    """One parcel: number, what to pay (COD + unpaid delivery), storage fee, sender."""
    flag = "🛂 " if w.amount_due >= PASSPORT_THRESHOLD else ""
    parts = []
    if w.cod_amount > 0:
        parts.append(f"післяплата {int(w.cod_amount)}")
    if w.delivery_due > 0:
        parts.append(f"доставка {int(w.delivery_due)}")
    due = f"{flag}до сплати `{int(w.amount_due)} грн` ({' + '.join(parts)})" if parts else "сплачено"
    storage = f" · зберігання {int(w.storage_fee)} грн" if w.storage_fee > 0 else ""
    sender = f" · від {w.sender_name}" if w.sender_name and w.sender_name != "N/A" else ""
    return f"  • `{w.int_doc_number}` · {due}{storage}{sender}"


def format_digest_body(d: AccountDigest) -> str:
    """Text under the card: passport reminder, waybill numbers to pick up, uncollected money transfers."""
    lines: List[str] = []
    if d.needs_passport:
        limit = f"{int(PASSPORT_THRESHOLD):,}".replace(",", " ")
        lines.append(f"🛂 *Є сума від {limit} грн* — візьміть паспорт або майте активний номер телефону.")
        lines.append("")

    if d.incoming:
        total_due = int(sum(w.amount_due for w in d.incoming))
        lines.append(f"📥 *Посилки до отримання — {len(d.incoming)} шт, до сплати {total_due} грн*")
        groups: dict = {}
        for w in d.incoming:
            groups.setdefault(_branch_label(w.address_recipient), []).append(w)
        # Branches with the most money first, then by name.
        ordered = sorted(groups.items(), key=lambda kv: (-sum(w.amount_due for w in kv[1]), kv[0]))
        shown = 0
        for branch, parcels in ordered:
            if shown >= _MAX_PARCELS:
                break
            due = int(sum(w.amount_due for w in parcels))
            lines.append(f"📍 *{branch}* — {len(parcels)} шт, до сплати {due} грн")
            for w in sorted(parcels, key=lambda x: -x.amount_due):
                if shown >= _MAX_PARCELS:
                    break
                lines.append(_parcel_line(w))
                shown += 1
        if len(d.incoming) > shown:
            lines.append(f"  … ще {len(d.incoming) - shown} шт")
        lines.append("")

    if d.transfers:
        total = int(sum(t.cod_amount for t in d.transfers))
        lines.append(f"💸 *Не забрані грошові перекази — {total} грн ({len(d.transfers)} шт):*")
        for t in d.transfers[:_MAX_LINES]:
            flag = "🛂 " if t.cod_amount >= PASSPORT_THRESHOLD else ""
            lines.append(
                f"  • {flag}`{int(t.cod_amount)} грн` → {t.city_recipient or '—'}, "
                f"{t.recipient_name or '—'} (ТТН `{t.int_doc_number}`)"
            )
        if len(d.transfers) > _MAX_LINES:
            lines.append(f"  … ще {len(d.transfers) - _MAX_LINES} шт")
    return "\n".join(lines).strip()


async def _send_markdown(bot, chat_id: int, text: str) -> None:
    """Send Markdown text; if Telegram rejects the markup (odd names), resend it as plain text."""
    try:
        await bot.send_message(chat_id, text, parse_mode="Markdown")
    except Exception:
        await bot.send_message(chat_id, text.replace("*", "").replace("`", ""))


async def send_account_digest(bot, chat_id: int, d: AccountDigest) -> None:
    """Send one account: client card (name + phone caption), then the lists below it."""
    header = format_digest_header(d)
    card = await build_account_card(d.eff)
    sent_card = False
    if card:
        try:
            await bot.send_photo(
                chat_id,
                BufferedInputFile(card, filename="client_card.png"),
                caption=header,
                parse_mode="Markdown",
            )
            sent_card = True
        except Exception as e:
            logger.warning(f"Failed to send client card to {chat_id}: {e}")
    if not sent_card:
        await _send_markdown(bot, chat_id, header)
    for part in _split_message(format_digest_body(d)):
        await _send_markdown(bot, chat_id, part)


def _split_message(text: str, limit: int = _MSG_LIMIT) -> List[str]:
    """Split text on line boundaries so each part fits Telegram's message limit."""
    parts: List[str] = []
    cur = ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > limit:
            parts.append(cur.strip())
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur.strip())
    return parts or [text]


async def run_transfer_check(bot, storage_manager: UserSettingsManager, settings: Settings) -> int:
    """One pass: send every bot user a digest per account that has something to collect. Returns users notified."""
    notified_users = 0
    for uid_str in list(storage_manager.data.keys()):
        try:
            uid = int(uid_str)
        except (ValueError, TypeError):
            continue
        try:
            digests = await gather_account_digests(uid, storage_manager, settings, months=2)
        except Exception as e:
            logger.warning(f"Daily check failed for user {uid_str}: {e}")
            continue

        sent_any = False
        for d in digests:
            if d.is_empty:
                continue
            try:
                await send_account_digest(bot, uid, d)
                sent_any = True
            except Exception as e:
                logger.warning(f"Failed to send digest to {uid}: {e}")
        if sent_any:
            notified_users += 1
    return notified_users


async def daily_transfer_reminder_loop(
    bot, storage_manager: UserSettingsManager, settings: Settings, hour: int = 10
) -> None:
    """Run run_transfer_check once a day at the given local hour. No external scheduler."""
    logger.info(f"Daily transfer reminder scheduled for {hour:02d}:00 local time.")
    while True:
        now = datetime.datetime.now()
        nxt = now.replace(hour=hour, minute=0, second=0, microsecond=0)
        if nxt <= now:
            nxt += datetime.timedelta(days=1)
        await asyncio.sleep((nxt - now).total_seconds())
        try:
            count = await run_transfer_check(bot, storage_manager, settings)
            logger.info(f"Daily transfer check done; notified {count} user(s).")
        except Exception as e:
            logger.error(f"Daily transfer check error: {e}", exc_info=True)
