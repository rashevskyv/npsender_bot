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
