"""Unit tests for Address/Courier Delivery flow and callbacks."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.ai.schemas import ParsedRecipientInfo
from src.bot.keyboards import AddressConfirmCallback, WaybillActionCallback, StreetSelectCallback
from src.bot.handlers import (
    PENDING_SESSIONS,
    USER_ACTIVE_SESSIONS,
    router,
)
from src.config import Settings
from src.nova_poshta.models import (
    CityInfo,
    StreetInfo,
    AddressSaveResult,
    CounterpartyRecipientResult,
    WaybillCreateResult,
)
from src.storage import UserCustomSettings, UserSettingsManager
from src.bot.handlers import register_handlers


def _get_handler(name: str):
    for h in reversed(router.callback_query.handlers):
        if name in str(h.callback):
            return h.callback
    raise RuntimeError(f"Handler {name} not found in router")


@pytest.fixture(autouse=True)
def setup_test_handlers(tmp_path):
    storage_file = str(tmp_path / "user_settings.json")
    drafts_file = str(tmp_path / "user_drafts.json")
    scansheets_file = str(tmp_path / "user_scansheets.json")
    manager = UserSettingsManager(
        filepath=storage_file,
        drafts_filepath=drafts_file,
        scansheets_filepath=scansheets_file,
    )
    register_handlers(
        settings=Settings(TELEGRAM_BOT_TOKEN="dummy"),
        ai_extractor=MagicMock(),
        np_client=MagicMock(),
        storage_manager=manager,
    )
    return manager


@pytest.mark.asyncio
async def test_process_address_confirm_callback_uses_user_id(setup_test_handlers):
    manager = setup_test_handlers
    user_id = 12345678
    session_id = "test-sess-addr"

    # Configure user in storage
    manager.update_user_settings(
        user_id,
        nova_poshta_api_key="test_np_key",
        ai_api_key="test_ai_key",
    )

    parsed_info = ParsedRecipientInfo(
        first_name="Арсеній",
        last_name="Жупник",
        middle_name="Олександрович",
        phone="+380502850704",
        city_name="Сміла",
        region_name="Черкаська",
        street_name="Віри Гордієнко",
        building_number="99",
        is_address_delivery=False,
        has_address_suspicion=True,
        is_recipient_info=True,
    )

    PENDING_SESSIONS[session_id] = {
        "parsed_info": parsed_info,
        "user_id": user_id,
    }
    USER_ACTIVE_SESSIONS[user_id] = session_id

    # Mock Telegram callback where callback.from_user is user, callback.message.from_user is bot (id 999999)
    callback = MagicMock()
    callback.from_user.id = user_id
    callback.message.from_user.id = 999999
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()

    callback_data = AddressConfirmCallback(action="choice", choice="courier", session_id=session_id)

    with patch("src.nova_poshta.client.NovaPoshtaClient.search_city", new_callable=AsyncMock) as mock_search_city, \
         patch("src.nova_poshta.client.NovaPoshtaClient.search_street", new_callable=AsyncMock) as mock_search_street:

        mock_search_city.return_value = [
            CityInfo(Ref="city-smila-ref", Description="Сміла", AreaDescription="Черкаська", RegionsDescription="")
        ]
        mock_search_street.return_value = [
            StreetInfo(Ref="street-vg-ref", Description="Віри Гордієнко", StreetsType="вул.", CityRef="city-smila-ref")
        ]

        handler = _get_handler("process_address_confirm_callback")
        await handler(callback, callback_data)

        assert PENDING_SESSIONS[session_id]["is_address_delivery"] is True
        assert PENDING_SESSIONS[session_id]["street_ref"] == "street-vg-ref"
        assert callback.message.edit_text.called
        call_args = callback.message.edit_text.call_args[0][0]
        assert "🏡 Адресна доставка" in call_args
        assert "вул. Віри Гордієнко" in call_args
        assert "буд. 99" in call_args


@pytest.mark.asyncio
async def test_confirm_waybill_address_delivery_creates_doors_waybill(setup_test_handlers):
    manager = setup_test_handlers
    user_id = 12345678
    session_id = "test-sess-confirm"

    manager.update_user_settings(
        user_id,
        nova_poshta_api_key="test_np_key",
        ai_api_key="test_ai_key",
    )

    parsed_info = ParsedRecipientInfo(
        first_name="Арсеній",
        last_name="Жупник",
        middle_name="Олександрович",
        phone="+380502850704",
        city_name="Сміла",
        street_name="Віри Гордієнко",
        building_number="99",
        flat_number="12",
        is_address_delivery=True,
        is_recipient_info=True,
    )

    city = CityInfo(Ref="city-smila-ref", Description="Сміла", AreaDescription="Черкаська", RegionsDescription="")

    PENDING_SESSIONS[session_id] = {
        "parsed_info": parsed_info,
        "city": city,
        "warehouse": None,
        "is_address_delivery": True,
        "street_name": "Віри Гордієнко",
        "street_ref": "street-vg-ref",
        "building_number": "99",
        "flat_number": "12",
        "destination_description": "🏡 Адресна доставка: вул. Віри Гордієнко, буд. 99, кв. 12",
        "payer_type": "Recipient",
        "cargo_type": "Parcel",
        "declared_value": 500.0,
        "cargo_description": "Посилка",
        "cod_amount": 0.0,
        "cod_payment_type": "cash",
        "user_id": user_id,
    }
    USER_ACTIVE_SESSIONS[user_id] = session_id

    callback = MagicMock()
    callback.from_user.id = user_id
    callback.message.from_user.id = 999999
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()

    callback_data = WaybillActionCallback(
        action="confirm",
        session_id=session_id,
    )

    with patch("src.nova_poshta.client.NovaPoshtaClient.create_recipient_counterparty", new_callable=AsyncMock) as mock_create_cp, \
         patch("src.nova_poshta.client.NovaPoshtaClient.create_counterparty_address", new_callable=AsyncMock) as mock_create_addr, \
         patch("src.nova_poshta.client.NovaPoshtaClient.create_waybill", new_callable=AsyncMock) as mock_create_wb:

        mock_create_cp.return_value = CounterpartyRecipientResult(
            counterparty_ref="cp-ref-123",
            contact_person_ref="contact-ref-123",
        )
        mock_create_addr.return_value = AddressSaveResult(
            Ref="addr-guid-created",
            Description="вул. Віри Гордієнко, 99, кв. 12",
        )
        mock_create_wb.return_value = WaybillCreateResult(
            int_doc_number="20450999999999",
            ref="doc-ref-created",
            cost=125.0,
            estimated_delivery_date="12.08.2026",
        )

        handler = _get_handler("process_waybill_callback")
        await handler(callback, callback_data)

        assert mock_create_addr.called
        assert mock_create_addr.call_args[1]["counterparty_ref"] == "cp-ref-123"
        assert mock_create_addr.call_args[1]["street_ref"] == "street-vg-ref"
        assert mock_create_addr.call_args[1]["building_number"] == "99"
        assert mock_create_addr.call_args[1]["flat"] == "12"

        assert mock_create_wb.called
        assert mock_create_wb.call_args[1]["recipient_warehouse_ref"] == "addr-guid-created"
        assert mock_create_wb.call_args[1]["service_type"] == "WarehouseDoors"

        assert callback.message.edit_text.called
        success_card = callback.message.edit_text.call_args[0][0]
        assert "20450999999999" in success_card
        assert "Адресна доставка" in success_card
        assert mock_create_wb.call_args[1]["service_type"] == "WarehouseDoors"

        assert callback.message.edit_text.called
        success_card = callback.message.edit_text.call_args[0][0]
        assert "20450999999999" in success_card
        assert "Адресна доставка" in success_card


@pytest.mark.asyncio
async def test_process_street_select_callback():
    session_id = "test_street_sel"
    user_id = 998877

    parsed_info = ParsedRecipientInfo(
        last_name="Жупник",
        first_name="Арсеній",
        middle_name="Олександрович",
        phone="380502850704",
        city_name="Сміла",
        street_name="Віри Гордієнко",
        building_number="99",
        is_address_delivery=True,
        is_recipient_info=True,
        cargo_description="планшет",
        declared_value=15000.0,
        cod_amount=15000.0,
        cod_payment_type="cash",
    )
    city = CityInfo(Ref="city-smila-ref", Description="Сміла", AreaDescription="Черкаська", RegionsDescription="")

    candidate_street1 = StreetInfo(Ref="street-vul-ref", Description="Гордієнко Віри", StreetsType="вул.", CityRef="city-smila-ref")
    candidate_street2 = StreetInfo(Ref="street-prov-ref", Description="Гордієнко Віри", StreetsType="пров.", CityRef="city-smila-ref")

    PENDING_SESSIONS[session_id] = {
        "parsed_info": parsed_info,
        "city": city,
        "street_candidates": [candidate_street1, candidate_street2],
        "building_number": "99",
        "is_address_delivery": True,
        "payer_type": "Recipient",
        "cargo_type": "Parcel",
        "declared_value": 15000.0,
        "cargo_description": "планшет",
        "cod_amount": 15000.0,
        "cod_payment_type": "cash",
        "user_id": user_id,
    }
    USER_ACTIVE_SESSIONS[user_id] = session_id

    callback = MagicMock()
    callback.from_user.id = user_id
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()

    callback_data = StreetSelectCallback(
        street_ref="street-vul-ref",
        session_id=session_id,
    )

    handler = _get_handler("process_street_select_callback")
    await handler(callback, callback_data)

    assert callback.message.edit_text.called
    updated_card = callback.message.edit_text.call_args[0][0]
    assert "вул. Гордієнко Віри" in updated_card
    assert "планшет" in updated_card
    assert "15000 грн" in updated_card

    # Verify session is updated
    session = PENDING_SESSIONS[session_id]
    assert session["street_ref"] == "street-vul-ref"
    assert session["street_name"] == "Гордієнко Віри"
    assert "вул. Гордієнко Віри" in session["destination_description"]


def _get_message_handler():
    for h in reversed(router.message.handlers):
        if "process_text_message" in str(h.callback):
            return h.callback
    raise RuntimeError("process_text_message not found in router")


@pytest.mark.asyncio
async def test_preserve_user_custom_settings_across_natural_language_updates(setup_test_handlers):
    """Verify that toggling payer to 'Sender' is preserved when user sends a text update like 'Опис вантажу сувенір'."""
    import asyncio
    manager = setup_test_handlers
    session_id = "test_sess_preserve"
    user_id = 112233

    manager.update_user_settings(
        user_id,
        nova_poshta_api_key="test_np_key",
        ai_api_key="test_ai_key",
    )

    parsed_info = ParsedRecipientInfo(
        last_name="Ковальчук",
        first_name="Р.",
        middle_name="О.",
        phone="0631344371",
        city_name="Житомир",
        warehouse_number=19,
        is_recipient_info=True,
        cargo_description="планшет",
        declared_value=500.0,
    )
    city = CityInfo(Ref="city-zhytomyr-ref", Description="Житомир", AreaDescription="Житомирська", RegionsDescription="")
    warehouse = MagicMock()
    warehouse.ref = "wh-19-ref"
    warehouse.description = "Відділення №19: вул. Чуднівська, 92"

    PENDING_SESSIONS[session_id] = {
        "parsed_info": parsed_info,
        "city": city,
        "warehouse": warehouse,
        "is_address_delivery": False,
        "destination_description": warehouse.description,
        "payer_type": "Sender",  # User toggled to Sender!
        "cargo_type": "Documents",  # User toggled to Documents!
        "declared_value": 2000.0,
        "cargo_description": "планшет",
        "cod_amount": 1000.0,
        "cod_payment_type": "card",
        "user_id": user_id,
    }
    USER_ACTIVE_SESSIONS[user_id] = session_id

    # Now user sends follow-up text "Опис вантажу сувенір"
    updated_parsed_info = ParsedRecipientInfo(
        last_name="Ковальчук",
        first_name="Р.",
        middle_name="О.",
        phone="0631344371",
        city_name="Житомир",
        warehouse_number=19,
        is_recipient_info=True,
        cargo_description="сувенір",  # Updated description
        payer_type=None,  # No explicit payer mentioned in text
        cargo_type=None,
    )

    message = MagicMock()
    message.from_user.id = user_id
    message.text = "Опис вантажу сувенір"
    status_mock = MagicMock()
    status_mock.edit_text = AsyncMock()
    message.answer = AsyncMock(return_value=status_mock)

    with patch("src.ai.extractor.AIExtractor.parse_text", new_callable=AsyncMock) as mock_parse, \
         patch("src.nova_poshta.client.NovaPoshtaClient.search_city", new_callable=AsyncMock) as mock_city, \
         patch("src.nova_poshta.client.NovaPoshtaClient.get_warehouse", new_callable=AsyncMock) as mock_wh:
        mock_parse.return_value = updated_parsed_info
        mock_city.return_value = [city]
        mock_wh.return_value = warehouse

        msg_handler = _get_message_handler()
        await msg_handler(message)
        from src.bot.handlers import USER_DEBOUNCE_TASKS
        if user_id in USER_DEBOUNCE_TASKS:
            await USER_DEBOUNCE_TASKS[user_id]

    # Session MUST have preserved:
    session = PENDING_SESSIONS[session_id]
    assert session["payer_type"] == "Sender", "Payer type 'Sender' must be preserved!"
    assert session["cargo_type"] == "Documents", "Cargo type 'Documents' must be preserved!"
    assert session["declared_value"] == 2000.0, "Declared value 2000.0 must be preserved!"
    assert session["cod_amount"] == 1000.0, "COD amount 1000.0 must be preserved!"
    assert session["cod_payment_type"] == "card", "COD payment type 'card' must be preserved!"
    assert session["cargo_description"] == "сувенір", "Cargo description must be updated to 'сувенір'!"


