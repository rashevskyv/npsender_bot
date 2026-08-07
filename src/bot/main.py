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

    logger.info("Starting Bot Long Polling...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
