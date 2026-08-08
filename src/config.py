"""Application configuration using Pydantic BaseSettings."""

from typing import Literal
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration class loading from environment variables or .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram Bot
    telegram_bot_token: str = Field(..., alias="TELEGRAM_BOT_TOKEN")

    # Nova Poshta API
    nova_poshta_api_key: str = Field(..., alias="NOVA_POSHTA_API_KEY")
    nova_poshta_api_url: str = Field(
        "https://api.novaposhta.ua/v2.0/json/", alias="NOVA_POSHTA_API_URL"
    )

    # AI Provider Settings
    ai_provider: Literal["openai_compatible", "openai", "gemini"] = Field(
        "openai_compatible", alias="AI_PROVIDER"
    )
    ai_base_url: str = Field("http://localhost:8081/v1", alias="AI_BASE_URL")
    ai_api_key: str = Field("dummy-key", alias="AI_API_KEY")
    ai_model: str = Field("gemini-3.6-flash", alias="AI_MODEL")

    # Sender Info
    sender_city_ref: str = Field("", alias="SENDER_CITY_REF")
    sender_counterparty_ref: str = Field("", alias="SENDER_COUNTERPARTY_REF")
    sender_contact_ref: str = Field("", alias="SENDER_CONTACT_REF")
    sender_address_ref: str = Field("", alias="SENDER_ADDRESS_REF")
    sender_phone: str = Field("", alias="SENDER_PHONE")
    sender_name: str = Field("", alias="SENDER_NAME")

    # Defaults for Express Waybill
    default_payer_type: Literal["Recipient", "Sender", "ThirdPerson"] = Field(
        "Recipient", alias="DEFAULT_PAYER_TYPE"
    )
    default_payment_method: Literal["Cash", "NonCash"] = Field(
        "Cash", alias="DEFAULT_PAYMENT_METHOD"
    )
    default_service_type: str = Field("WarehouseWarehouse", alias="DEFAULT_SERVICE_TYPE")
    default_cargo_type: str = Field("Parcel", alias="DEFAULT_CARGO_TYPE")
    default_seats_amount: int = Field(1, alias="DEFAULT_SEATS_AMOUNT")
    default_weight: float = Field(1.0, alias="DEFAULT_WEIGHT")
    default_declared_value: float = Field(500.0, alias="DEFAULT_DECLARED_VALUE")


# Lazy global instance initialization
def get_settings() -> Settings:
    """Retrieve application settings."""
    return Settings()
