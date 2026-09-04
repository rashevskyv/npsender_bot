"""Unit tests for express waybill tracking via Nova Poshta API."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.config import Settings
from src.nova_poshta.client import NovaPoshtaClient
from src.nova_poshta.models import TrackingDocumentDetails
from src.bot.handlers import (
    extract_ttn_from_text,
    is_tracking_intent,
    format_tracking_card,
    USER_TRACKING_WAITING,
)
from src.bot.keyboards import (
    TrackActionCallback,
    get_tracking_keyboard,
    get_main_reply_keyboard,
)


def test_extract_ttn_from_text():
    """Test extracting 14-digit and 11-digit waybill numbers from various text formats."""
    # 1. Direct standard 14 digits
    assert extract_ttn_from_text("20450123456789") == "20450123456789"
    assert extract_ttn_from_text("59000123456789") == "59000123456789"
    assert extract_ttn_from_text("10000123456789") == "10000123456789"

    # 2. Digits with spaces and hyphens
    assert extract_ttn_from_text("2045 0123 4567 89") == "20450123456789"
    assert extract_ttn_from_text("2045-0123-4567-89") == "20450123456789"
    assert extract_ttn_from_text("20 4501 2345 6789") == "20450123456789"

    # 3. Phrases with TTN
    assert extract_ttn_from_text("відстеж 20450123456789") == "20450123456789"
    assert extract_ttn_from_text("де посилка 20450123456789?") == "20450123456789"
    assert extract_ttn_from_text("ТТН: 2045 0123 4567 89") == "20450123456789"
    assert extract_ttn_from_text("/track 20450123456789") == "20450123456789"

    # 4. 11-digit waybills
    assert extract_ttn_from_text("20450123456") == "20450123456"

    # 5. Negative cases: recipient info should not be mistaken for TTN
    recipient_msg = "Юрченко Роман Сергійович 0995360818 Київ поштомат 26584"
    assert extract_ttn_from_text(recipient_msg) is None

    # Phone numbers
    assert extract_ttn_from_text("0995360818") is None
    assert extract_ttn_from_text("+380995360818") is None

    # Bank cards
    assert extract_ttn_from_text("4441 1111 2222 3333") is None

    # Short words / empty
    assert extract_ttn_from_text("") is None
    assert extract_ttn_from_text("привіт") is None


def test_is_tracking_intent():
    """Test detecting user tracking intent."""
    assert is_tracking_intent("відстеж посилку") is True
    assert is_tracking_intent("де моя посилка?") is True
    assert is_tracking_intent("статус ТТН") is True
    assert is_tracking_intent("трекінг накладної") is True
    assert is_tracking_intent("track parcel") is True

    assert is_tracking_intent("привіт як справи") is False
    assert is_tracking_intent("створи накладну на Київ") is False


def test_tracking_document_details_from_api_dict():
    """Test parsing complete Nova Poshta API response into TrackingDocumentDetails."""
    raw_api_data = {
        "Number": "20451506103956",
        "StatusCode": "7",
        "Status": "Прибув у відділення",
        "RefEW": "ref-123",
        "CitySender": "Київ",
        "WarehouseSender": "Відділення №1",
        "SenderFullNameEW": "Мартинюк Владислав",
        "PhoneSender": "+380991112233",
        "CityRecipient": "Львів",
        "WarehouseRecipient": "Відділення №15: вул. Городоцька, 20",
        "RecipientFullNameEW": "Шевченко Тарас",
        "PhoneRecipient": "+380679998877",
        "DateCreated": "01.09.2026 10:30",
        "ScheduledDeliveryDate": "03.09.2026",
        "ActualDeliveryDate": "",
        "DateFirstDayStorage": "08.09.2026",
        "CargoType": "Parcel",
        "CargoDescriptionString": "Електроніка",
        "DocumentWeight": "2.5",
        "VolumeWeight": "1.8",
        "SeatsAmount": "1",
        "AnnouncedPrice": "3500",
        "DocumentCost": "85.00",
        "PayerType": "Recipient",
        "PaymentStatus": "Сплачено",
        "AfterpaymentOnGoodsCost": "1500.00",
        "RedeliveryPaymentCardDescription": "537541******1234",
        "AmountToPay": "1500.00",
        "LightReturnNumber": "LR12345",
    }

    details = TrackingDocumentDetails.from_api_dict(raw_api_data)

    assert details.number == "20451506103956"
    assert details.status_code == "7"
    assert details.status == "Прибув у відділення"
    assert details.city_sender == "Київ"
    assert details.city_recipient == "Львів"
    assert details.sender_full_name == "Мартинюк Владислав"
    assert details.recipient_full_name == "Шевченко Тарас"
    assert details.document_weight == 2.5
    assert details.announced_price == 3500.0
    assert details.document_cost == 85.0
    assert details.afterpayment_cost == 1500.0
    assert details.redelivery_card == "537541******1234"
    assert details.is_light_return is True
    assert details.date_first_day_storage == "08.09.2026"


def test_format_tracking_card_in_transit():
    """Test formatting a tracking card for an in-transit shipment with COD."""
    details = TrackingDocumentDetails(
        number="20451506103956",
        status_code="4",
        status="Посилка прямує до міста Львів",
        city_sender="Київ",
        warehouse_sender="Відділення №1: вул. Пирогівський шлях, 135",
        sender_full_name="Іван Іванов",
        city_recipient="Львів",
        warehouse_recipient="Відділення №15: вул. Городоцька, 20",
        recipient_full_name="Петро Петренко",
        date_created="02.09.2026",
        scheduled_delivery_date="04.09.2026",
        cargo_description="Одяг та взуття",
        document_weight=1.5,
        volume_weight=0.0,
        announced_price=1200.0,
        document_cost=70.0,
        payer_type="Recipient",
        payment_status="Сплачено",
        afterpayment_cost=500.0,
        amount_to_pay=500.0,
        is_light_return=True,
    )

    card = format_tracking_card(details)

    assert "20451506103956" in card
    assert "🚚 *Статус:* Посилка прямує до міста Львів" in card
    assert "🔄 *Послуга:* Легке повернення" in card
    assert "📤 *Звідки:* Київ, Відділення №1" in card
    assert "📥 *Куди:* Львів, Відділення №15" in card
    assert "Одяг та взуття" in card
    assert "1.5 кг" in card
    assert "1200 грн" in card
    assert "Вартість доставки: 70.00 грн" in card
    assert "💰 *Накладений платіж:* 500.00 грн" in card
    assert "Разом до сплати при отриманні:" in card


def test_format_tracking_card_not_found():
    """Test formatting a tracking card when TTN is deleted or not found."""
    details = TrackingDocumentDetails(
        number="20450000000000",
        status_code="3",
        status="Номер не знайдено",
    )

    card = format_tracking_card(details)

    assert "20450000000000" in card
    assert "❌ *Статус:* Номер не знайдено" in card
    assert "Перевірте правильність номера ТТН" in card
    # Should not show full route headers for missing parcels
    assert "📍 *Маршрут:*" not in card


@pytest.mark.asyncio
async def test_nova_poshta_client_track_document():
    """Test track_document method of NovaPoshtaClient with mock."""
    client = NovaPoshtaClient(Settings(TELEGRAM_BOT_TOKEN="dummy", NOVA_POSHTA_API_KEY="dummy"))

    async def mock_post(model_name, called_method, method_properties):
        assert model_name == "TrackingDocument"
        assert called_method == "getStatusDocuments"
        doc = method_properties["Documents"][0]
        assert doc["DocumentNumber"] == "20451234567890"
        return {
            "success": True,
            "data": [
                {
                    "Number": "20451234567890",
                    "StatusCode": "9",
                    "Status": "Відправлення отримано",
                    "CitySender": "Дніпро",
                    "CityRecipient": "Одеса",
                    "DocumentCost": "90.00",
                }
            ],
        }

    client._post = mock_post
    result = await client.track_document("2045 1234 5678 90")

    assert result is not None
    assert result.number == "20451234567890"
    assert result.status_code == "9"
    assert result.status == "Відправлення отримано"
    assert result.city_sender == "Дніпро"
    assert result.city_recipient == "Одеса"
    assert result.document_cost == 90.0


@pytest.mark.asyncio
async def test_nova_poshta_client_track_document_error():
    """Test track_document returns None on API error."""
    client = NovaPoshtaClient(Settings(TELEGRAM_BOT_TOKEN="dummy", NOVA_POSHTA_API_KEY="dummy"))

    async def mock_post_fail(*args, **kwargs):
        raise RuntimeError("API Error")

    client._post = mock_post_fail
    result = await client.track_document("20451234567890")
    assert result is None


def test_keyboards_tracking():
    """Test tracking keyboard creation and callback packing."""
    kb = get_tracking_keyboard("20450123456789")
    assert len(kb.inline_keyboard) == 2

    row0 = kb.inline_keyboard[0]
    assert row0[0].text == "📱 Показати штрихкод"
    assert row0[1].text == "🔄 Оновити"

    row1 = kb.inline_keyboard[1]
    assert "https://novaposhta.ua/tracking/?cargo_number=20450123456789" in row1[0].url

    # Check callback data can be unpacked
    cb_data = TrackActionCallback.unpack(row0[0].callback_data)
    assert cb_data.action == "barcode"
    assert cb_data.doc_number == "20450123456789"

    main_kb = get_main_reply_keyboard()
    button_texts = [btn.text for row in main_kb.keyboard for btn in row]
    assert "🔍 Відстежити ТТН" in button_texts


def _get_msg_handler(name: str):
    from src.bot.handlers import router
    for h in reversed(router.message.handlers):
        if name in str(h.callback):
            return h.callback
    raise RuntimeError(f"Handler {name} not found in router")


def _get_cb_handler(name: str):
    from src.bot.handlers import router
    for h in reversed(router.callback_query.handlers):
        if name in str(h.callback):
            return h.callback
    raise RuntimeError(f"Handler {name} not found in router")


@pytest.fixture
def setup_track_handlers(tmp_path):
    from src.storage import UserSettingsManager
    from src.bot.handlers import register_handlers

    storage_file = str(tmp_path / "user_settings.json")
    drafts_file = str(tmp_path / "user_drafts.json")
    scansheets_file = str(tmp_path / "user_scansheets.json")
    manager = UserSettingsManager(
        filepath=storage_file,
        drafts_filepath=drafts_file,
        scansheets_filepath=scansheets_file,
    )
    settings = Settings(
        TELEGRAM_BOT_TOKEN="dummy",
        NOVA_POSHTA_API_KEY="dummy",
    )
    register_handlers(
        settings=settings,
        ai_extractor=MagicMock(),
        np_client=MagicMock(),
        storage_manager=manager,
    )
    return manager


@pytest.mark.asyncio
async def test_cmd_track_with_args(setup_track_handlers):
    """Test /track 20450123456789 directly displays tracking card."""
    user_id = 998877
    cmd_handler = _get_msg_handler("cmd_track")

    message = MagicMock()
    message.from_user.id = user_id
    message.text = "/track 20450123456789"
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    message.answer = AsyncMock(return_value=status_msg)

    fake_details = TrackingDocumentDetails(
        number="20450123456789",
        status_code="4",
        status="Прямує до відділення",
        city_sender="Київ",
        city_recipient="Одеса",
        document_cost=80.0,
    )

    with patch("src.nova_poshta.client.NovaPoshtaClient.track_document", new_callable=AsyncMock) as mock_track:
        mock_track.return_value = fake_details
        await cmd_handler(message)

        mock_track.assert_called_once_with("20450123456789")
        status_msg.edit_text.assert_called()
        call_args = status_msg.edit_text.call_args[0][0]
        assert "20450123456789" in call_args
        assert "Прямує до відділення" in call_args


@pytest.mark.asyncio
async def test_cmd_track_without_args(setup_track_handlers):
    """Test /track without args prompts for TTN and sets waiting state."""
    user_id = 998878
    USER_TRACKING_WAITING.discard(user_id)
    cmd_handler = _get_msg_handler("cmd_track")

    message = MagicMock()
    message.from_user.id = user_id
    message.text = "/track"
    message.answer = AsyncMock()

    await cmd_handler(message)

    assert user_id in USER_TRACKING_WAITING
    message.answer.assert_called_once()
    prompt = message.answer.call_args[0][0]
    assert "Надішліть номер накладної" in prompt


@pytest.mark.asyncio
async def test_process_text_message_ttn_direct(setup_track_handlers):
    """Test sending a 14-digit TTN in chat directly triggers tracking."""
    user_id = 998879
    text_handler = _get_msg_handler("process_text_message")

    message = MagicMock()
    message.from_user.id = user_id
    message.text = "2045 0123 4567 89"
    status_msg = MagicMock()
    status_msg.edit_text = AsyncMock()
    message.answer = AsyncMock(return_value=status_msg)

    fake_details = TrackingDocumentDetails(
        number="20450123456789",
        status_code="9",
        status="Відправлення отримано",
        city_sender="Харків",
        city_recipient="Київ",
    )

    with patch("src.nova_poshta.client.NovaPoshtaClient.track_document", new_callable=AsyncMock) as mock_track:
        mock_track.return_value = fake_details
        await text_handler(message)

        mock_track.assert_called_once_with("20450123456789")
        status_msg.edit_text.assert_called()
        call_args = status_msg.edit_text.call_args[0][0]
        assert "20450123456789" in call_args
        assert "Відправлення отримано" in call_args


@pytest.mark.asyncio
async def test_process_track_callback_refresh_and_barcode(setup_track_handlers):
    """Test TrackActionCallback refresh and barcode handling."""
    user_id = 998880
    cb_handler = _get_cb_handler("process_track_callback")

    # 1. Test refresh
    cb_refresh = MagicMock()
    cb_refresh.from_user.id = user_id
    cb_refresh.answer = AsyncMock()
    cb_refresh.message.edit_text = AsyncMock()

    fake_details = TrackingDocumentDetails(
        number="20450123456789",
        status_code="7",
        status="Прибув у відділення",
    )

    with patch("src.nova_poshta.client.NovaPoshtaClient.track_document", new_callable=AsyncMock) as mock_track:
        mock_track.return_value = fake_details
        await cb_handler(cb_refresh, TrackActionCallback(action="refresh", doc_number="20450123456789"))

        mock_track.assert_called_once_with("20450123456789")
        cb_refresh.message.edit_text.assert_called_once()
        text_arg = cb_refresh.message.edit_text.call_args[0][0]
        assert "Прибув у відділення" in text_arg

    # 2. Test barcode
    cb_barcode = MagicMock()
    cb_barcode.from_user.id = user_id
    cb_barcode.answer = AsyncMock()
    cb_barcode.message.answer_photo = AsyncMock()

    await cb_handler(cb_barcode, TrackActionCallback(action="barcode", doc_number="20450123456789"))
    cb_barcode.message.answer_photo.assert_called_once()
    assert "Штрихкод для експрес-накладної" in cb_barcode.message.answer_photo.call_args[1]["caption"]


def test_tracking_document_details_type_hints():
    """Verify that type hints on TrackingDocumentDetails and from_api_dict resolve without NameError (e.g. Dict, Any)."""
    import typing
    hints_cls = typing.get_type_hints(TrackingDocumentDetails)
    assert "number" in hints_cls
    hints_method = typing.get_type_hints(TrackingDocumentDetails.from_api_dict)
    assert "data" in hints_method


