"""Pydantic schemas for AI entity extraction."""

from typing import Optional
from pydantic import BaseModel, Field


class ParsedRecipientInfo(BaseModel):
    """Structured recipient details extracted from unstructured text."""

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
    settlement_type: Optional[str] = Field(
        default=None, description="Settlement type if specified (місто, село, смт)"
    )
    warehouse_number: Optional[int] = Field(
        default=None, description="Branch or postomat number (Номер відділення або поштомату)"
    )
    is_postomat: bool = Field(
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

    @property
    def full_name(self) -> str:
        """Construct full name string."""
        parts = [p for p in [self.last_name, self.first_name, self.middle_name] if p]
        return " ".join(parts) if parts else "N/A"
