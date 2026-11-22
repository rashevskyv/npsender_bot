"""Money-transfer (COD payout) report for the button, and the once-a-day uncollected-transfer reminder.

A "грошовий переказ" here is the money from a cash-on-delivery (накладений платіж)
shipment coming back to the sender. The important state is "waiting" — cash the
sender has not collected yet. See client.classify_transfer_status.
"""

import asyncio
import datetime
import logging
from typing import List, Tuple, Optional

from src.config import Settings
from src.storage import UserSettingsManager
from src.nova_poshta.client import NovaPoshtaClient, classify_transfer_status
from src.nova_poshta.models import CODItemInfo
from src.bot.keyboards import format_profile_display_name

logger = logging.getLogger(__name__)

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
        client = NovaPoshtaClient(eff)
        merged: dict = {}
        for (yy, mm) in ym_list:
            try:
                stats = await client.get_monthly_cod_stats(
                    year=yy, month=mm,
                    user_phone=eff.sender_phone,
                    user_cp_ref=eff.sender_counterparty_ref,
                )
            except Exception as e:
                logger.warning(f"Transfer fetch failed for profile {p.id} {yy}-{mm}: {e}")
                continue
            for it in stats.items:
                merged[it.int_doc_number] = it
        return format_profile_display_name(p, profiles), list(merged.values())

    results = await asyncio.gather(*[_one(p) for p in profiles])
    return [r for r in results if r]


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


def format_transfers_reminder(items: List[Tuple[str, CODItemInfo]]) -> str:
    """Build the daily reminder listing uncollected cash transfers."""
    lines = ["🔔 *Не забрані грошові перекази — є що забрати!*", ""]
    total = 0.0
    for name, it in items:
        city = it.city_recipient or "—"
        rec = it.recipient_name or "—"
        lines.append(f"👤 {name}: `{int(it.cod_amount)} грн` → {city}, {rec} (ТТН `{it.int_doc_number}`)")
        total += it.cod_amount
    lines.append("")
    lines.append(f"💰 *Разом не забрано: {int(total)} грн ({len(items)} переказів)*")
    return "\n".join(lines)


async def run_transfer_check(bot, storage_manager: UserSettingsManager, settings: Settings) -> int:
    """One pass: remind every bot user about ALL their uncollected cash transfers. Returns users reminded."""
    notified_users = 0
    for uid_str in list(storage_manager.data.keys()):
        try:
            uid = int(uid_str)
        except (ValueError, TypeError):
            continue
        try:
            reports = await gather_profiles_transfers(uid, storage_manager, settings, months=2)
        except Exception as e:
            logger.warning(f"Transfer check failed for user {uid_str}: {e}")
            continue

        waiting: List[Tuple[str, CODItemInfo]] = [
            (name, it) for name, items in reports for it in _waiting_items(items)
        ]
        if not waiting:
            continue

        # Send all uncollected, chunked so a long list never trips Telegram's 4096-char limit.
        sent_any = False
        for i in range(0, len(waiting), 30):
            chunk = waiting[i:i + 30]
            try:
                await bot.send_message(uid, format_transfers_reminder(chunk), parse_mode="Markdown")
                sent_any = True
            except Exception as e:
                logger.warning(f"Failed to send transfer reminder to {uid}: {e}")
                break
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
