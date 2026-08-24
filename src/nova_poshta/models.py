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

    @property
    def is_postomat(self) -> bool:
        """Check if warehouse is a postomat."""
        desc = (self.description or "").lower()
        t_type = (self.type_of_warehouse or "").lower()
        return "поштомат" in desc or "postomat" in desc or "f9316480" in t_type or "841339c7" in t_type

    @property
    def warehouse_number(self) -> Optional[int]:
        """Parse warehouse number as integer."""
        if not self.number:
            return None
        digits = "".join(filter(str.isdigit, str(self.number)))
        return int(digits) if digits else None


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
    ref: Optional[str] = None
    state_name: str
    state_id: Optional[str] = None
    recipient_name: str
    recipient_phone: Optional[str] = None
    sender_name: Optional[str] = None
    sender_phone: Optional[str] = None
    city_recipient: str
    address_recipient: str
    cost: float
    declared_value: float = 500.0
    cod_amount: float = 0.0
    cod_payment_type: str = "cash"
    payer_type: str = "Recipient"
    description: str
    estimated_delivery_date: Optional[str] = None
    date_created: Optional[str] = None
    is_light_return: bool = False


class ScanSheetInfo(BaseModel):
    """Nova Poshta ScanSheet (Register) entity."""

    ref: str = Field(..., alias="Ref")
    number: str = Field(..., alias="Number")
    date_created: str = Field(default="", alias="DateTime")
    count_of_documents: int = Field(default=0, alias="CountOfDocuments")
    is_printed: bool = Field(default=False)


class StreetInfo(BaseModel):
    """Nova Poshta Street entity."""

    ref: str = Field(..., alias="Ref")
    description: str = Field(..., alias="Description")
    streets_type: str = Field(default="вул.", alias="StreetsType")
    city_ref: str = Field(..., alias="CityRef")


class AddressSaveResult(BaseModel):
    """Result of Address/save operation."""

    ref: str = Field(..., alias="Ref")
    description: Optional[str] = Field(default=None, alias="Description")


class CODItemInfo(BaseModel):
    """Information about a single waybill shipment with cash on delivery (накладений платіж)."""

    int_doc_number: str
    ref: Optional[str] = None
    date_created: str
    cod_amount: float
    cod_payment_type: str = "cash"  # "cash" | "card"
    state_id: str
    state_name: str
    recipient_name: str
    recipient_phone: Optional[str] = None
    city_recipient: str
    description: str = "Посилка"
    is_received: bool = False
    is_in_transit: bool = False
    is_refused: bool = False
    is_draft: bool = False


class CODMonthlyStats(BaseModel):
    """Monthly summary statistics for Cash On Delivery (накладений платіж) shipments."""

    year: int
    month: int
    month_name: str  # e.g. "Серпень 2026"
    from_date: str   # "01.MM.YYYY"
    to_date: str     # "DD.MM.YYYY"

    total_count: int = 0
    total_sum: float = 0.0

    received_count: int = 0
    received_sum: float = 0.0

    in_transit_count: int = 0
    in_transit_sum: float = 0.0

    refused_count: int = 0
    refused_sum: float = 0.0

    drafts_count: int = 0
    drafts_sum: float = 0.0

    items: List[CODItemInfo] = Field(default_factory=list)

