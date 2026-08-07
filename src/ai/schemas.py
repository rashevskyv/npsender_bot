"""Pydantic schemas for AI entity extraction and conversational intent."""

from typing import Optional
from pydantic import BaseModel, Field, field_validator


class ParsedRecipientInfo(BaseModel):
    """Structured recipient details or conversational response from AI model."""

    is_recipient_info: Optional[bool] = Field(
        default=True,
        description="True if the message contains recipient data for Nova Poshta express waybill creation, False if it's conversational/chat",
    )
    conversational_response: Optional[str] = Field(
        default=None,
        description="Friendly response explaining bot capabilities, required recipient details, or answering general questions when is_recipient_info is False",
    )
    last_name: Optional[str] = Field(
        default=None, description="Recipient's last name (Прізвище)"
    )
    first_name: Optional[str] = Field(
        default=None, description="Recipient's first name (Ім'я)"
    )
    middle_name: Optional[str] = Field(
        default=None, description="Recipient's patronymic / middle name (По-батькові)"
    )
    phone: Optional[str] = Field(
        default=None, description="Normalized phone number (e.g., 380XXXXXXXXX or 0XXXXXXXXX)"
    )
    city_name: Optional[str] = Field(
        default=None, description="City, town, or village name (Назва населеного пункту)"
    )
    region_name: Optional[str] = Field(
        default=None, description="Oblast/Region name if specified (Область)"
    )
    district_name: Optional[str] = Field(
        default=None, description="District/Raion name if specified (Район)"
    )
    settlement_type: Optional[str] = Field(
        default=None, description="Settlement type if specified (місто, село, смт)"
    )
    warehouse_number: Optional[int] = Field(
        default=None, description="Branch or postomat number (Номер відділення або поштомату)"
    )
    is_postomat: Optional[bool] = Field(
        default=False, description="True if the user specified a postomat (поштомат)"
    )
    street_name: Optional[str] = Field(
        default=None, description="Street name for address delivery (Вулиця)"
    )
    building_number: Optional[str] = Field(
        default=None, description="Building/House number for address delivery (Будинок)"
    )
    flat_number: Optional[str] = Field(
        default=None, description="Apartment/Flat number for address delivery (Квартира)"
    )
    cargo_description: Optional[str] = Field(
        default=None, description="Specific description of items if mentioned in text"
    )
    declared_value: Optional[float] = Field(
        default=None, description="Declared value in UAH if specified in text"
    )

    @field_validator("is_postomat", mode="before")
    @classmethod
    def convert_postomat(cls, v):
        if v is None:
            return False
        return bool(v)

    @field_validator("is_recipient_info", mode="before")
    @classmethod
    def convert_recipient(cls, v):
        if v is None:
            return True
        return bool(v)

    @field_validator("warehouse_number", mode="before")
    @classmethod
    def convert_wh(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, str):
            digits = "".join(filter(str.isdigit, v))
            return int(digits) if digits else None
        try:
            return int(v)
        except (ValueError, TypeError):
            return None

    @field_validator("declared_value", mode="before")
    @classmethod
    def convert_val(cls, v):
        if v is None or v == "":
            return None
        if isinstance(v, str):
            try:
                digits_clean = v.replace(",", ".").strip()
                return float(digits_clean)
            except ValueError:
                return None
        try:
            return float(v)
        except (ValueError, TypeError):
            return None

    @property
    def full_name(self) -> str:
        """Construct full name string."""
        parts = [p for p in [self.last_name, self.first_name, self.middle_name] if p]
        return " ".join(parts) if parts else "N/A"
