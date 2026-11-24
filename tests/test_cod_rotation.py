"""Tests for per-account COD switch, COD account rotation and the hourly payout-card reminder."""

import datetime
from zoneinfo import ZoneInfo

import pytest

from src.storage import UserSettingsManager, SenderProfile, UserCustomSettings, PendingCardReminder
import src.bot.reminders as reminders
from src.bot.reminders import format_card_reminder, run_card_reminders, send_card_reminder, CARD_REMINDER_INTERVAL
from src.bot.keyboards import (
    CardReminderCallback,
    ClientCardCallback,
    get_card_reminder_keyboard,
    get_client_card_keyboard,
)

UID = 555
# 2026-11-24 09:00 Kyiv: inside the 08:00-20:00 reminder window
T0 = datetime.datetime(2026, 11, 24, 9, 0, tzinfo=ZoneInfo("Europe/Kyiv")).timestamp()
HOUR = 3600


def _manager(tmp_path):
    return UserSettingsManager(
        filepath=str(tmp_path / "s.json"),
        drafts_filepath=str(tmp_path / "d.json"),
        scansheets_filepath=str(tmp_path / "ss.json"),
    )


def _with_profiles(tmp_path, ids=("a", "b", "c"), active="a"):
    sm = _manager(tmp_path)
    sm.data[str(UID)] = UserCustomSettings(
        nova_poshta_api_key="k",
        sender_phone="380991112233",
        profiles=[
            SenderProfile(id=i, name=f"Користувач {n}", nova_poshta_api_key="k", sender_phone="380991112233")
            for n, i in enumerate(ids, 1)
        ],
        active_profile_id=active,
    )
    return sm


def _active(sm):
    return sm.get_active_profile(UID).id


def test_cod_allowed_defaults_to_true(tmp_path):
    sm = _with_profiles(tmp_path)
    assert all(p.cod_allowed for p in sm.get_sender_profiles(UID))


def test_rotation_goes_round_robin_and_skips_disallowed(tmp_path):
    sm = _with_profiles(tmp_path)           # a -> b -> c -> a
    sm.set_cod_allowed(UID, "b", False)     # b is switched off
    assert sm.rotate_cod_profile(UID).id == "c" and _active(sm) == "c"   # a -> (skip b) -> c
    assert sm.rotate_cod_profile(UID).id == "a" and _active(sm) == "a"   # c -> a (wraps)
    assert sm.rotate_cod_profile(UID).id == "c"


def test_rotation_stays_put_without_another_allowed_account(tmp_path):
    sm = _with_profiles(tmp_path)
    sm.set_cod_allowed(UID, "b", False)
    sm.set_cod_allowed(UID, "c", False)
    assert sm.rotate_cod_profile(UID) is None and _active(sm) == "a"
    # a single account never rotates either
    assert _with_profiles(tmp_path, ids=("a",)).rotate_cod_profile(UID) is None


def test_disabling_the_active_account_moves_to_next_allowed(tmp_path):
    sm = _with_profiles(tmp_path)
    sm.set_cod_allowed(UID, "a", False)     # the handler then rotates away from the disabled active account
    assert sm.rotate_cod_profile(UID).id == "b"


def test_rotation_skips_account_without_api_key(tmp_path):
    sm = _with_profiles(tmp_path)
    sm.get_sender_profile(UID, "b").nova_poshta_api_key = ""
    assert sm.rotate_cod_profile(UID).id == "c"


def test_draft_owner_survives_reload(tmp_path):
    from src.storage import SavedDraft
    sm = _with_profiles(tmp_path)
    sm.add_user_draft(UID, SavedDraft(
        ref="r1", int_doc_number="T1", recipient_name="x", recipient_phone="1", city_description="c",
        warehouse_description="w", payer_type="Recipient", cargo_description="d", declared_value=1,
        cost=1, created_at="01.01.2026", profile_id="b"))
    assert _manager(tmp_path).get_user_drafts(UID)[0].profile_id == "b"


@pytest.mark.asyncio
async def test_reminders_work_on_a_reloaded_manager(tmp_path):
    sm = _with_profiles(tmp_path)
    sm.add_pending_card_reminder(UID, PendingCardReminder(int_doc_number="T1", cod_amount=1500))
    reloaded = _manager(tmp_path)          # settings come from JSON: values must be models, not dicts
    bot = _Bot()
    assert await run_card_reminders(bot, reloaded, now=T0) == 1
    assert "T1" in bot.sent[0][1]


def test_cod_allowed_survives_reload(tmp_path):
    sm = _with_profiles(tmp_path)
    sm.set_cod_allowed(UID, "b", False)
    sm.save_settings()
    reloaded = _manager(tmp_path)
    assert {p.id: p.cod_allowed for p in reloaded.get_sender_profiles(UID)} == {"a": True, "b": False, "c": True}


def test_pending_reminders_add_dedupe_remove_and_persist(tmp_path):
    sm = _with_profiles(tmp_path)
    sm.add_pending_card_reminder(UID, PendingCardReminder(int_doc_number="T1", account="Користувач 1", cod_amount=1500))
    sm.add_pending_card_reminder(UID, PendingCardReminder(int_doc_number="T2", cod_amount=500))
    sm.add_pending_card_reminder(UID, PendingCardReminder(int_doc_number="T1", cod_amount=2000))   # same TTN replaces
    pend = sm.get_user_settings(UID).pending_card_reminders
    assert [p.int_doc_number for p in pend] == ["T2", "T1"] and pend[1].cod_amount == 2000

    assert [p.int_doc_number for p in _manager(tmp_path).get_user_settings(UID).pending_card_reminders] == ["T2", "T1"]

    left = sm.remove_pending_card_reminder(UID, "T2")
    assert [p.int_doc_number for p in left] == ["T1"]
    assert sm.remove_pending_card_reminder(UID, "nope")[0].int_doc_number == "T1"   # unknown TTN is harmless
    assert sm.remove_pending_card_reminder(UID, "T1") == []


class _Bot:
    def __init__(self, fail=False):
        self.sent = []
        self.fail = fail

    async def send_message(self, uid, text, parse_mode=None, reply_markup=None):
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent.append((uid, text, reply_markup))


@pytest.mark.asyncio
async def test_reminder_repeats_hourly_until_done(tmp_path):
    sm = _with_profiles(tmp_path)
    sm.add_pending_card_reminder(UID, PendingCardReminder(int_doc_number="T1", cod_amount=1500))
    bot = _Bot()
    t0 = T0

    assert await run_card_reminders(bot, sm, now=t0) == 1                                 # never sent -> sent now
    assert await run_card_reminders(bot, sm, now=t0 + CARD_REMINDER_INTERVAL - 1) == 0    # not an hour yet
    assert await run_card_reminders(bot, sm, now=t0 + CARD_REMINDER_INTERVAL) == 1        # an hour later -> again
    assert await run_card_reminders(bot, sm, now=t0 + 2 * CARD_REMINDER_INTERVAL) == 1    # ...and again
    assert len(bot.sent) == 3

    sm.remove_pending_card_reminder(UID, "T1")                                            # "Що зроблено"
    assert await run_card_reminders(bot, sm, now=t0 + 5 * CARD_REMINDER_INTERVAL) == 0    # stops for good
    assert len(bot.sent) == 3


@pytest.mark.asyncio
async def test_one_message_lists_all_pending_with_a_button_each(tmp_path):
    sm = _with_profiles(tmp_path)
    sm.add_pending_card_reminder(UID, PendingCardReminder(int_doc_number="T1", account="Користувач 1", cod_amount=1500))
    sm.add_pending_card_reminder(UID, PendingCardReminder(int_doc_number="T2", cod_amount=700))
    bot = _Bot()
    assert await send_card_reminder(bot, sm, UID, now=1.0)
    uid, text, kb = bot.sent[0]
    assert uid == UID and "T1" in text and "T2" in text and "1500 грн" in text and "на сайті" in text
    assert [b[0].text for b in kb.inline_keyboard] == ["✅ Що зроблено — T1", "✅ Що зроблено — T2"]
    assert [CardReminderCallback.unpack(b[0].callback_data).ttn for b in kb.inline_keyboard] == ["T1", "T2"]


@pytest.mark.asyncio
async def test_failed_send_is_retried_and_never_raises(tmp_path):
    sm = _with_profiles(tmp_path)
    sm.add_pending_card_reminder(UID, PendingCardReminder(int_doc_number="T1", cod_amount=1500))
    assert await run_card_reminders(_Bot(fail=True), sm, now=T0) == 0       # no exception escapes
    assert sm.get_user_settings(UID).card_reminder_last_sent == 0.0               # not marked as sent
    assert await run_card_reminders(_Bot(), sm, now=T0 + 1) == 1                 # next tick retries


def test_format_card_reminder_mentions_website_not_app():
    text = format_card_reminder([PendingCardReminder(int_doc_number="T1", account="Користувач 2", cod_amount=900)])
    assert "на сайті Нової Пошти" in text and "не в додатку" in text and "Користувач 2" in text and "T1" in text


def test_client_card_keyboard_toggle_label_follows_state():
    def toggle(allowed):
        kb = get_client_card_keyboard(profile_id="a", cod_allowed=allowed)
        btn = next(b for row in kb.inline_keyboard for b in row if "наложку" in b.text)
        return btn.text, ClientCardCallback.unpack(btn.callback_data)

    text_on, cb_on = toggle(True)
    text_off, cb_off = toggle(False)
    assert "Вимкнути" in text_on and "Дозволити" in text_off
    assert cb_on.action == cb_off.action == "cod_toggle" and cb_on.profile_id == "a"
    assert len(get_card_reminder_keyboard(["1" * 14]).inline_keyboard[0][0].callback_data.encode()) <= 64


@pytest.mark.asyncio
async def test_no_hourly_reminders_outside_branch_hours(tmp_path):
    sm = _with_profiles(tmp_path)
    sm.add_pending_card_reminder(UID, PendingCardReminder(int_doc_number="T1", cod_amount=1500))
    bot = _Bot()
    assert await run_card_reminders(bot, sm, now=T0 + 10.9 * HOUR) == 1      # 19:54 -> still sends
    assert await run_card_reminders(bot, sm, now=T0 + 11 * HOUR) == 0        # 20:00 -> quiet
    assert await run_card_reminders(bot, sm, now=T0 + 22 * HOUR) == 0        # 07:00 next day -> quiet
    assert await run_card_reminders(bot, sm, now=T0 + 23 * HOUR) == 1        # 08:00 -> overdue one goes out


def test_users_keyboard_shows_cod_off_and_toggle_for_current_user(tmp_path):
    from src.bot.keyboards import get_users_management_keyboard, UserProfileCallback
    sm = _with_profiles(tmp_path)
    sm.set_cod_allowed(UID, "b", False)
    profs = sm.get_sender_profiles(UID)
    bal = {p.id: {"rem_sum": 5000.0} for p in profs}

    def texts(active):
        kb = get_users_management_keyboard(profs, active_profile_id=active, balances_map=bal)
        return [b for row in kb.inline_keyboard for b in row]

    btns = texts("a")
    assert [b.text for b in btns].count("🚫 Наложка відключена") == 1           # only b: no balance line for it
    assert sum("Залишок" in b.text for b in btns) == 2                          # a and c keep the balance
    toggle = next(b for b in btns if "наложку для поточного" in b.text)
    assert toggle.text.startswith("🚫 Виключити")
    cb = UserProfileCallback.unpack(toggle.callback_data)
    assert (cb.action, cb.profile_id) == ("cod_toggle", "a")

    assert next(b for b in texts("b") if "наложку для поточного" in b.text).text.startswith("✅ Увімкнути")


def test_name_with_phone_tail_goes_right_after_surname():
    from src.bot.keyboards import name_with_phone_tail
    assert name_with_phone_tail("Рашевський Владислав Сергійович", "380502559301") == "Рашевський (..9301) Владислав Сергійович"
    assert name_with_phone_tail("Іванов", "+38 (050) 111-22-33") == "Іванов (..2233)"
    assert name_with_phone_tail("Іванов Іван", "") == "Іванов Іван"
