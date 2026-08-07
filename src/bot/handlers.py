"""Telegram bot message handlers and callback handlers in Ukrainian."""

import asyncio
import datetime
import logging
import uuid
from typing import Dict, Any, Optional, List

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from src.config import Settings
from src.storage import UserSettingsManager, UserCustomSettings, SavedDraft
from src.ai.schemas import ParsedRecipientInfo
from src.ai.extractor import AIExtractor
from src.nova_poshta.client import NovaPoshtaClient
from src.bot.keyboards import (
    get_main_reply_keyboard,
    get_confirmation_keyboard,
    WaybillActionCallback,
    DraftActionCallback,
    get_draft_keyboard,
)

logger = logging.getLogger(__name__)
router = Router()

# In-memory storage for active pending waybill verification sessions
PENDING_SESSIONS: Dict[str, Dict[str, Any]] = {}
USER_ACTIVE_SESSIONS: Dict[int, str] = {}  # user_id -> active session_id
VALUE_OPTIONS = [500.0, 1000.0, 2000.0, 5000.0, 10000.0]

# Debouncer buffers for multi-part forwarded messages
USER_MESSAGE_BUFFERS: Dict[int, List[str]] = {}
USER_DEBOUNCE_TASKS: Dict[int, asyncio.Task] = {}
USER_LAST_MESSAGES: Dict[int, Message] = {}


def clear_user_active_session(user_id: int):
    """Clear active waybill session for a given user."""
    session_id = USER_ACTIVE_SESSIONS.pop(user_id, None)
    if session_id:
        PENDING_SESSIONS.pop(session_id, None)


def register_handlers(
    settings: Settings,
    ai_extractor: AIExtractor,
    np_client: NovaPoshtaClient,
    storage_manager: UserSettingsManager,
):
    """Factory to inject dependencies into router handlers."""

    @router.message(Command("start"))
    async def cmd_start(message: Message):
        """Welcome message and basic instructions."""
        clear_user_active_session(message.from_user.id)
        welcome_text = (
            "👋 **Вітаємо у боті автоматичної генерації ТТН Нової Пошти!**\n\n"
            "Надішліть мені реквізити отримувача у довільному форматі (ПІБ, телефон, місто, номер відділення або поштомату), "
            "і я за допомогою штучного інтелекту розпаршу дані та сформую express-накладну (ТТН).\n\n"
            "**Приклад повідомлення:**\n"
            "`Юрченко Роман Сергійович 0995360818 Київ поштомат 26584`\n\n"
            "**Користуйтеся кнопками меню нижче для швидкого доступу!**"
        )
        await message.answer(
            welcome_text, parse_mode="Markdown", reply_markup=get_main_reply_keyboard()
        )

    @router.message(Command("help"))
    @router.message(F.text == "❓ Допомога")
    async def cmd_help(message: Message):
        """Help instructions."""
        clear_user_active_session(message.from_user.id)
        help_text = (
            "📖 **Як користуватися ботом:**\n\n"
            "1. **Налаштування ключів (за бажанням):** Натисніть кнопку `⚙️ Налаштування` або скористайтеся командою `/set_np_key ВАШ_КЛЮЧ`, щоб підключити власний акаунт Нової Пошти.\n"
            "2. **Надішліть реквізити:** Надішліть дані отримувача одним повідомленням у довільному порядку.\n"
            "3. **Автоматична валідація та контекст:** Бот перевірить місто та відділення у базі НП. Якщо ви захочете уточнити опис (наприклад, написати слово *'сувенір'*) або змінити оцінку, просто надішліть доповнення наступним повідомленням!\n"
            "4. **Інтерактивні кнопки:** Використовуйте кнопки під карткою для зміни платника (Отримувач/Відправник), типу вантажу чи оціночної вартості (мін. 500 грн).\n"
            "5. **Чернетки та посилки:** Кнопки `📝 Мої чернетки (ТТН)` та `📦 Активні посилки` дозволяють переглядати та видаляти створені ТТН."
        )
        await message.answer(
            help_text, parse_mode="Markdown", reply_markup=get_main_reply_keyboard()
        )

    @router.message(Command("set_np_key"))
    async def cmd_set_np_key(message: Message):
        """Set user's personal Nova Poshta API key."""
        clear_user_active_session(message.from_user.id)
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                "⚠️ *Використання:* `/set_np_key ВАШ_API_КЛЮЧ_НОВОЇ_ПОШТИ`", parse_mode="Markdown"
            )
            return

        api_key = parts[1].strip()
        status_msg = await message.answer(
            "⏳ *Перевірка API-ключа Нової Пошти та підтягування профілю відправника...*", parse_mode="Markdown"
        )

        try:
            profile = await np_client.fetch_sender_profile(api_key)
            u_settings = storage_manager.get_user_settings(message.from_user.id)

            u_settings.nova_poshta_api_key = api_key
            u_settings.sender_counterparty_ref = profile["sender_counterparty_ref"]
            u_settings.sender_contact_ref = profile["sender_contact_ref"]
            u_settings.sender_city_ref = profile["sender_city_ref"]
            u_settings.sender_address_ref = profile["sender_address_ref"]
            u_settings.sender_phone = profile["sender_phone"]
            u_settings.sender_name = profile["sender_name"]

            storage_manager.update_user_settings(message.from_user.id, u_settings)

            await status_msg.edit_text(
                "✅ *API-ключ Нової Пошти успішно збережено!*\n\n"
                f"👤 *Відправник:* `{profile['sender_name']}`\n"
                f"📞 *Телефон:* `{profile['sender_phone'] or 'Не вказано'}`\n"
                f"🔑 *Ключ:* `{api_key[:6]}...{api_key[-4:]}`\n\n"
                "Усі наступні накладні будуть створюватися від імені вашого особистого акаунта!",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.error(f"Failed to set Nova Poshta API key: {e}")
            await status_msg.edit_text(
                f"❌ *Помилка перевірки ключа Нової Пошти:* {str(e)}", parse_mode="Markdown"
            )

    @router.message(Command("set_ai_key"))
    async def cmd_set_ai_key(message: Message):
        """Set user's personal AI API key."""
        clear_user_active_session(message.from_user.id)
        parts = message.text.split(maxsplit=1)
        if len(parts) < 2:
            await message.answer(
                "⚠️ *Використання:* `/set_ai_key ВАШ_AI_API_KEY`", parse_mode="Markdown"
            )
            return

        api_key = parts[1].strip()
        u_settings = storage_manager.get_user_settings(message.from_user.id)
        u_settings.ai_api_key = api_key
        storage_manager.update_user_settings(message.from_user.id, u_settings)

        await message.answer(
            f"✅ *Персональний AI API ключ успішно збережено!* (`{api_key[:6]}...{api_key[-4:]}`)",
            parse_mode="Markdown",
        )

    @router.message(Command("reset_settings"))
    async def cmd_reset_settings(message: Message):
        """Reset custom settings to system defaults."""
        clear_user_active_session(message.from_user.id)
        storage_manager.reset_user_settings(message.from_user.id)
        await message.answer(
            "🔄 *Ваші персональні ключі та налаштування скинуто до системних за замовчуванням.*",
            parse_mode="Markdown",
            reply_markup=get_main_reply_keyboard(),
        )

    @router.message(Command("settings"))
    @router.message(F.text == "⚙️ Налаштування")
    async def cmd_settings(message: Message):
        """Show current effective settings for the user."""
        clear_user_active_session(message.from_user.id)
        eff = storage_manager.get_effective_settings(message.from_user.id, settings)
        u_custom = storage_manager.get_user_settings(message.from_user.id)

        has_custom_np = bool(u_custom.nova_poshta_api_key)
        has_custom_ai = bool(u_custom.ai_api_key)

        status_text = (
            "⚙️ **Ваші активні налаштування:**\n\n"
            f"• **Ключ Нової Пошти:** `{'Особистий (' + eff.nova_poshta_api_key[:6] + '...)' if has_custom_np else 'Системний'}`\n"
            f"• **Профіль відправника:** `{u_custom.sender_name or 'Основний акаунт'}`\n"
            f"• **Телефон відправника:** `{eff.sender_phone or 'Не вказано'}`\n"
            f"• **AI Провайдер:** `{eff.ai_provider}`\n"
            f"• **AI Ключ:** `{'Особистий' if has_custom_ai else 'Системний'}`\n"
            f"• **AI Модель:** `{eff.ai_model}`\n"
            f"• **Мін. оціночна вартість:** `{eff.default_declared_value} грн`\n\n"
            "Щоб змінити ключі, використайте команди:\n"
            "`/set_np_key ВАШ_КЛЮЧ` або `/set_ai_key ВАШ_КЛЮЧ`\n"
            "Або `/reset_settings` для скидання."
        )
        await message.answer(
            status_text, parse_mode="Markdown", reply_markup=get_main_reply_keyboard()
        )

    @router.message(Command("drafts"))
    @router.message(F.text == "📝 Мої чернетки (ТТН)")
    async def cmd_drafts(message: Message):
        """View and manage created waybill drafts."""
        clear_user_active_session(message.from_user.id)
        user_id = message.from_user.id
        drafts = storage_manager.get_user_drafts(user_id)

        if not drafts:
            await message.answer(
                "📝 *Збережених чернеток ТТН не знайдено.*\n"
                "Створіть нову накладну, надіславши реквізити отримувача боту!",
                parse_mode="Markdown",
                reply_markup=get_main_reply_keyboard(),
            )
            return

        await message.answer(
            f"📄 *Ваші збережені чернетки ТТН ({len(drafts)}):*", parse_mode="Markdown"
        )

        for draft in drafts[:10]:
            tracking_url = (
                f"https://novaposhta.ua/tracking/?cargo_number={draft.int_doc_number}"
            )
            card = (
                f"🎫 *ТТН:* `{draft.int_doc_number}`\n"
                f"👤 *Отримувач:* {draft.recipient_name}\n"
                f"📞 *Телефон:* `{draft.recipient_phone}`\n"
                f"🏙 *Місто:* {draft.city_description}\n"
                f"📦 *Пункт призначення:* {draft.warehouse_description}\n"
                f"📝 *Опис:* {draft.cargo_description}\n"
                f"💳 *Платник:* {draft.payer_type} | 💰 *Оцінка:* {int(draft.declared_value)} грн\n"
                f"📅 *Створено:* {draft.created_at}\n\n"
                f"🔗 [Відстежити ТТН на сайті Нової Пошти]({tracking_url})"
            )
            await message.answer(
                card,
                parse_mode="Markdown",
                disable_web_page_preview=True,
                reply_markup=get_draft_keyboard(ref=draft.ref),
            )

    @router.message(Command("parcels"))
    @router.message(F.text == "📦 Активні посилки")
    async def cmd_parcels(message: Message):
        """Show active outgoing shipments / waybills."""
        clear_user_active_session(message.from_user.id)
        eff_settings = storage_manager.get_effective_settings(message.from_user.id, settings)
        user_np_client = NovaPoshtaClient(eff_settings)

        status_msg = await message.answer(
            "🔍 *Отримання ваших активних посилок з Нової Пошти...*", parse_mode="Markdown"
        )
        try:
            items = await user_np_client.get_outgoing_waybills(days_back=30, limit=10)
            if not items:
                await status_msg.edit_text(
                    "📦 *Активних вихідних посилок за останні 30 днів не знайдено.*",
                    parse_mode="Markdown",
                )
                return

            response_lines = ["📦 *Ваші активні посилки (останні 30 днів):*\n"]
            for idx, item in enumerate(items, 1):
                tracking_url = (
                    f"https://novaposhta.ua/tracking/?cargo_number={item.int_doc_number}"
                )
                response_lines.append(
                    f"*{idx}. ТТН:* [{item.int_doc_number}]({tracking_url})\n"
                    f"👤 *Отримувач:* {item.recipient_name}\n"
                    f"🏙 *Пункт призначення:* {item.city_recipient}, {item.address_recipient}\n"
                    f"📝 *Опис:* {item.description}\n"
                    f"💰 *Вартість доставки:* ~{item.cost} грн | 📊 *Статус:* {item.state_name}\n"
                    "----------------------------------------"
                )

            await status_msg.edit_text(
                "\n".join(response_lines),
                parse_mode="Markdown",
                disable_web_page_preview=True,
            )
        except Exception as e:
            logger.error(f"Error fetching parcels: {e}", exc_info=True)
            await status_msg.edit_text(
                f"❌ *Не вдалося отримати посилки:* {str(e)}", parse_mode="Markdown"
            )

    async def _process_user_accumulated_messages(user_id: int):
        """Wait for rapid forwarded messages to accumulate before parsing."""
        try:
            await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            return

        buffered_texts = USER_MESSAGE_BUFFERS.pop(user_id, [])
        last_message = USER_LAST_MESSAGES.pop(user_id, None)

        if not buffered_texts or not last_message:
            return

        combined_text = "\n".join(buffered_texts)
        await _handle_combined_text_message(last_message, combined_text)

    @router.message(F.text)
    async def process_text_message(message: Message):
        """Handle raw user text with debouncing for rapid forwarded messages."""
        text = message.text.strip()
        if text.startswith("/"):
            return

        user_id = message.from_user.id
        if user_id not in USER_MESSAGE_BUFFERS:
            USER_MESSAGE_BUFFERS[user_id] = []

        USER_MESSAGE_BUFFERS[user_id].append(text)
        USER_LAST_MESSAGES[user_id] = message

        # Cancel existing pending debounce task if running, restart 1.0s timer
        if user_id in USER_DEBOUNCE_TASKS and not USER_DEBOUNCE_TASKS[user_id].done():
            USER_DEBOUNCE_TASKS[user_id].cancel()

        USER_DEBOUNCE_TASKS[user_id] = asyncio.create_task(
            _process_user_accumulated_messages(user_id)
        )

    async def _handle_combined_text_message(message: Message, text: str):
        """Core text processing logic for accumulated recipient messages."""
        user_id = message.from_user.id
        eff_settings = storage_manager.get_effective_settings(user_id, settings)
        user_ai_extractor = AIExtractor(eff_settings)
        user_np_client = NovaPoshtaClient(eff_settings)

        # Check if there is an active pending verification session for this user
        prev_parsed_info = None
        active_session_id = USER_ACTIVE_SESSIONS.get(user_id)
        if active_session_id and active_session_id in PENDING_SESSIONS:
            prev_parsed_info = PENDING_SESSIONS[active_session_id].get("parsed_info")

        status_msg = await message.answer(
            "⏳ *Обробка повідомлення та аналіз реквізитів через AI...*", parse_mode="Markdown"
        )

        try:
            # 1. Parse text with AI (with optional previous context)
            parsed_info = await user_ai_extractor.parse_text(
                text, previous_info=prev_parsed_info
            )

            # Handle conversational / chat intent
            if not parsed_info.is_recipient_info:
                resp_text = (
                    parsed_info.conversational_response
                    or "👋 Привіт! Я ваш AI-асистент Нової Пошти. Надішліть реквізити отримувача (ПІБ, телефон, місто, номер відділення) для створення ТТН!"
                )
                await status_msg.edit_text(resp_text, parse_mode="Markdown")
                return

            # Get or generate active session ID
            session_id = active_session_id or str(uuid.uuid4())[:8]

            # Check missing required fields
            missing_fields = []
            if not parsed_info.last_name:
                missing_fields.append("👤 Прізвище та Ім'я отримувача")
            if not parsed_info.phone:
                missing_fields.append("📞 Номер телефону отримувача")
            if not parsed_info.city_name:
                missing_fields.append("🏙 Місто / Населений пункт")
            if not parsed_info.warehouse_number:
                missing_fields.append("📦 Номер відділення або поштомату")

            if missing_fields:
                # Save partial session so follow-up forwarded messages merge seamlessly
                PENDING_SESSIONS[session_id] = {
                    "parsed_info": parsed_info,
                    "user_id": user_id,
                }
                USER_ACTIVE_SESSIONS[user_id] = session_id

                missing_str = "\n".join([f"• {field}" for field in missing_fields])
                known_name = parsed_info.full_name or "Не вказано"
                known_phone = parsed_info.phone or "Не вказано"
                known_city = parsed_info.city_name or "Не вказано"
                known_wh = f"{'Поштомат' if parsed_info.is_postomat else 'Відділення'} № {parsed_info.warehouse_number}" if parsed_info.warehouse_number else "Не вказано"

                await status_msg.edit_text(
                    "⏳ *Отримано часткові реквізити отримувача (Очікую решту даних...)*\n\n"
                    "📋 *Вже збережено:* \n"
                    f"• ПІБ: `{known_name}`\n"
                    f"• Телефон: `{known_phone}`\n"
                    f"• Місто: `{known_city}`\n"
                    f"• Пункт: `{known_wh}`\n\n"
                    "⚠️ *Очікую доповнення:* \n"
                    f"{missing_str}\n\n"
                    "💡 *Надішліть або перепостіть наступне повідомлення з рештою даних!*"
                )
                return

            # 2. Lookup City in Nova Poshta
            await status_msg.edit_text(
                "🔍 *Пошук міста та відділення у базі Нової Пошти...*", parse_mode="Markdown"
            )
            cities = await user_np_client.search_city(parsed_info.city_name)
            if not cities:
                await status_msg.edit_text(
                    f"❌ Місто *'{parsed_info.city_name}'* не знайдено у базі Нової Пошти. Перевірте написання."
                )
                return

            matched_city = cities[0]

            # 3. Lookup Warehouse / Postomat
            warehouse = await user_np_client.get_warehouse(
                city_ref=matched_city.ref,
                warehouse_number=parsed_info.warehouse_number,
                is_postomat=parsed_info.is_postomat,
            )

            if not warehouse:
                w_type = "Поштомат" if parsed_info.is_postomat else "Відділення"
                await status_msg.edit_text(
                    f"❌ {w_type} *№ {parsed_info.warehouse_number}* у місті *{matched_city.description}* не знайдено."
                )
                return

            # Enforce minimum declared value of 500 UAH
            declared_val = max(
                parsed_info.declared_value or eff_settings.default_declared_value,
                500.0,
            )
            cargo_desc = parsed_info.cargo_description or "Посилка"

            # Create or update session for interactive confirmation
            session_id = active_session_id or str(uuid.uuid4())[:8]
            PENDING_SESSIONS[session_id] = {
                "parsed_info": parsed_info,
                "city": matched_city,
                "warehouse": warehouse,
                "payer_type": eff_settings.default_payer_type,
                "cargo_type": eff_settings.default_cargo_type,
                "declared_value": declared_val,
                "cargo_description": cargo_desc,
                "user_id": user_id,
            }
            USER_ACTIVE_SESSIONS[user_id] = session_id

            card_text = (
                "📋 *Розпарсені дані отримувача для перевірки:*\n\n"
                f"👤 *Отримувач:* {parsed_info.full_name}\n"
                f"📞 *Телефон:* `{parsed_info.phone}`\n"
                f"🏙 *Місто:* {matched_city.description}\n"
                f"📦 *Пункт призначення:* {warehouse.description}\n"
                f"📝 *Опис вантажу:* {cargo_desc}\n"
                f"💰 *Оціночна вартість:* {int(declared_val)} грн (Мін. 500 грн)\n\n"
                "Перевірте дані та оберіть дію нижче:"
            )

            await status_msg.edit_text(
                card_text,
                parse_mode="Markdown",
                reply_markup=get_confirmation_keyboard(
                    payer_type=eff_settings.default_payer_type,
                    cargo_type=eff_settings.default_cargo_type,
                    declared_value=declared_val,
                    session_id=session_id,
                ),
            )
        except Exception as e:
            logger.error(f"Error processing text message: {e}", exc_info=True)
            await status_msg.edit_text(f"❌ *Сталася помилка:* {str(e)}", parse_mode="Markdown")

    @router.callback_query(WaybillActionCallback.filter())
    async def process_waybill_callback(
        callback: CallbackQuery, callback_data: WaybillActionCallback
    ):
        """Handle inline keyboard buttons for waybill creation."""
        session_id = callback_data.session_id
        session = PENDING_SESSIONS.get(session_id)

        if not session:
            await callback.answer(
                "Сесію завершено. Надішліть реквізити отримувача знову.", show_alert=True
            )
            return

        action = callback_data.action
        user_id = session["user_id"]

        if action == "cancel":
            PENDING_SESSIONS.pop(session_id, None)
            USER_ACTIVE_SESSIONS.pop(user_id, None)
            await callback.message.edit_text("❌ *Створення накладної скасовано.*", parse_mode="Markdown")
            await callback.answer()
            return

        if action == "toggle_payer":
            new_payer = "Sender" if callback_data.payer_type == "Recipient" else "Recipient"
            session["payer_type"] = new_payer
            await callback.message.edit_reply_markup(
                reply_markup=get_confirmation_keyboard(
                    payer_type=new_payer,
                    cargo_type=session["cargo_type"],
                    declared_value=session["declared_value"],
                    session_id=session_id,
                )
            )
            payer_ua = "Отримувач" if new_payer == "Recipient" else "Відправник"
            await callback.answer(f"Платника змінено на: {payer_ua}")
            return

        if action == "toggle_cargo":
            new_cargo = "Documents" if callback_data.cargo_type == "Parcel" else "Parcel"
            session["cargo_type"] = new_cargo
            await callback.message.edit_reply_markup(
                reply_markup=get_confirmation_keyboard(
                    payer_type=session["payer_type"],
                    cargo_type=new_cargo,
                    declared_value=session["declared_value"],
                    session_id=session_id,
                )
            )
            cargo_ua = "Посилка" if new_cargo == "Parcel" else "Документи"
            await callback.answer(f"Тип вантажу змінено на: {cargo_ua}")
            return

        if action == "cycle_value":
            current_val = session["declared_value"]
            try:
                curr_idx = VALUE_OPTIONS.index(current_val)
                next_val = VALUE_OPTIONS[(curr_idx + 1) % len(VALUE_OPTIONS)]
            except ValueError:
                next_val = VALUE_OPTIONS[0]

            session["declared_value"] = next_val

            parsed_info = session["parsed_info"]
            city = session["city"]
            warehouse = session["warehouse"]
            cargo_desc = session["cargo_description"]

            card_text = (
                "📋 *Розпарсені дані отримувача для перевірки:*\n\n"
                f"👤 *Отримувач:* {parsed_info.full_name}\n"
                f"📞 *Телефон:* `{parsed_info.phone}`\n"
                f"🏙 *Місто:* {city.description}\n"
                f"📦 *Пункт призначення:* {warehouse.description}\n"
                f"📝 *Опис вантажу:* {cargo_desc}\n"
                f"💰 *Оціночна вартість:* {int(next_val)} грн (Мін. 500 грн)\n\n"
                "Перевірте дані та оберіть дію нижче:"
            )

            await callback.message.edit_text(
                card_text,
                parse_mode="Markdown",
                reply_markup=get_confirmation_keyboard(
                    payer_type=session["payer_type"],
                    cargo_type=session["cargo_type"],
                    declared_value=next_val,
                    session_id=session_id,
                ),
            )
            await callback.answer(f"Оціночну вартість встановлено: {int(next_val)} грн")
            return

        if action == "confirm":
            editing_ref = session.get("editing_draft_ref")
            action_title = "Оновлення" if editing_ref else "Генерація"
            await callback.answer(f"{action_title} express-накладної...")
            await callback.message.edit_text(
                f"⏳ *Реєстрація отримувача та {action_title.lower()} express-накладної у базі Нової Пошти...*",
                parse_mode="Markdown",
            )

            eff_settings = storage_manager.get_effective_settings(user_id, settings)
            user_np_client = NovaPoshtaClient(eff_settings)

            parsed_info = session["parsed_info"]
            city = session["city"]
            warehouse = session["warehouse"]
            payer_type = session["payer_type"]
            declared_value = session["declared_value"]
            cargo_desc = session["cargo_description"]

            try:
                # Create recipient counterparty
                recipient_res = await user_np_client.create_recipient_counterparty(
                    first_name=parsed_info.first_name or "",
                    last_name=parsed_info.last_name or "",
                    middle_name=parsed_info.middle_name or "",
                    phone=parsed_info.phone or "",
                )

                # Create or Update Waybill
                if editing_ref:
                    wb_res = await user_np_client.update_waybill(
                        document_ref=editing_ref,
                        recipient_cp_ref=recipient_res.counterparty_ref,
                        recipient_contact_ref=recipient_res.contact_person_ref,
                        recipient_phone=parsed_info.phone or "",
                        recipient_city_ref=city.ref,
                        recipient_warehouse_ref=warehouse.ref,
                        payer_type=payer_type,
                        description=cargo_desc,
                        seats_amount=eff_settings.default_seats_amount,
                        weight=eff_settings.default_weight,
                        declared_value=declared_value,
                    )
                    storage_manager.delete_user_draft(user_id, editing_ref)
                else:
                    wb_res = await user_np_client.create_waybill(
                        recipient_cp_ref=recipient_res.counterparty_ref,
                        recipient_contact_ref=recipient_res.contact_person_ref,
                        recipient_phone=parsed_info.phone or "",
                        recipient_city_ref=city.ref,
                        recipient_warehouse_ref=warehouse.ref,
                        payer_type=payer_type,
                        description=cargo_desc,
                        seats_amount=eff_settings.default_seats_amount,
                        weight=eff_settings.default_weight,
                        declared_value=declared_value,
                    )

                PENDING_SESSIONS.pop(session_id, None)
                USER_ACTIVE_SESSIONS.pop(user_id, None)

                # Save created/updated draft locally for user
                draft_item = SavedDraft(
                    ref=wb_res.ref,
                    int_doc_number=wb_res.int_doc_number,
                    recipient_name=parsed_info.full_name,
                    recipient_phone=parsed_info.phone or "",
                    city_description=city.description,
                    warehouse_description=warehouse.description,
                    payer_type=payer_type,
                    cargo_description=cargo_desc,
                    declared_value=declared_value,
                    cost=wb_res.cost,
                    created_at=datetime.date.today().strftime("%d.%m.%Y"),
                )
                storage_manager.add_user_draft(user_id, draft_item)

                tracking_url = (
                    f"https://novaposhta.ua/tracking/?cargo_number={wb_res.int_doc_number}"
                )
                payer_ua = "Отримувач" if payer_type == "Recipient" else "Відправник"
                success_title = "одно успішно оновлено" if editing_ref else "о успішно створено"

                success_card = (
                    f"✅ *Express-накладну{success_title}!*\n\n"
                    f"🎫 *Номер ТТН:* `{wb_res.int_doc_number}`\n"
                    f"👤 *Отримувач:* {parsed_info.full_name}\n"
                    f"📞 *Телефон:* `{parsed_info.phone}`\n"
                    f"🏙 *Місто:* {city.description}\n"
                    f"📦 *Пункт призначення:* {warehouse.description}\n"
                    f"📝 *Опис вантажу:* {cargo_desc}\n"
                    f"💳 *Платник:* {payer_ua}\n"
                    f"💰 *Вартість доставки:* ~{wb_res.cost} грн | *Оцінка:* {int(declared_value)} грн\n"
                    f"📅 *Очікувана дата доставки:* {wb_res.estimated_delivery_date or 'Не вказано'}\n\n"
                    f"🔗 [Відстежити ТТН на сайті Нової Пошти]({tracking_url})"
                )

                await callback.message.edit_text(
                    success_card,
                    parse_mode="Markdown",
                    disable_web_page_preview=True,
                    reply_markup=get_draft_keyboard(ref=wb_res.ref),
                )
            except Exception as err:
                logger.error(f"Failed to create/update waybill: {err}", exc_info=True)
                await callback.message.edit_text(
                    f"❌ *Помилка формування ТТН:* {str(err)}", parse_mode="Markdown"
                )

    @router.callback_query(DraftActionCallback.filter())
    async def process_draft_callback(
        callback: CallbackQuery, callback_data: DraftActionCallback
    ):
        """Handle inline actions on saved waybill drafts."""
        user_id = callback.from_user.id
        eff_settings = storage_manager.get_effective_settings(user_id, settings)
        user_np_client = NovaPoshtaClient(eff_settings)

        action = callback_data.action
        ref = callback_data.ref

        if action == "edit":
            await callback.answer("Завантаження чернетки для редагування...")
            drafts = storage_manager.get_user_drafts(user_id)
            target_draft = next((d for d in drafts if d.ref == ref), None)

            if not target_draft:
                await callback.message.edit_text(
                    "⚠️ *Чернетку не знайдено.*", parse_mode="Markdown"
                )
                return

            user_ai_extractor = AIExtractor(eff_settings)
            draft_prompt = (
                f"{target_draft.recipient_name} {target_draft.recipient_phone} "
                f"{target_draft.city_description} {target_draft.warehouse_description}. "
                f"Опис вантажу: {target_draft.cargo_description}. Оціночна вартість: {target_draft.declared_value}"
            )

            # Use AI to parse the stored draft text into structured ParsedRecipientInfo
            parsed_info = await user_ai_extractor.parse_text(draft_prompt)

            city = None
            warehouse = None
            if parsed_info.city_name:
                cities = await user_np_client.search_city(parsed_info.city_name)
                if cities:
                    city = cities[0]
                    if parsed_info.warehouse_number:
                        warehouse = await user_np_client.get_warehouse(
                            city_ref=city.ref,
                            warehouse_number=parsed_info.warehouse_number,
                            is_postomat=parsed_info.is_postomat,
                        )

            # Activate editing session
            session_id = str(uuid.uuid4())[:8]
            PENDING_SESSIONS[session_id] = {
                "parsed_info": parsed_info,
                "city": city,
                "warehouse": warehouse,
                "payer_type": target_draft.payer_type,
                "cargo_type": "Parcel",
                "declared_value": target_draft.declared_value,
                "cargo_description": target_draft.cargo_description,
                "user_id": user_id,
                "editing_draft_ref": ref,
            }
            USER_ACTIVE_SESSIONS[user_id] = session_id

            card_text = (
                f"✏️ *Редагування ТТН № `{target_draft.int_doc_number}`*\n\n"
                f"👤 *Отримувач:* {target_draft.recipient_name}\n"
                f"📞 *Телефон:* `{target_draft.recipient_phone}`\n"
                f"🏙 *Місто:* {target_draft.city_description}\n"
                f"📦 *Пункт призначення:* {target_draft.warehouse_description}\n"
                f"📝 *Опис вантажу:* {target_draft.cargo_description}\n"
                f"💰 *Оціночна вартість:* {int(target_draft.declared_value)} грн\n\n"
                "💡 *Надішліть будь-які зміни живим текстом* (наприклад: *'зміни опис на сувенір'*, *'оцінка 2000 грн'*), "
                "або скористайтеся кнопками нижче:"
            )

            await callback.message.edit_text(
                card_text,
                parse_mode="Markdown",
                reply_markup=get_confirmation_keyboard(
                    payer_type=target_draft.payer_type,
                    cargo_type="Parcel",
                    declared_value=target_draft.declared_value,
                    session_id=session_id,
                ),
            )
            return

        if action == "delete":
            await callback.answer("Видалення накладної з Нової Пошти...")
            try:
                success = await user_np_client.delete_waybill(ref)
                storage_manager.delete_user_draft(user_id, ref)

                if success:
                    await callback.message.edit_text(
                        "🗑 *Express-накладну (ТТН) успішно видалено з бази Нової Пошти!*",
                        parse_mode="Markdown",
                    )
                else:
                    await callback.message.edit_text(
                        "⚠️ *Не вдалося видалити ТТН з Нової Пошти (можливо, її вже скасовано або оброблено).*",
                        parse_mode="Markdown",
                    )
            except Exception as e:
                logger.error(f"Error deleting waybill: {e}", exc_info=True)
                await callback.answer(f"Помилка видалення: {str(e)}", show_alert=True)
