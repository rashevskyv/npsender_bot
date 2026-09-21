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
                KeyboardButton(text="🔍 Відстежити ТТН"),
            ],
            [
                KeyboardButton(text="👥 Користувачі"),
                KeyboardButton(text="⚙️ Налаштування"),
            ],
            [
                KeyboardButton(text="💳 Картка клієнта"),
                KeyboardButton(text="❓ Допомога"),
            ],
        ],
        resize_keyboard=True,
        persistent=True,
    )


class UserProfileCallback(CallbackData, prefix="uprof"):
    """Callback data schema for sender user profiles management."""

    action: str  # "select", "add", "delete_prompt", "delete", "refresh"
    profile_id: str  # profile ID or "none"


class ClientCardCallback(CallbackData, prefix="npcard"):
    """Callback data schema for Nova Poshta client loyalty card actions."""

    action: str  # "show", "switch_phone", "switch_card", "refresh"
    profile_id: str  # profile ID or "active"
    mode: str  # "card" or "phone"


def get_users_management_keyboard(
    profiles: List[Any],
    active_profile_id: Optional[str] = None,
    balances_map: Optional[Dict[str, Dict[str, Any]]] = None,
) -> InlineKeyboardMarkup:
    """Build interactive inline keyboard for multi-user profile management."""
    rows = []
    balances_map = balances_map or {}

    for p in profiles:
        is_active = (p.id == active_profile_id)
        bal = balances_map.get(p.id, {})
        rem_sum = bal.get("rem_sum")

        if is_active:
            label = f"✅ {p.name}"
            if rem_sum is not None:
                label += f"\n(залишок: {int(rem_sum)} грн)"
            rows.append([
                InlineKeyboardButton(
                    text=label,
                    callback_data=UserProfileCallback(action="select", profile_id=p.id).pack(),
                )
            ])
        else:
            label = f"🔄 {p.name}"
            if rem_sum is not None:
                label += f"\n(залишок: {int(rem_sum)} грн)"
            rows.append([
                InlineKeyboardButton(
                    text=label,
                    callback_data=UserProfileCallback(action="select", profile_id=p.id).pack(),
                )
            ])

    mgmt_row = [
        InlineKeyboardButton(
            text="➕ Додати користувача",
            callback_data=UserProfileCallback(action="add", profile_id="none").pack(),
        ),
        InlineKeyboardButton(
            text="🔄 Оновити",
            callback_data=UserProfileCallback(action="refresh", profile_id="none").pack(),
        ),
    ]
    rows.append(mgmt_row)

    rows.append([
        InlineKeyboardButton(
            text="🔄 Підтягнути адресу з сайту (API)",
            callback_data=UserProfileCallback(action="sync_address", profile_id="none").pack(),
        )
    ])

    if len(profiles) > 1:
        rows.append([
            InlineKeyboardButton(
                text="🗑 Видалити користувача",
                callback_data=UserProfileCallback(action="delete_prompt", profile_id="none").pack(),
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_settings_keyboard() -> InlineKeyboardMarkup:
    """Build inline action buttons for settings/profile screen."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💳 Картка клієнта (штрихкод)",
                    callback_data=ClientCardCallback(action="show", profile_id="active", mode="card").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👥 Керування користувачами",
                    callback_data=UserProfileCallback(action="refresh", profile_id="none").pack(),
                ),
            ],
        ]
    )


def get_client_card_keyboard(
    profile_id: str = "active", current_mode: str = "card"
) -> InlineKeyboardMarkup:
    """Build action buttons below Nova Poshta client card image."""
    rows = []
    if current_mode == "card":
        mode_btn = InlineKeyboardButton(
            text="📱 Штрихкод телефону",
            callback_data=ClientCardCallback(action="switch_phone", profile_id=profile_id, mode="phone").pack(),
        )
    else:
        mode_btn = InlineKeyboardButton(
            text="💳 Штрихкод картки (CID)",
            callback_data=ClientCardCallback(action="switch_card", profile_id=profile_id, mode="card").pack(),
        )

    rows.append([
        mode_btn,
        InlineKeyboardButton(
            text="🔄 Оновити",
            callback_data=ClientCardCallback(action="refresh", profile_id=profile_id, mode=current_mode).pack(),
        ),
    ])
    rows.append([
        InlineKeyboardButton(
            text="⚙️ Налаштування",
            callback_data=ClientCardCallback(action="settings", profile_id=profile_id, mode=current_mode).pack(),
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_profile_delete_keyboard(
    profiles: List[Any], active_profile_id: Optional[str] = None
) -> InlineKeyboardMarkup:
    """Keyboard to select which non-active profile to delete."""
    rows = []
    for p in profiles:
        if p.id != active_profile_id:
            rows.append([
                InlineKeyboardButton(
                    text=f"🗑 Видалити «{p.name}»",
                    callback_data=UserProfileCallback(action="delete", profile_id=p.id).pack(),
                )
            ])
    rows.append([
        InlineKeyboardButton(
            text="🔙 Назад до списку користувачів",
            callback_data=UserProfileCallback(action="refresh", profile_id="none").pack(),
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


class WaybillActionCallback(CallbackData, prefix="wb"):
    """Callback data schema for waybill actions."""

    action: str  # "confirm", "force_confirm", "cancel", "toggle_payer", "toggle_cargo", "cycle_value", "cycle_cod", "toggle_cod_type", "back_to_card", "switch_profile", "cycle_sender"
    session_id: str  # unique ID or state reference


def get_confirmation_keyboard(
    payer_type: str = "Recipient",
    cargo_type: str = "Parcel",
    declared_value: float = 500.0,
    cod_amount: float = 0.0,
    cod_payment_type: str = "cash",
    sender_card_mask: Optional[str] = None,
    session_id: str = "default",
    suggested_profile: Optional[Dict[str, Any]] = None,
    active_profile_name: Optional[str] = None,
    has_multiple_profiles: bool = False,
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

    # If recommendation exists to switch user with more limit
    if suggested_profile:
        s_name = suggested_profile.get("name", "іншого")
        s_rem = int(suggested_profile.get("rem_sum", 0))
        keyboard_rows.append([
            InlineKeyboardButton(
                text=f"👥 Переключити на: {s_name} (+{s_rem} грн ліміту)",
                callback_data=WaybillActionCallback(
                    action="switch_profile",
                    session_id=session_id,
                ).pack(),
            )
        ])
    elif has_multiple_profiles and active_profile_name:
        keyboard_rows.append([
            InlineKeyboardButton(
                text=f"👤 Відправник: {active_profile_name} 🔄",
                callback_data=WaybillActionCallback(
                    action="cycle_sender",
                    session_id=session_id,
                ).pack(),
            )
        ])

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


def get_limit_exceeded_confirmation_keyboard(
    session_id: str, suggested_profile: Optional[Dict[str, Any]] = None
) -> InlineKeyboardMarkup:
    """Build confirmation keyboard when COD limits are exceeded."""
    keyboard = []
    if suggested_profile:
        s_name = suggested_profile.get("name", "іншого")
        s_rem = int(suggested_profile.get("rem_sum", 0))
        keyboard.append([
            InlineKeyboardButton(
                text=f"👥 Переключити на: {s_name} (залишок {s_rem} грн)",
                callback_data=WaybillActionCallback(
                    action="switch_profile",
                    session_id=session_id,
                ).pack(),
            )
        ])

    keyboard.extend([
        [
            InlineKeyboardButton(
                text="⚠️ Все одно створити ТТН",
                callback_data=WaybillActionCallback(
                    action="force_confirm",
                    session_id=session_id,
                ).pack(),
            ),
        ],
        [
            InlineKeyboardButton(
                text="💰 Змінити наложку",
                callback_data=WaybillActionCallback(
                    action="cycle_cod",
                    session_id=session_id,
                ).pack(),
            ),
            InlineKeyboardButton(
                text="🔙 До картки ТТН",
                callback_data=WaybillActionCallback(
                    action="back_to_card",
                    session_id=session_id,
                ).pack(),
            ),
        ],
        [
            InlineKeyboardButton(
                text="❌ Скасувати створення",
                callback_data=WaybillActionCallback(
                    action="cancel",
                    session_id=session_id,
                ).pack(),
            ),
        ],
    ])
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


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


class TrackActionCallback(CallbackData, prefix="trk"):
    """Callback data schema for waybill tracking actions."""

    action: str  # "refresh", "barcode", "track"
    doc_number: str


def get_tracking_keyboard(doc_number: str) -> InlineKeyboardMarkup:
    """Build interactive action buttons for tracking results."""
    clean_num = "".join(filter(str.isdigit, str(doc_number)))
    buttons = [
        [
            InlineKeyboardButton(
                text="📱 Згенерувати штрих-код",
                callback_data=TrackActionCallback(action="barcode", doc_number=clean_num).pack(),
            ),
            InlineKeyboardButton(
                text="🔄 Оновити",
                callback_data=TrackActionCallback(action="refresh", doc_number=clean_num).pack(),
            ),
        ],
        [
            InlineKeyboardButton(
                text="🌐 Відкрити на сайті Нової Пошти",
                url=f"https://novaposhta.ua/tracking/?cargo_number={clean_num}",
            ),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_waybill_action_keyboard(doc_number: str) -> InlineKeyboardMarkup:
    """Build interactive action choice keyboard when a waybill number is sent: Track or Barcode."""
    clean_num = "".join(filter(str.isdigit, str(doc_number)))
    buttons = [
        [
            InlineKeyboardButton(
                text="🔍 Відстежити",
                callback_data=TrackActionCallback(action="track", doc_number=clean_num).pack(),
            ),
            InlineKeyboardButton(
                text="📱 Згенерувати штрих-код",
                callback_data=TrackActionCallback(action="barcode", doc_number=clean_num).pack(),
            ),
        ],
        [
            InlineKeyboardButton(
                text="🌐 Відкрити на сайті Нової Пошти",
                url=f"https://novaposhta.ua/tracking/?cargo_number={clean_num}",
            ),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_barcode_keyboard(doc_number: str) -> InlineKeyboardMarkup:
    """Build interactive action buttons under generated waybill barcode photo."""
    clean_num = "".join(filter(str.isdigit, str(doc_number)))
    buttons = [
        [
            InlineKeyboardButton(
                text="🔍 Відстежити ТТН",
                callback_data=TrackActionCallback(action="track", doc_number=clean_num).pack(),
            ),
            InlineKeyboardButton(
                text="🌐 Відкрити на сайті НП",
                url=f"https://novaposhta.ua/tracking/?cargo_number={clean_num}",
            ),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_missing_sender_address_keyboard(session_id: str) -> InlineKeyboardMarkup:
    """Build inline keyboard when sender departure city or warehouse is not configured."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Спробувати знову",
                    callback_data=WaybillActionCallback(action="confirm", session_id=session_id).pack(),
                ),
                InlineKeyboardButton(
                    text="❌ Скасувати",
                    callback_data=WaybillActionCallback(action="cancel", session_id=session_id).pack(),
                ),
            ]
        ]
    )



