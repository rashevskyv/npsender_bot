"""Tests for money-transfer classification/report and the "what to collect" digest (button + daily)."""

import pytest
from unittest.mock import AsyncMock

from src.nova_poshta.client import classify_transfer_status, is_arrived_for_pickup, NovaPoshtaClient
from src.nova_poshta.models import CODItemInfo, WaybillItemInfo
from src.storage import UserSettingsManager, SenderProfile, UserCustomSettings
from src.config import Settings
import src.bot.reminders as reminders
from src.bot.reminders import (
    AccountDigest,
    format_transfers_report,
    format_digest_header,
    format_digest_body,
    run_transfer_check,
)


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


def _incoming(num, cod=0.0, state_id="7", sender="Олег", addr="м. Київ, Відділення №1 (до 30 кг): вул. А, 1",
              delivery=0.0, storage=0.0):
    return WaybillItemInfo(
        int_doc_number=num, state_name="Прибув у відділення", state_id=state_id,
        recipient_name="Я", sender_name=sender, city_recipient="Київ",
        address_recipient=addr, cost=0.0, cod_amount=cod, delivery_due=delivery,
        storage_fee=storage, description="Посилка",
    )


def _eff(phone="380991112233"):
    return Settings(TELEGRAM_BOT_TOKEN="t", NOVA_POSHTA_API_KEY="k", AI_API_KEY="a", SENDER_PHONE=phone)


def test_is_arrived_for_pickup():
    assert is_arrived_for_pickup("7", "Прибув у відділення")
    assert is_arrived_for_pickup("8", "")
    assert is_arrived_for_pickup("", "Прибув у поштомат")
    assert not is_arrived_for_pickup("5", "У дорозі")            # in transit
    assert not is_arrived_for_pickup("9", "Отримано")            # collected
    assert not is_arrived_for_pickup("104", "Змінено адресу")


@pytest.mark.asyncio
async def test_get_uncollected_incoming_keeps_only_arrived_and_parses_cod(monkeypatch):
    raw = [
        {"IntDocNumber": "A1", "StateId": "7", "StateName": "Прибув у відділення",
         "AfterpaymentOnGoodsCost": "1500", "RedeliverySum": "1500", "Cost": "300",
         "SenderContactPerson": "Олег", "CityRecipientDescription": "Київ"},
        {"IntDocNumber": "A2", "StateId": "7", "StateName": "Прибув у відділення",
         "AfterpaymentOnGoodsCost": "", "RedeliverySum": "0"},   # "0" string must not become COD
        {"IntDocNumber": "A5", "StateId": "7", "StateName": "Прибув у відділення",
         "DocumentCost": 180, "ExpressWaybillPaymentStatus": "NeedPayment", "StoragePrice": 35},
        {"IntDocNumber": "A6", "StateId": "7", "StateName": "Прибув у відділення",
         "DocumentCost": 145, "ExpressWaybillPaymentStatus": "Payed"},     # sender already paid
        {"IntDocNumber": "A3", "StateId": "5", "StateName": "У дорозі", "RedeliverySum": "999"},
        {"IntDocNumber": "A4", "StateId": "9", "StateName": "Отримано", "RedeliverySum": "999"},
    ]
    client = NovaPoshtaClient(_eff())
    monkeypatch.setattr(client, "_fetch_raw_incoming_waybills_with_cache", AsyncMock(return_value=raw))
    items = await client.get_uncollected_incoming(user_phone="380991112233")
    assert [i.int_doc_number for i in items] == ["A1", "A2", "A5", "A6"]
    by = {i.int_doc_number: i for i in items}
    assert by["A1"].cod_amount == 1500.0 and by["A1"].amount_due == 1500.0
    assert by["A2"].cod_amount == 0.0
    assert by["A5"].delivery_due == 180.0 and by["A5"].amount_due == 180.0   # unpaid delivery is due
    assert by["A5"].storage_fee == 35.0 and by["A5"].amount_due == 180.0     # storage not in the sum
    assert by["A6"].delivery_due == 0.0 and by["A6"].amount_due == 0.0       # already paid


def test_needs_passport_threshold_is_inclusive():
    eff = _eff()
    assert not AccountDigest("A", eff, transfers=[_item("1", 29999)]).needs_passport
    assert AccountDigest("A", eff, transfers=[_item("1", 30000)]).needs_passport
    assert AccountDigest("A", eff, incoming=[_incoming("2", cod=45000)]).needs_passport
    assert not AccountDigest("A", eff, incoming=[_incoming("2", cod=500)]).needs_passport
    assert AccountDigest("A", eff).is_empty


def test_format_digest_lists_numbers_and_passport_note():
    d = AccountDigest(
        "Користувач 1", _eff(),
        transfers=[_item("T900", 31000, city="Львів")],
        incoming=[_incoming("I100", cod=1500), _incoming("I101")],
    )
    header = format_digest_header(d)
    assert "Користувач 1" in header and "380991112233" in header      # phone as text
    body = format_digest_body(d)
    assert "I100" in body and "I101" in body and "T900" in body       # waybill numbers
    assert "до сплати `1500 грн` (післяплата 1500)" in body
    assert "30 000" in body and "паспорт" in body                     # passport reminder
    # no passport note below the threshold
    low = AccountDigest("A", _eff(), incoming=[_incoming("I1", cod=500)])
    assert "паспорт" not in format_digest_body(low)


class _FakeBot:
    def __init__(self):
        self.sent = []   # (kind, chat_id, text)

    async def send_message(self, uid, text, parse_mode=None, reply_markup=None):
        self.sent.append(("text", uid, text))

    async def send_photo(self, uid, photo, caption=None, parse_mode=None):
        self.sent.append(("photo", uid, caption))


def _storage(tmp_path):
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
    return storage


@pytest.mark.asyncio
async def test_run_transfer_check_sends_card_then_lists_every_run(tmp_path, monkeypatch):
    digests = [AccountDigest("Іван", _eff(), transfers=[_item("900", 1200)], incoming=[_incoming("I7", cod=100)])]
    monkeypatch.setattr(reminders, "gather_account_digests", AsyncMock(return_value=digests))
    monkeypatch.setattr(reminders, "build_account_card", AsyncMock(return_value=b"png"))
    bot = _FakeBot()
    storage = _storage(tmp_path)

    # Same content every run (no "already notified" suppression): card photo, then the list below it.
    for run in (1, 2):
        assert await run_transfer_check(bot, storage, _eff()) == 1
        kinds = [k for k, *_ in bot.sent[(run - 1) * 2:]]
        assert kinds[:2] == ["photo", "text"]
    assert len(bot.sent) == 4
    assert "380991112233" in bot.sent[0][2]                       # phone text in the caption
    assert "900" in bot.sent[1][2] and "I7" in bot.sent[1][2]     # numbers below the card


@pytest.mark.asyncio
async def test_run_transfer_check_falls_back_to_text_without_card(tmp_path, monkeypatch):
    digests = [AccountDigest("Іван", _eff(), incoming=[_incoming("I7")])]
    monkeypatch.setattr(reminders, "gather_account_digests", AsyncMock(return_value=digests))
    monkeypatch.setattr(reminders, "build_account_card", AsyncMock(return_value=None))
    bot = _FakeBot()
    assert await run_transfer_check(bot, _storage(tmp_path), _eff()) == 1
    assert [k for k, *_ in bot.sent] == ["text", "text"]          # header + list, no photo


@pytest.mark.asyncio
async def test_run_transfer_check_silent_when_nothing_waiting(tmp_path, monkeypatch):
    monkeypatch.setattr(reminders, "gather_account_digests",
                        AsyncMock(return_value=[AccountDigest("Іван", _eff())]))
    bot = _FakeBot()
    assert await run_transfer_check(bot, _storage(tmp_path), _eff()) == 0
    assert bot.sent == []


def test_format_digest_groups_incoming_by_branch_with_amounts():
    b1 = "м. Київ, Відділення №1 (до 30 кг): вул. А, 1"
    b2 = "м. Львів, Відділення №7 (до 30 кг на одне місце): вул. Б, 2"
    d = AccountDigest("Користувач 1", _eff(), incoming=[
        _incoming("I1", delivery=180, addr=b1),
        _incoming("I2", cod=1000, delivery=100, addr=b1),
        _incoming("I3", addr=b2),                       # paid -> nothing to pay
        _incoming("I4", addr=""),                       # branch unknown
    ])
    body = format_digest_body(d)
    assert "4 шт, до сплати 1280 грн" in body           # total over all parcels
    assert "📍 *м. Київ, Відділення №1: вул. А, 1* — 2 шт, до сплати 1280 грн" in body
    assert "(до 30 кг" not in body                       # weight limit stripped from the branch
    assert "📍 *м. Львів, Відділення №7: вул. Б, 2* — 1 шт, до сплати 0 грн" in body
    assert "Відділення не вказано" in body
    assert body.index("I2") > body.index("№1") and body.index("I3") > body.index("№7")  # under its branch
    assert "до сплати `1100 грн` (післяплата 1000 + доставка 100)" in body
    assert "сплачено" in body                            # I3
    # storage is shown but not summed
    assert "зберігання 35 грн" in format_digest_body(AccountDigest("A", _eff(), incoming=[_incoming("I5", delivery=180, storage=35)]))


def test_split_message_respects_limit():
    text = chr(10).join(f"line {i} " + "x" * 90 for i in range(100))
    parts = reminders._split_message(text, limit=1000)
    assert len(parts) > 1 and all(len(p) <= 1000 for p in parts)
    assert chr(10).join(parts) == text
