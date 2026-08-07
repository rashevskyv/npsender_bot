"""Unit tests for Nova Poshta models and client structures."""

import pytest
from src.nova_poshta.models import CityInfo, WarehouseInfo, WaybillCreateResult


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
