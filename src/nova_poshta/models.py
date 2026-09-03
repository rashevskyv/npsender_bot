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


class TrackingDocumentDetails(BaseModel):
    """Full tracking details of an express waybill from Nova Poshta API."""

    number: str = ""
    status_code: str = ""
    status: str = ""
    ref_ew: Optional[str] = None

    # Sender info
    city_sender: str = ""
    warehouse_sender: str = ""
    warehouse_sender_address: str = ""
    sender_address: str = ""
    sender_full_name: str = ""
    sender_phone: str = ""

    # Recipient info
    city_recipient: str = ""
    warehouse_recipient: str = ""
    warehouse_recipient_address: str = ""
    warehouse_recipient_number: str = ""
    recipient_address: str = ""
    recipient_full_name: str = ""
    recipient_phone: str = ""

    # Dates
    date_created: str = ""
    scheduled_delivery_date: str = ""
    actual_delivery_date: str = ""
    recipient_date_time: str = ""
    date_scan: str = ""
    date_moving: str = ""
    date_first_day_storage: str = ""
    days_storage_cargo: str = ""

    # Cargo details
    cargo_type: str = ""
    cargo_description: str = ""
    document_weight: float = 0.0
    factual_weight: float = 0.0
    volume_weight: float = 0.0
    check_weight: float = 0.0
    seats_amount: int = 1
    announced_price: float = 0.0

    # Financial / Payment details
    document_cost: float = 0.0
    payer_type: str = ""
    payment_status: str = ""
    payment_method: str = ""
    express_waybill_payment_status: str = ""
    amount_to_pay: float = 0.0
    amount_paid: float = 0.0

    # COD / Afterpayment details
    afterpayment_cost: float = 0.0
    redelivery_sum: float = 0.0
    redelivery_payer: str = ""
    redelivery_card: str = ""
    redelivery_num: str = ""

    # Additional services & info
    is_light_return: bool = False
    light_return_number: str = ""
    undelivery_reasons: str = ""
    undelivery_reasons_date: str = ""
    undelivery_reasons_subtype: str = ""

    @classmethod
    def from_api_dict(cls, data: Dict[str, Any]) -> "TrackingDocumentDetails":
        """Construct TrackingDocumentDetails safely from Nova Poshta getStatusDocuments API item."""
        def _to_float(val: Any) -> float:
            if not val:
                return 0.0
            try:
                s = str(val).replace(" ", "").replace(",", ".").replace("грн", "").replace("₴", "").strip()
                return float(s)
            except (ValueError, TypeError):
                return 0.0

        def _to_int(val: Any, default: int = 1) -> int:
            if not val:
                return default
            try:
                digits = "".join(filter(str.isdigit, str(val)))
                return int(digits) if digits else default
            except (ValueError, TypeError):
                return default

        # Sender full name fallback
        sender_name = (
            str(data.get("SenderFullNameEW") or "").strip()
            or str(data.get("CounterpartySenderDescription") or "").strip()
        )

        # Recipient full name fallback
        recipient_name = (
            str(data.get("RecipientFullNameEW") or "").strip()
            or str(data.get("RecipientFullName") or "").strip()
            or str(data.get("CounterpartyRecipientDescription") or "").strip()
        )

        # Check light return
        lr_num = str(data.get("LightReturnNumber") or "").strip()
        is_lr = bool(lr_num and lr_num not in ("0", "False", "false", "None"))
        if not is_lr:
            t_fields = [
                str(data.get("CargoDescriptionString") or ""),
                str(data.get("AdditionalInformationEW") or ""),
                str(data.get("Status") or ""),
                str(data.get("ServiceType") or ""),
            ]
            if any("легке повернення" in t.lower() or "легкеповернення" in t.lower() for t in t_fields):
                is_lr = True

        return cls(
            number=str(data.get("Number") or data.get("DocumentNumber") or "").strip(),
            status_code=str(data.get("StatusCode") or "").strip(),
            status=str(data.get("Status") or data.get("StateName") or "").strip(),
            ref_ew=str(data.get("RefEW") or "").strip() or None,
            # Sender
            city_sender=str(data.get("CitySender") or "").strip(),
            warehouse_sender=str(data.get("WarehouseSender") or "").strip(),
            warehouse_sender_address=str(data.get("WarehouseSenderAddress") or "").strip(),
            sender_address=str(data.get("SenderAddress") or "").strip(),
            sender_full_name=sender_name,
            sender_phone=str(data.get("PhoneSender") or "").strip(),
            # Recipient
            city_recipient=str(data.get("CityRecipient") or "").strip(),
            warehouse_recipient=str(data.get("WarehouseRecipient") or "").strip(),
            warehouse_recipient_address=str(data.get("WarehouseRecipientAddress") or "").strip(),
            warehouse_recipient_number=str(data.get("WarehouseRecipientNumber") or "").strip(),
            recipient_address=str(data.get("RecipientAddress") or "").strip(),
            recipient_full_name=recipient_name,
            recipient_phone=str(data.get("PhoneRecipient") or "").strip(),
            # Dates
            date_created=str(data.get("DateCreated") or "").strip(),
            scheduled_delivery_date=str(data.get("ScheduledDeliveryDate") or "").strip(),
            actual_delivery_date=str(data.get("ActualDeliveryDate") or "").strip(),
            recipient_date_time=str(data.get("RecipientDateTime") or "").strip(),
            date_scan=str(data.get("DateScan") or "").strip(),
            date_moving=str(data.get("DateMoving") or "").strip(),
            date_first_day_storage=str(data.get("DateFirstDayStorage") or "").strip(),
            days_storage_cargo=str(data.get("DaysStorageCargo") or "").strip(),
            # Cargo
            cargo_type=str(data.get("CargoType") or "").strip(),
            cargo_description=str(data.get("CargoDescriptionString") or "").strip(),
            document_weight=_to_float(data.get("DocumentWeight")),
            factual_weight=_to_float(data.get("FactualWeight")),
            volume_weight=_to_float(data.get("VolumeWeight")),
            check_weight=_to_float(data.get("CheckWeight")),
            seats_amount=_to_int(data.get("SeatsAmount"), default=1),
            announced_price=_to_float(data.get("AnnouncedPrice")),
            # Finances
            document_cost=_to_float(data.get("DocumentCost")),
            payer_type=str(data.get("PayerType") or "").strip(),
            payment_status=str(data.get("PaymentStatus") or "").strip(),
            payment_method=str(data.get("PaymentMethod") or "").strip(),
            express_waybill_payment_status=str(data.get("ExpressWaybillPaymentStatus") or "").strip(),
            amount_to_pay=_to_float(data.get("ExpressWaybillAmountToPay") or data.get("AmountToPay")),
            amount_paid=_to_float(data.get("AmountPaid")),
            # COD
            afterpayment_cost=_to_float(data.get("AfterpaymentOnGoodsCost")),
            redelivery_sum=_to_float(data.get("RedeliverySum")),
            redelivery_payer=str(data.get("RedeliveryPayer") or "").strip(),
            redelivery_card=str(data.get("RedeliveryPaymentCardDescription") or "").strip(),
            redelivery_num=str(data.get("RedeliveryNum") or "").strip(),
            # Additional
            is_light_return=is_lr,
            light_return_number=lr_num,
            undelivery_reasons=str(data.get("UndeliveryReasons") or "").strip(),
            undelivery_reasons_date=str(data.get("UndeliveryReasonsDate") or "").strip(),
            undelivery_reasons_subtype=str(data.get("UndeliveryReasonsSubtypeDescription") or "").strip(),
        )

