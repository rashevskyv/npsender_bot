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

    # Drop the "Меню" button so the persistent reply keyboard shows on desktop as on mobile
    await setup_bot_commands(bot)

    logger.info("Starting Bot Long Polling...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


async def setup_bot_commands(bot: Bot) -> None:
    """Remove the "Меню" command button: the persistent reply keyboard is the only menu, on desktop too.

    Telegram shows the "Меню" button while the bot has commands, so the list is cleared.
    Commands still work when typed.
    """
    from aiogram.types import BotCommandScopeDefault, MenuButtonDefault

    try:
        await bot.delete_my_commands(scope=BotCommandScopeDefault())
        await bot.set_chat_menu_button(menu_button=MenuButtonDefault())
        logger.info("Bot command menu removed; the reply keyboard is the menu.")
    except Exception as e:
        logger.warning(f"Failed to set bot commands: {e}")


if __name__ == "__main__":
    asyncio.run(main())
