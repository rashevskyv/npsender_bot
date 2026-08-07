"""Inline keyboards for Telegram bot."""

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters.callback_data import CallbackData


class WaybillActionCallback(CallbackData, prefix="wb"):
    """Callback data schema for waybill actions."""

    action: str  # "confirm", "cancel", "toggle_payer", "toggle_cargo"
    payer_type: str  # "Recipient", "Sender"
    cargo_type: str  # "Parcel", "Documents"
    session_id: str  # unique ID or state reference


def get_confirmation_keyboard(
    payer_type: str = "Recipient",
    cargo_type: str = "Parcel",
    session_id: str = "default",
) -> InlineKeyboardMarkup:
    """Build interactive confirmation keyboard with toggle buttons."""
    payer_label = "👤 Payer: Recipient" if payer_type == "Recipient" else "📦 Payer: Sender"
    cargo_label = "📦 Cargo: Parcel" if cargo_type == "Parcel" else "📄 Cargo: Documents"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🔄 {payer_label}",
                    callback_data=WaybillActionCallback(
                        action="toggle_payer",
                        payer_type=payer_type,
                        cargo_type=cargo_type,
                        session_id=session_id,
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"🔄 {cargo_label}",
                    callback_data=WaybillActionCallback(
                        action="toggle_cargo",
                        payer_type=payer_type,
                        cargo_type=cargo_type,
                        session_id=session_id,
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✅ Create Waybill (ТТН)",
                    callback_data=WaybillActionCallback(
                        action="confirm",
                        payer_type=payer_type,
                        cargo_type=cargo_type,
                        session_id=session_id,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data=WaybillActionCallback(
                        action="cancel",
                        payer_type=payer_type,
                        cargo_type=cargo_type,
                        session_id=session_id,
                    ).pack(),
                ),
            ],
        ]
    )
    return kb
