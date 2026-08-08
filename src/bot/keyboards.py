from typing import List, Dict, Any, Optional
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
                KeyboardButton(text="📤 Вихідні (що їдуть)"),
                KeyboardButton(text="📥 Вхідні (що їдуть)"),
            ],
            [
                KeyboardButton(text="📝 Мої чернетки (ТТН)"),
                KeyboardButton(text="📋 Реєстри (ScanSheet)"),
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

    action: str  # "confirm", "cancel", "toggle_payer", "toggle_cargo", "cycle_value", "cycle_cod", "toggle_cod_type"
    payer_type: str  # "Recipient", "Sender"
    cargo_type: str  # "Parcel", "Documents"
    declared_value: float  # e.g., 500.0, 1000.0, 2000.0
    cod_amount: float = 0.0  # Cash on delivery amount in UAH
    cod_payment_type: str = "cash"  # "cash" or "card"
    session_id: str  # unique ID or state reference


def get_confirmation_keyboard(
    payer_type: str = "Recipient",
    cargo_type: str = "Parcel",
    declared_value: float = 500.0,
    cod_amount: float = 0.0,
    cod_payment_type: str = "cash",
    sender_card_mask: Optional[str] = None,
    session_id: str = "default",
) -> InlineKeyboardMarkup:
    """Build interactive confirmation keyboard with toggle buttons in Ukrainian."""
    payer_label = "👤 Платник: Отримувач" if payer_type == "Recipient" else "📦 Платник: Відправник"
    cargo_label = "📦 Вантаж: Посилка" if cargo_type == "Parcel" else "📄 Вантаж: Документи"

    if not cod_amount or cod_amount <= 0:
        cod_label = "💸 Наложка: ❌ Немає"
    else:
        cod_label = f"💰 Наложка: {int(cod_amount)} грн 🔄"

    keyboard_rows = [
        [
            InlineKeyboardButton(
                text=f"🔄 {payer_label}",
                callback_data=WaybillActionCallback(
                    action="toggle_payer",
                    payer_type=payer_type,
                    cargo_type=cargo_type,
                    declared_value=declared_value,
                    cod_amount=cod_amount,
                    cod_payment_type=cod_payment_type,
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
                    cod_amount=cod_amount,
                    cod_payment_type=cod_payment_type,
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
                    cod_amount=cod_amount,
                    cod_payment_type=cod_payment_type,
                    session_id=session_id,
                ).pack(),
            ),
            InlineKeyboardButton(
                text=f"🔄 {cod_label}",
                callback_data=WaybillActionCallback(
                    action="cycle_cod",
                    payer_type=payer_type,
                    cargo_type=cargo_type,
                    declared_value=declared_value,
                    cod_amount=cod_amount,
                    cod_payment_type=cod_payment_type,
                    session_id=session_id,
                ).pack(),
            ),
        ],
    ]

    # Add COD payout type toggle row when COD amount > 0
    if cod_amount and cod_amount > 0:
        if cod_payment_type == "card":
            card_info = f" ({sender_card_mask})" if sender_card_mask else " (⚠️ Вказати картку)"
            payout_label = f"🔄 💳 Виплата: На картку{card_info}"
        else:
            payout_label = "🔄 💵 Виплата: Готівкою у відділенні"

        keyboard_rows.append(
            [
                InlineKeyboardButton(
                    text=payout_label,
                    callback_data=WaybillActionCallback(
                        action="toggle_cod_type",
                        payer_type=payer_type,
                        cargo_type=cargo_type,
                        declared_value=declared_value,
                        cod_amount=cod_amount,
                        cod_payment_type=cod_payment_type,
                        session_id=session_id,
                    ).pack(),
                )
            ]
        )

    keyboard_rows.append(
        [
            InlineKeyboardButton(
                text="✅ Створити ТТН",
                callback_data=WaybillActionCallback(
                    action="confirm",
                    payer_type=payer_type,
                    cargo_type=cargo_type,
                    declared_value=declared_value,
                    cod_amount=cod_amount,
                    cod_payment_type=cod_payment_type,
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
                    cod_amount=cod_amount,
                    cod_payment_type=cod_payment_type,
                    session_id=session_id,
                ).pack(),
            ),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


class DraftActionCallback(CallbackData, prefix="draft"):
    """Callback data schema for draft management."""

    action: str  # "delete", "edit"
    ref: str  # Nova Poshta Ref GUID


def get_draft_keyboard(ref: str) -> InlineKeyboardMarkup:
    """Build inline keyboard for a specific draft item."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Редагувати ТТН",
                    callback_data=DraftActionCallback(action="edit", ref=ref).pack(),
                ),
                InlineKeyboardButton(
                    text="🗑 Видалити ТТН",
                    callback_data=DraftActionCallback(action="delete", ref=ref).pack(),
                ),
            ]
        ]
    )


class CitySelectCallback(CallbackData, prefix="citysel"):
    """Callback data schema for selecting city when duplicates exist."""

    city_ref: str
    session_id: str


def get_city_selection_keyboard(
    candidates: list, session_id: str
) -> InlineKeyboardMarkup:
    """Build inline keyboard to let user pick between duplicate cities."""
    buttons = []
    for city, warehouse in candidates:
        area_str = f" ({city.area})" if city.area else ""
        label = f"🏙 {city.description}{area_str}"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=CitySelectCallback(
                        city_ref=city.ref, session_id=session_id
                    ).pack(),
                )
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


class RegisterActionCallback(CallbackData, prefix="reg"):
    """Callback data schema for register management."""

    action: str  # "delete", "barcode"
    ref: str  # Nova Poshta ScanSheet Ref GUID


def get_register_keyboard(ref: str) -> InlineKeyboardMarkup:
    """Build inline keyboard for a specific register (ScanSheet) item."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📱 Показати штрихкод",
                    callback_data=RegisterActionCallback(action="barcode", ref=ref).pack(),
                ),
                InlineKeyboardButton(
                    text="🗑 Видалити реєстр",
                    callback_data=RegisterActionCallback(action="delete", ref=ref).pack(),
                ),
            ]
        ]
    )


class AddressConfirmCallback(CallbackData, prefix="addr"):
    """Callback data schema for confirming courier address delivery vs warehouse."""

    choice: str  # "courier", "warehouse"
    session_id: str


def get_address_confirmation_keyboard(session_id: str) -> InlineKeyboardMarkup:
    """Build inline keyboard to ask user if they want courier home delivery or warehouse."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚚 Доставка кур'єром додому",
                    callback_data=AddressConfirmCallback(choice="courier", session_id=session_id).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📦 Доставка у відділення / поштомат",
                    callback_data=AddressConfirmCallback(choice="warehouse", session_id=session_id).pack(),
                ),
            ],
        ]
    )
