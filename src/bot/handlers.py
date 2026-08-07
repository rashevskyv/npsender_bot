"""Telegram bot message handlers and callback handlers."""

import logging
import uuid
from typing import Dict, Any

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery

from src.config import Settings
from src.ai.extractor import AIExtractor
from src.nova_poshta.client import NovaPoshtaClient
from src.bot.keyboards import get_confirmation_keyboard, WaybillActionCallback

logger = logging.getLogger(__name__)
router = Router()

# In-memory storage for active pending waybill verification sessions
# session_id -> dict of parsed objects & state
PENDING_SESSIONS: Dict[str, Dict[str, Any]] = {}


def register_handlers(
    settings: Settings, ai_extractor: AIExtractor, np_client: NovaPoshtaClient
):
    """Factory to inject dependencies into router handlers."""

    @router.message(Command("start"))
    async def cmd_start(message: Message):
        """Welcome message and basic instructions."""
        welcome_text = (
            "👋 **Welcome to Nova Poshta AI Waybill Generator Bot!**\n\n"
            "Send me recipient details in free format (Full name, Phone number, City, Branch/Postomat number), "
            "and I will parse it with AI and generate an Express Waybill (ТТН) for Nova Poshta.\n\n"
            "**Example message:**\n"
            "`Іванов Петро Васильович 0971234567 Львів відділення 12`\n\n"
            "Type /help for more instructions or /settings to check status."
        )
        await message.answer(welcome_text, parse_mode="Markdown")

    @router.message(Command("help"))
    async def cmd_help(message: Message):
        """Help instructions."""
        help_text = (
            "📖 **How to use this bot:**\n\n"
            "1. Paste recipient info in any order in a single message.\n"
            "2. AI will extract Recipient Name, Phone, City, and Branch/Postomat number.\n"
            "3. The bot searches Nova Poshta database to locate exact City and Warehouse references.\n"
            "4. Review the details, toggle Payer (Recipient/Sender) if needed, and click **Create Waybill**.\n\n"
            "For bug reports or configuration updates, contact your administrator."
        )
        await message.answer(help_text, parse_mode="Markdown")

    @router.message(Command("settings"))
    async def cmd_settings(message: Message):
        """Show current bot settings status."""
        status_text = (
            "⚙️ **Bot Configuration Status:**\n\n"
            f"• AI Provider: `{settings.ai_provider}`\n"
            f"• AI Model: `{settings.ai_model}`\n"
            f"• Default Payer: `{settings.default_payer_type}`\n"
            f"• Default Cargo: `{settings.default_cargo_type}`\n"
            f"• Sender Configured: `{'Yes' if settings.sender_counterparty_ref else 'No (Run fetch_sender_info)'}`"
        )
        await message.answer(status_text, parse_mode="Markdown")

    @router.message(F.text)
    async def process_text_message(message: Message):
        """Handle raw user text with recipient details."""
        text = message.text.strip()
        if text.startswith("/"):
            return

        status_msg = await message.answer("⏳ *Parsing recipient details with AI...*", parse_mode="Markdown")

        try:
            # 1. Parse text with AI
            parsed_info = await ai_extractor.parse_text(text)

            if not parsed_info.city_name or not parsed_info.last_name:
                await status_msg.edit_text(
                    "⚠️ *Could not extract complete recipient info.*\n"
                    "Please make sure your message contains at least a Last Name, Phone Number, City, and Branch/Postomat number."
                )
                return

            # 2. Lookup City in Nova Poshta
            await status_msg.edit_text("🔍 *Searching Nova Poshta database for City and Branch...*", parse_mode="Markdown")
            cities = await np_client.search_city(parsed_info.city_name)
            if not cities:
                await status_msg.edit_text(
                    f"❌ City *'{parsed_info.city_name}'* was not found in Nova Poshta database. Please check spelling."
                )
                return

            matched_city = cities[0]

            # 3. Lookup Warehouse / Postomat
            warehouse = None
            if parsed_info.warehouse_number:
                warehouse = await np_client.get_warehouse(
                    city_ref=matched_city.ref,
                    warehouse_number=parsed_info.warehouse_number,
                    is_postomat=parsed_info.is_postomat,
                )

            if not warehouse:
                w_type = "Postomat" if parsed_info.is_postomat else "Branch"
                await status_msg.edit_text(
                    f"❌ {w_type} *№ {parsed_info.warehouse_number or 'N/A'}* in city *{matched_city.description}* was not found."
                )
                return

            # Create session for interactive confirmation
            session_id = str(uuid.uuid4())[:8]
            PENDING_SESSIONS[session_id] = {
                "parsed_info": parsed_info,
                "city": matched_city,
                "warehouse": warehouse,
                "payer_type": settings.default_payer_type,
                "cargo_type": settings.default_cargo_type,
                "user_id": message.from_user.id,
            }

            card_text = (
                "📋 *Parsed Recipient Details for Verification:*\n\n"
                f"👤 *Recipient:* {parsed_info.full_name}\n"
                f"📞 *Phone:* `{parsed_info.phone or 'N/A'}`\n"
                f"🏙 *City:* {matched_city.description}\n"
                f"📦 *Destination:* {warehouse.description}\n\n"
                "Please review the details below and select an action:"
            )

            await status_msg.edit_text(
                card_text,
                parse_mode="Markdown",
                reply_markup=get_confirmation_keyboard(
                    payer_type=settings.default_payer_type,
                    cargo_type=settings.default_cargo_type,
                    session_id=session_id,
                ),
            )
        except Exception as e:
            logger.error(f"Error processing text message: {e}", exc_info=True)
            await status_msg.edit_text(f"❌ *An error occurred:* {str(e)}", parse_mode="Markdown")

    @router.callback_query(WaybillActionCallback.filter())
    async def process_waybill_callback(callback: CallbackQuery, callback_data: WaybillActionCallback):
        """Handle inline keyboard buttons for waybill creation."""
        session_id = callback_data.session_id
        session = PENDING_SESSIONS.get(session_id)

        if not session:
            await callback.answer("Session expired. Please paste recipient details again.", show_alert=True)
            return

        action = callback_data.action

        if action == "cancel":
            PENDING_SESSIONS.pop(session_id, None)
            await callback.message.edit_text("❌ *Waybill creation cancelled.*", parse_mode="Markdown")
            await callback.answer()
            return

        if action == "toggle_payer":
            new_payer = "Sender" if callback_data.payer_type == "Recipient" else "Recipient"
            session["payer_type"] = new_payer
            await callback.message.edit_reply_markup(
                reply_markup=get_confirmation_keyboard(
                    payer_type=new_payer,
                    cargo_type=session["cargo_type"],
                    session_id=session_id,
                )
            )
            await callback.answer(f"Payer changed to: {new_payer}")
            return

        if action == "toggle_cargo":
            new_cargo = "Documents" if callback_data.cargo_type == "Parcel" else "Parcel"
            session["cargo_type"] = new_cargo
            await callback.message.edit_reply_markup(
                reply_markup=get_confirmation_keyboard(
                    payer_type=session["payer_type"],
                    cargo_type=new_cargo,
                    session_id=session_id,
                )
            )
            await callback.answer(f"Cargo type changed to: {new_cargo}")
            return

        if action == "confirm":
            await callback.answer("Creating Express Waybill (ТТН)...")
            await callback.message.edit_text("⏳ *Registering Recipient and Generating ТТН...*", parse_mode="Markdown")

            parsed_info = session["parsed_info"]
            city = session["city"]
            warehouse = session["warehouse"]
            payer_type = session["payer_type"]

            try:
                # Create recipient counterparty
                recipient_res = await np_client.create_recipient_counterparty(
                    first_name=parsed_info.first_name or "",
                    last_name=parsed_info.last_name or "",
                    middle_name=parsed_info.middle_name or "",
                    phone=parsed_info.phone or "",
                )

                # Create Waybill
                wb_res = await np_client.create_waybill(
                    recipient_cp_ref=recipient_res.counterparty_ref,
                    recipient_contact_ref=recipient_res.contact_person_ref,
                    recipient_phone=parsed_info.phone or "",
                    recipient_city_ref=city.ref,
                    recipient_warehouse_ref=warehouse.ref,
                    payer_type=payer_type,
                    description=parsed_info.cargo_description or "Посилка",
                    seats_amount=settings.default_seats_amount,
                    weight=settings.default_weight,
                    declared_value=parsed_info.declared_value or settings.default_declared_value,
                )

                PENDING_SESSIONS.pop(session_id, None)

                tracking_url = f"https://novaposhta.ua/tracking/?cargo_number={wb_res.int_doc_number}"

                success_card = (
                    "✅ *Express Waybill Successfully Created!*\n\n"
                    f"🎫 *ТТН Number:* `{wb_res.int_doc_number}`\n"
                    f"👤 *Recipient:* {parsed_info.full_name}\n"
                    f"📞 *Phone:* `{parsed_info.phone}`\n"
                    f"🏙 *City:* {city.description}\n"
                    f"📦 *Destination:* {warehouse.description}\n"
                    f"💳 *Payer:* {payer_type}\n"
                    f"💰 *Cost:* ~{wb_res.cost} UAH\n"
                    f"📅 *Estimated Delivery:* {wb_res.estimated_delivery_date or 'N/A'}\n\n"
                    f"🔗 [Track Waybill on Nova Poshta]({tracking_url})"
                )

                await callback.message.edit_text(
                    success_card, parse_mode="Markdown", disable_web_page_preview=True
                )
            except Exception as err:
                logger.error(f"Failed to create waybill: {err}", exc_info=True)
                await callback.message.edit_text(
                    f"❌ *Failed to create Waybill:* {str(err)}", parse_mode="Markdown"
                )
