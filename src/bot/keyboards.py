"""Inline and Reply keyboards for Telegram bot (Ukrainian UI)."""

from aiogram.types import (
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    KeyboardButton,
)
from aiogram.filters.callback_data import CallbackData


def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    """Build persistent main reply keyboard with buttons."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="📦 Активні посилки"),
                KeyboardButton(text="📝 Мої чернетки (ТТН)"),
            ],
            [
                KeyboardButton(text="⚙️ Налаштування"),
                KeyboardButton(text="❓ Допомога"),
            ],
        ],
        resize_keyboard=True,
        persistent=True,
    )


class WaybillActionCallback(CallbackData, prefix="wb"):
    """Callback data schema for waybill actions."""

    action: str  # "confirm", "cancel", "toggle_payer", "toggle_cargo", "cycle_value"
    payer_type: str  # "Recipient", "Sender"
    cargo_type: str  # "Parcel", "Documents"
    declared_value: float  # e.g., 500.0, 1000.0, 2000.0
    session_id: str  # unique ID or state reference


def get_confirmation_keyboard(
    payer_type: str = "Recipient",
    cargo_type: str = "Parcel",
    declared_value: float = 500.0,
    session_id: str = "default",
) -> InlineKeyboardMarkup:
    """Build interactive confirmation keyboard with toggle buttons in Ukrainian."""
    payer_label = "👤 Платник: Отримувач" if payer_type == "Recipient" else "📦 Платник: Відправник"
    cargo_label = "📦 Вантаж: Посилка" if cargo_type == "Parcel" else "📄 Вантаж: Документи"

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🔄 {payer_label}",
                    callback_data=WaybillActionCallback(
                        action="toggle_payer",
                        payer_type=payer_type,
                        cargo_type=cargo_type,
                        declared_value=declared_value,
                        session_id=session_id,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text=f"🔄 {cargo_label}",
                    callback_data=WaybillActionCallback(
                        action="toggle_cargo",
                        payer_type=payer_type,
                        cargo_type=cargo_type,
                        declared_value=declared_value,
                        session_id=session_id,
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=f"💰 Оцінка: {int(declared_value)} грн 🔄",
                    callback_data=WaybillActionCallback(
                        action="cycle_value",
                        payer_type=payer_type,
                        cargo_type=cargo_type,
                        declared_value=declared_value,
                        session_id=session_id,
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✅ Створити ТТН",
                    callback_data=WaybillActionCallback(
                        action="confirm",
                        payer_type=payer_type,
                        cargo_type=cargo_type,
                        declared_value=declared_value,
                        session_id=session_id,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="❌ Скасувати",
                    callback_data=WaybillActionCallback(
                        action="cancel",
                        payer_type=payer_type,
                        cargo_type=cargo_type,
                        declared_value=declared_value,
                        session_id=session_id,
                    ).pack(),
                ),
            ],
        ]
    )
    return kb


class DraftActionCallback(CallbackData, prefix="draft"):
    """Callback data schema for draft management."""

    action: str  # "delete"
    ref: str  # Nova Poshta Ref GUID


def get_draft_keyboard(ref: str) -> InlineKeyboardMarkup:
    """Build inline keyboard for a specific draft item."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🗑 Видалити ТТН",
                    callback_data=DraftActionCallback(action="delete", ref=ref).pack(),
                )
            ]
        ]
    )
