"""Main entry point for starting Nova Poshta Telegram Bot."""

import asyncio
import logging
import sys

from aiogram import Bot, Dispatcher

from src.config import get_settings
from src.storage import UserSettingsManager
from src.ai.extractor import AIExtractor
from src.nova_poshta.client import NovaPoshtaClient
from src.bot.handlers import router, register_handlers

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)


async def main():
    """Start Telegram Bot application."""
    logger.info("Initializing Nova Poshta AI Waybill Bot...")
    settings = get_settings()
    storage_manager = UserSettingsManager()

    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()

    ai_extractor = AIExtractor(settings)
    np_client = NovaPoshtaClient(settings)

    # Register handlers with dependencies
    register_handlers(settings, ai_extractor, np_client, storage_manager)
    dp.include_router(router)

    # Setup Telegram Desktop and Mobile persistent Menu button
    await setup_bot_commands(bot)

    logger.info("Starting Bot Long Polling...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


async def setup_bot_commands(bot: Bot) -> None:
    """Configure default bot commands and persistent menu button for Desktop and Mobile."""
    from aiogram.types import BotCommand, BotCommandScopeDefault, MenuButtonCommands

    commands = [
        BotCommand(command="start", description="Головне меню / перезапуск"),
        BotCommand(command="users", description="👥 Керування користувачами та лімітами"),
        BotCommand(command="settings", description="⚙️ Налаштування профілю"),
        BotCommand(command="client_card", description="💳 Картка клієнта для сканування"),
        BotCommand(command="outgoing", description="📤 Вихідні відправлення"),
        BotCommand(command="incoming", description="📥 Вхідні відправлення"),
        BotCommand(command="drafts", description="📝 Чернетки ТТН"),
        BotCommand(command="scansheet", description="📋 Реєстри (ScanSheet)"),
        BotCommand(command="cod", description="💰 Накладений платіж"),
        BotCommand(command="track", description="🔍 Відстежити посилку"),
        BotCommand(command="help", description="❓ Довідка та команди"),
    ]
    try:
        await bot.set_my_commands(commands, scope=BotCommandScopeDefault())
        await bot.set_chat_menu_button(menu_button=MenuButtonCommands())
        logger.info("Bot commands and menu button successfully configured.")
    except Exception as e:
        logger.warning(f"Failed to set bot commands: {e}")


if __name__ == "__main__":
    asyncio.run(main())
