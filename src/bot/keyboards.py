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
                KeyboardButton(text="💰 Накладений платіж"),
                KeyboardButton(text="⚙️ Налаштування"),
            ],
            [
                KeyboardButton(text="❓ Допомога"),
            ],
        ],
        resize_keyboard=True,
        persistent=True,
    )



class WaybillActionCallback(CallbackData, prefix="wb"):
    """Callback data schema for waybill actions."""

    action: str  # "confirm", "cancel", "toggle_payer", "toggle_cargo", "cycle_value", "cycle_cod", "toggle_cod_type"
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
                    session_id=session_id,
                ).pack(),
            ),
            InlineKeyboardButton(
                text=f"🔄 {cargo_label}",
                callback_data=WaybillActionCallback(
                    action="toggle_cargo",
                    session_id=session_id,
                ).pack(),
            ),
        ],
        [
            InlineKeyboardButton(
                text=f"💰 Оцінка: {int(declared_value)} грн 🔄",
                callback_data=WaybillActionCallback(
                    action="cycle_value",
                    session_id=session_id,
                ).pack(),
            ),
            InlineKeyboardButton(
                text=f"🔄 {cod_label}",
                callback_data=WaybillActionCallback(
                    action="cycle_cod",
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
                    session_id=session_id,
                ).pack(),
            ),
            InlineKeyboardButton(
                text="❌ Скасувати",
                callback_data=WaybillActionCallback(
                    action="cancel",
                    session_id=session_id,
                ).pack(),
            ),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=keyboard_rows)


class DraftActionCallback(CallbackData, prefix="draft"):
    """Callback data schema for draft management."""

    action: str  # "delete", "edit", "barcode"
    ref: str  # Nova Poshta Ref GUID or TTN Number


def get_draft_keyboard(ref: str) -> InlineKeyboardMarkup:
    """Build inline keyboard for a specific draft item."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📱 Показати штрихкод",
                    callback_data=DraftActionCallback(action="barcode", ref=ref).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Редагувати ТТН",
                    callback_data=DraftActionCallback(action="edit", ref=ref).pack(),
                ),
                InlineKeyboardButton(
                    text="🗑 Видалити ТТН",
                    callback_data=DraftActionCallback(action="delete", ref=ref).pack(),
                ),
            ],
        ]
    )


def get_waybill_keyboard(doc_number: str) -> InlineKeyboardMarkup:
    """Build inline keyboard with barcode button for outgoing/incoming waybills."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📱 Показати штрихкод",
                    callback_data=DraftActionCallback(action="barcode", ref=doc_number).pack(),
                ),
            ],
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


class StreetSelectCallback(CallbackData, prefix="strtsel"):
    """Callback data schema for selecting street when multiple candidates exist."""

    street_ref: str
    session_id: str


def get_street_selection_keyboard(
    streets: list, session_id: str
) -> InlineKeyboardMarkup:
    """Build inline keyboard to let user pick between matching streets/lanes."""
    buttons = []
    for s in streets[:8]:
        label = f"🏡 {s.streets_type} {s.description}"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=StreetSelectCallback(
                        street_ref=s.ref, session_id=session_id
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


class CODActionCallback(CallbackData, prefix="codact"):
    """Callback data schema for COD dashboard actions."""

    action: str  # "refresh", "settings", "list", "back"
    page: int = 0


class CODSettingsCallback(CallbackData, prefix="codset"):
    """Callback data schema for updating COD limits."""

    setting_type: str  # "sum", "count", "toggle_warn"
    value: str  # e.g., "30000", "50000", "150000", "0" (none), "5", "10", "20"


def get_cod_stats_keyboard() -> InlineKeyboardMarkup:
    """Build inline keyboard for monthly COD statistics dashboard."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📜 Накладні та суми наложки (ТТН)",
                    callback_data=CODActionCallback(action="list", page=0).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Оновити з API",
                    callback_data=CODActionCallback(action="refresh", page=0).pack(),
                ),
                InlineKeyboardButton(
                    text="⚙️ Налаштувати ліміти",
                    callback_data=CODActionCallback(action="settings", page=0).pack(),
                ),
            ],
        ]
    )



def get_cod_settings_keyboard(
    current_sum_limit: Optional[float] = 30000.0,
    current_count_limit: Optional[int] = 10,
    warning_enabled: bool = True,
) -> InlineKeyboardMarkup:
    """Build inline keyboard for configuring COD monthly limits."""
    # Sum options
    s_30k = "✅ 30 тис" if current_sum_limit == 30000.0 else "30 тис"
    s_50k = "✅ 50 тис" if current_sum_limit == 50000.0 else "50 тис"
    s_150k = "✅ 150 тис" if current_sum_limit == 150000.0 else "150 тис"
    s_off = "✅ Без ліміту" if not current_sum_limit or current_sum_limit <= 0 else "Без ліміту"

    # Count options
    c_5 = "✅ 5 шт" if current_count_limit == 5 else "5 шт"
    c_10 = "✅ 10 шт" if current_count_limit == 10 else "10 шт"
    c_20 = "✅ 20 шт" if current_count_limit == 20 else "20 шт"
    c_off = "✅ Без ліміту" if not current_count_limit or current_count_limit <= 0 else "Без ліміту"

    warn_label = "🔔 Попередження: УВІМКНЕНО" if warning_enabled else "🔕 Попередження: ВИМКНЕНО"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💰 Ліміт суми на місяць:", callback_data=CODActionCallback(action="noop", page=0).pack()),
            ],
            [
                InlineKeyboardButton(
                    text=s_30k,
                    callback_data=CODSettingsCallback(setting_type="sum", value="30000").pack(),
                ),
                InlineKeyboardButton(
                    text=s_50k,
                    callback_data=CODSettingsCallback(setting_type="sum", value="50000").pack(),
                ),
                InlineKeyboardButton(
                    text=s_150k,
                    callback_data=CODSettingsCallback(setting_type="sum", value="150000").pack(),
                ),
                InlineKeyboardButton(
                    text=s_off,
                    callback_data=CODSettingsCallback(setting_type="sum", value="0").pack(),
                ),
            ],
            [
                InlineKeyboardButton(text="📦 Ліміт кількості посилок:", callback_data=CODActionCallback(action="noop", page=0).pack()),
            ],
            [
                InlineKeyboardButton(
                    text=c_5,
                    callback_data=CODSettingsCallback(setting_type="count", value="5").pack(),
                ),
                InlineKeyboardButton(
                    text=c_10,
                    callback_data=CODSettingsCallback(setting_type="count", value="10").pack(),
                ),
                InlineKeyboardButton(
                    text=c_20,
                    callback_data=CODSettingsCallback(setting_type="count", value="20").pack(),
                ),
                InlineKeyboardButton(
                    text=c_off,
                    callback_data=CODSettingsCallback(setting_type="count", value="0").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=warn_label,
                    callback_data=CODSettingsCallback(
                        setting_type="toggle_warn",
                        value="0" if warning_enabled else "1",
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔙 Назад до статистики",
                    callback_data=CODActionCallback(action="back", page=0).pack(),
                ),
            ],
        ]
    )


def get_cod_shipments_keyboard(
    current_page: int,
    total_pages: int,
) -> InlineKeyboardMarkup:
    """Build inline keyboard for paginating COD shipments list."""
    nav_row = []
    if current_page > 0:
        nav_row.append(
            InlineKeyboardButton(
                text="⬅️ Попередня",
                callback_data=CODActionCallback(action="list", page=current_page - 1).pack(),
            )
        )
    nav_row.append(
        InlineKeyboardButton(
            text=f"📄 {current_page + 1}/{max(total_pages, 1)}",
            callback_data=CODActionCallback(action="noop", page=0).pack(),
        )
    )
    if current_page < total_pages - 1:
        nav_row.append(
            InlineKeyboardButton(
                text="➡️ Наступна",
                callback_data=CODActionCallback(action="list", page=current_page + 1).pack(),
            )
        )

    return InlineKeyboardMarkup(
        inline_keyboard=[
            nav_row,
            [
                InlineKeyboardButton(
                    text="🔙 Назад до статистики",
                    callback_data=CODActionCallback(action="back", page=0).pack(),
                ),
            ],
        ]
    )

