"""Pydantic data models for Nova Poshta API responses and requests."""

from typing import Optional, List, Any
from pydantic import BaseModel, Field


class CityInfo(BaseModel):
    """Nova Poshta City entity."""

    ref: str = Field(..., alias="Ref")
    description: str = Field(..., alias="Description")
    area: Optional[str] = Field(default=None, alias="AreaDescription")
    region: Optional[str] = Field(default=None, alias="RegionsDescription")


class WarehouseInfo(BaseModel):
    """Nova Poshta Warehouse / Postomat entity."""

    ref: str = Field(..., alias="Ref")
    description: str = Field(..., alias="Description")
    number: str = Field(..., alias="Number")
    type_of_warehouse: str = Field(..., alias="TypeOfWarehouse")
    city_ref: str = Field(..., alias="CityRef")
    city_description: Optional[str] = Field(default=None, alias="CityDescription")


class CounterpartyRecipientResult(BaseModel):
    """Result of Counterparty/save operation."""

    counterparty_ref: str
    contact_person_ref: str


class WaybillCreateResult(BaseModel):
    """Result of InternetDocument/save operation."""

    int_doc_number: str  # TTN Number (e.g. 20450123456789)
    ref: str
    cost: float
    estimated_delivery_date: Optional[str] = None


class WaybillItemInfo(BaseModel):
    """Detailed information about an existing express waybill."""

    int_doc_number: str
    state_name: str
    recipient_name: str
    recipient_phone: Optional[str] = None
    city_recipient: str
    address_recipient: str
    cost: float
    description: str
    estimated_delivery_date: Optional[str] = None
    date_created: Optional[str] = None


class ScanSheetInfo(BaseModel):
    """Nova Poshta ScanSheet (Register) entity."""

    ref: str = Field(..., alias="Ref")
    number: str = Field(..., alias="Number")
    date_created: str = Field(default="", alias="DateTime")
    count_of_documents: int = Field(default=0, alias="CountOfDocuments")
