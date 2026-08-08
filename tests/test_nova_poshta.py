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

