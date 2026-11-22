"""Tests for money-transfer (COD payout) classification, report, and daily uncollected reminder."""

import pytest
from unittest.mock import AsyncMock

from src.nova_poshta.client import classify_transfer_status
from src.nova_poshta.models import CODItemInfo
from src.storage import UserSettingsManager, SenderProfile, UserCustomSettings
from src.config import Settings
import src.bot.reminders as reminders
from src.bot.reminders import format_transfers_report, format_transfers_reminder, run_transfer_check


def _item(num, amount, pay="cash", state_id="9", state_name="Отримано",
          received=True, transit=False, refused=False, draft=False, city="Київ", rec="Петро"):
    return CODItemInfo(
        int_doc_number=num, date_created="2026-11-10", cod_amount=amount,
        cod_payment_type=pay, state_id=state_id, state_name=state_name,
        recipient_name=rec, city_recipient=city,
        is_received=received, is_in_transit=transit, is_refused=refused, is_draft=draft,
    )


def test_classify_transfer_status():
    # cash, received, generic -> waiting (НЕ ЗАБРАНО)
    assert classify_transfer_status(_item("1", 1000, state_id="9")) == "waiting"
    assert classify_transfer_status(_item("1b", 1000, state_id="10")) == "waiting"
    # cash, paid out (state 11) -> collected
    assert classify_transfer_status(
        _item("2", 1000, state_id="11", state_name="Грошовий переказ виплачено")) == "collected"
    # card, received -> card (nothing to collect)
    assert classify_transfer_status(_item("3", 1000, pay="card")) == "card"
    # still in transit -> pending
    assert classify_transfer_status(
        _item("4", 1000, state_id="5", state_name="Прямує", received=False, transit=True)) == "pending"
    # refused
    assert classify_transfer_status(
        _item("5", 1000, state_id="102", state_name="Відмова", received=False, refused=True)) == "refused"
    # draft
    assert classify_transfer_status(
        _item("6", 1000, state_id="1", state_name="Чернетка", received=False, draft=True)) == "draft"


def test_format_transfers_report_highlights_waiting():
    reports = [
        ("Іван", [
            _item("100", 1500, state_id="9"),                 # waiting
            _item("101", 2500, state_id="11", state_name="виплачено"),  # collected
            _item("102", 800, pay="card"),                    # card
        ]),
    ]
    text = format_transfers_report(reports, "Листопад 2026")
    assert "Не забрано — 1500 грн (1 шт)" in text
    assert "100" in text                      # waiting TTN listed
    assert "Забрано: 1 шт, 2500 грн" in text
    assert "На картку: 1 шт, 800 грн" in text
    assert "Разом не забрано: 1500 грн (1 переказів)" in text


def test_format_transfers_report_empty():
    assert "Немає налаштованих користувачів" in format_transfers_report([], "Листопад 2026")


class _FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, uid, text, parse_mode=None):
        self.sent.append((uid, text))


@pytest.mark.asyncio
async def test_run_transfer_check_sends_all_waiting_every_run(tmp_path, monkeypatch):
    storage = UserSettingsManager(
        filepath=str(tmp_path / "s.json"),
        drafts_filepath=str(tmp_path / "d.json"),
        scansheets_filepath=str(tmp_path / "ss.json"),
    )
    storage.data["555"] = UserCustomSettings(
        nova_poshta_api_key="k", sender_phone="380991112233",
        profiles=[SenderProfile(id="p1", name="Іван", nova_poshta_api_key="k", sender_phone="380991112233")],
        active_profile_id="p1",
    )

    reports = [("Іван", [_item("900", 1200, state_id="9"), _item("901", 3400, state_id="9")])]
    monkeypatch.setattr(reminders, "gather_profiles_transfers", AsyncMock(return_value=reports))

    settings = Settings(TELEGRAM_BOT_TOKEN="t", NOVA_POSHTA_API_KEY="k", AI_API_KEY="a")
    bot = _FakeBot()

    # Every run lists all uncollected transfers (no "already notified" suppression).
    notified = await run_transfer_check(bot, storage, settings)
    assert notified == 1
    assert len(bot.sent) == 1
    assert "900" in bot.sent[0][1] and "901" in bot.sent[0][1]

    notified2 = await run_transfer_check(bot, storage, settings)
    assert notified2 == 1
    assert len(bot.sent) == 2
    assert "900" in bot.sent[1][1] and "901" in bot.sent[1][1]


@pytest.mark.asyncio
async def test_run_transfer_check_silent_when_nothing_waiting(tmp_path, monkeypatch):
    storage = UserSettingsManager(
        filepath=str(tmp_path / "s.json"),
        drafts_filepath=str(tmp_path / "d.json"),
        scansheets_filepath=str(tmp_path / "ss.json"),
    )
    storage.data["555"] = UserCustomSettings(nova_poshta_api_key="k")
    # all collected / card -> nothing to remind about
    reports = [("Іван", [_item("900", 1200, state_id="11", state_name="виплачено"),
                         _item("901", 3400, pay="card")])]
    monkeypatch.setattr(reminders, "gather_profiles_transfers", AsyncMock(return_value=reports))

    settings = Settings(TELEGRAM_BOT_TOKEN="t", NOVA_POSHTA_API_KEY="k", AI_API_KEY="a")
    bot = _FakeBot()
    notified = await run_transfer_check(bot, storage, settings)
    assert notified == 0
    assert bot.sent == []


def test_format_transfers_reminder_lists_items():
    text = format_transfers_reminder([("Іван", _item("900", 1200, state_id="9", city="Львів"))])
    assert "900" in text and "1200" in text and "Львів" in text
    assert "Разом не забрано: 1200 грн (1 переказів)" in text
