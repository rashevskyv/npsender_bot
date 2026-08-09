"""Unit tests for Nova Poshta models and client structures."""

import pytest
from src.nova_poshta.models import CityInfo, WarehouseInfo, WaybillCreateResult, WaybillItemInfo


def test_city_info_model():
    city = CityInfo(
        Ref="db5c88e0-391c-11dd-90d9-001a92567626",
        Description="Київ",
        AreaDescription="Київська",
        RegionsDescription="",
    )
    assert city.ref == "db5c88e0-391c-11dd-90d9-001a92567626"
    assert city.description == "Київ"


def test_warehouse_info_model():
    wh = WarehouseInfo(
        Ref="12345-ref",
        Description="Відділення №5",
        Number="5",
        TypeOfWarehouse="841339c7-591a-42e2-8233-7a0a00f0ed6f",
        CityRef="db5c88e0-391c-11dd-90d9-001a92567626",
    )
    assert wh.number == "5"
    assert wh.description == "Відділення №5"


def test_waybill_result_model():
    res = WaybillCreateResult(
        int_doc_number="20450123456789",
        ref="ref-123",
        cost=110.5,
        estimated_delivery_date="08.08.2026",
    )
    assert res.int_doc_number == "20450123456789"
    assert res.cost == 110.5


def test_waybill_item_info_model():
    item = WaybillItemInfo(
        int_doc_number="20451506103956",
        state_name="Нова пошта очікує посилку",
        recipient_name="Юрченко Роман",
        city_recipient="Київ",
        address_recipient="Поштомат №26584",
        cost=100.0,
        description="Посилка",
    )
    assert item.int_doc_number == "20451506103956"
    assert item.cost == 100.0
    assert item.city_recipient == "Київ"


@pytest.mark.asyncio
async def test_get_documents_status_parsing():
    from src.config import Settings
    from src.nova_poshta.client import NovaPoshtaClient

    client = NovaPoshtaClient(Settings(TELEGRAM_BOT_TOKEN="dummy", NOVA_POSHTA_API_KEY="dummy"))

    async def mock_post(model_name, called_method, method_properties):
        return {
            "success": True,
            "data": [
                {
                    "Number": "204501",
                    "StatusCode": "1",
                    "Status": "Нова пошта очікує посилку від відправника",
                },
                {
                    "Number": "204502",
                    "StatusCode": "4",
                    "Status": "Відправлення у місті Київ",
                },
            ],
        }

    client._post = mock_post
    res = await client.get_documents_status(["204501", "204502"])

    assert len(res) == 2
    assert res["204501"]["is_shipped"] is False
    assert res["204502"]["is_shipped"] is True


def test_format_relative_delivery_date():
    import datetime
    from src.bot.handlers import format_relative_delivery_date

    now = datetime.date.today()
    today_str = now.strftime("%d.%m.%Y 18:00:00")
    tomorrow_str = (now + datetime.timedelta(days=1)).strftime("%d.%m.%Y 15:30")
    after_tomorrow_str = (now + datetime.timedelta(days=2)).strftime("%d.%m.%Y")
    future_str = (now + datetime.timedelta(days=5)).strftime("%d.%m.%Y 14:00")

    assert format_relative_delivery_date(today_str) == "Сьогодні о 18:00"
    assert format_relative_delivery_date(tomorrow_str) == "Завтра о 15:30"
    assert format_relative_delivery_date(after_tomorrow_str) == "Післязавтра"
    assert "14:00" in format_relative_delivery_date(future_str)


@pytest.mark.asyncio
async def test_outgoing_vs_incoming_separation():
    from src.config import Settings
    from src.nova_poshta.client import NovaPoshtaClient

    client = NovaPoshtaClient(Settings(TELEGRAM_BOT_TOKEN="dummy", NOVA_POSHTA_API_KEY="dummy"))

    # User profile: Юрченко Роман, 380995360818
    user_name = "Юрченко Роман Сергійович"
    user_phone = "380995360818"

    mock_docs = [
        # Outgoing shipment: User is Sender
        {
            "IntDocNumber": "20450001",
            "StateId": "4",
            "StateName": "У дорозі",
            "SenderContactPerson": "Юрченко Роман Сергійович",
            "SendersPhone": "380995360818",
            "RecipientContactPerson": "Іванов Іван Іванович",
            "RecipientsPhone": "380971112233",
            "CityRecipientDescription": "Львів",
            "RecipientAddressDescription": "Відділення №1",
            "Cost": "100",
            "Description": "Ноутбук",
        },
        # Incoming shipment: User is Recipient
        {
            "IntDocNumber": "20450002",
            "StateId": "4",
            "StateName": "У дорозі",
            "SenderContactPerson": "ТОВ Розетка",
            "SendersPhone": "380445000000",
            "RecipientContactPerson": "Юрченко Роман Сергійович",
            "RecipientsPhone": "380995360818",
            "CityRecipientDescription": "Київ",
            "RecipientAddressDescription": "Відділення №5",
            "Cost": "150",
            "Description": "Навушники",
        },
    ]

    async def mock_post(model_name, called_method, method_properties):
        return {"success": True, "data": mock_docs}

    client._post = mock_post

    outgoing = await client.get_outgoing_waybills(user_phone=user_phone, user_name=user_name)
    incoming = await client.get_incoming_waybills(user_phone=user_phone, user_name=user_name)

    assert len(outgoing) == 1
    assert outgoing[0].int_doc_number == "20450001"
    assert outgoing[0].recipient_name == "Іванов Іван Іванович"

    assert len(incoming) == 1
    assert incoming[0].int_doc_number == "20450002"
    assert incoming[0].sender_name == "ТОВ Розетка"


@pytest.mark.asyncio
async def test_waybills_caching_behavior():
    from src.config import Settings
    from src.nova_poshta.client import NovaPoshtaClient

    client = NovaPoshtaClient(Settings(TELEGRAM_BOT_TOKEN="dummy", NOVA_POSHTA_API_KEY="test_cache_key_123"))
    client.invalidate_waybills_cache()

    api_call_count = 0

    async def mock_post(model_name, called_method, method_properties):
        nonlocal api_call_count
        api_call_count += 1
        return {
            "success": True,
            "data": [
                {
                    "IntDocNumber": "20459999",
                    "StateId": "4",
                    "StateName": "У дорозі",
                    "SenderContactPerson": "Тест Відправник",
                    "SendersPhone": "380991112233",
                    "RecipientContactPerson": "Тест Отримувач",
                    "RecipientsPhone": "380994445566",
                    "CityRecipientDescription": "Київ",
                    "RecipientAddressDescription": "Відділення №1",
                    "Cost": "50",
                    "Description": "Кеш тест",
                }
            ],
        }

    client._post = mock_post

    # First call: cache miss, triggers API call
    inc1 = await client.get_incoming_waybills(user_phone="380994445566")
    assert api_call_count == 1
    assert len(inc1) == 1

    # Second call (outgoing right after incoming): cache HIT, 0 new API calls!
    out1 = await client.get_outgoing_waybills(user_phone="380991112233")
    assert api_call_count == 1
    assert len(out1) == 1

    client.invalidate_waybills_cache()
    inc2 = await client.get_incoming_waybills(user_phone="380994445566")
    assert api_call_count == 2
    assert len(inc2) == 1


@pytest.mark.asyncio
async def test_fetch_sender_profile_private_person_name_resolution():
    from src.config import Settings
    from src.nova_poshta.client import NovaPoshtaClient

    client = NovaPoshtaClient(Settings(TELEGRAM_BOT_TOKEN="dummy", NOVA_POSHTA_API_KEY="test_key"))

    async def mock_post(model_name, called_method, method_properties):
        if called_method == "getCounterparties":
            return {
                "success": True,
                "data": [
                    {
                        "Ref": "cp-ref-111",
                        "Description": "Приватна особа",
                        "CounterpartyType": "PrivatePerson",
                    }
                ],
            }
        elif called_method == "getCounterpartyContactPersons":
            return {
                "success": True,
                "data": [
                    {
                        "Ref": "contact-ref-222",
                        "Phones": "380995360818",
                        "LastName": "Юрченко",
                        "FirstName": "Роман",
                        "MiddleName": "Сергійович",
                    }
                ],
            }
        elif called_method == "getCounterpartyAddresses":
            return {
                "success": True,
                "data": [
                    {
                        "Ref": "addr-ref-333",
                        "CityRef": "city-ref-444",
                    }
                ],
            }
        return {"success": True, "data": []}

    client._post = mock_post

    profile = await client.fetch_sender_profile("test_key")

    assert profile["sender_name"] == "Юрченко Роман Сергійович"
    assert profile["sender_phone"] == "380995360818"
    assert profile["sender_counterparty_ref"] == "cp-ref-111"
    assert profile["sender_contact_ref"] == "contact-ref-222"


@pytest.mark.asyncio
async def test_create_and_delete_scan_sheet_methods():
    from src.config import Settings
    from src.nova_poshta.client import NovaPoshtaClient

    client = NovaPoshtaClient(Settings(TELEGRAM_BOT_TOKEN="dummy", NOVA_POSHTA_API_KEY="test_key"))

    recorded_calls = []

    async def mock_post(model_name, called_method, method_properties):
        recorded_calls.append((model_name, called_method, method_properties))
        if called_method == "insertDocuments":
            return {
                "success": True,
                "data": [
                    {
                        "Ref": "sheet-guid-123",
                        "Number": "105-79184007",
                        "DateTime": "2026-08-09 16:20:00",
                        "CountOfDocuments": 2,
                    }
                ],
            }
        elif called_method == "deleteScanSheet":
            return {"success": True, "data": []}
        return {"success": True, "data": []}

    client._post = mock_post

    res = await client.create_scan_sheet(["doc-ref-1", "doc-ref-2"])
    assert res.ref == "sheet-guid-123"
    assert res.number == "105-79184007"
    assert res.count_of_documents == 2
    assert recorded_calls[0] == (
        "ScanSheet",
        "insertDocuments",
        {"DocumentRefs": ["doc-ref-1", "doc-ref-2"]},
    )

    del_res = await client.delete_scan_sheet("sheet-guid-123")
    assert del_res is True
    assert recorded_calls[1] == (
        "ScanSheet",
        "deleteScanSheet",
        {"ScanSheetRefs": ["sheet-guid-123"]},
    )


@pytest.mark.asyncio
async def test_delete_waybill_method():
    from src.config import Settings
    from src.nova_poshta.client import NovaPoshtaClient

    client = NovaPoshtaClient(Settings(TELEGRAM_BOT_TOKEN="dummy", NOVA_POSHTA_API_KEY="test_key"))

    recorded_calls = []

    async def mock_post(model_name, called_method, method_properties):
        recorded_calls.append((model_name, called_method, method_properties))
        return {"success": True, "data": []}

    client._post = mock_post

    res = await client.delete_waybill("doc-guid-999")
    assert res is True
    assert recorded_calls[0] == (
        "InternetDocument",
        "delete",
        {"DocumentRefs": ["doc-guid-999"]},
    )





