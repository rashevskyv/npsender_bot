"""CLI utility to sync complete offline settlements database from Nova Poshta API 2.0."""

import sys
import logging
from src.config import Settings
from src.utils.city_search import CitySearchEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    settings = Settings()
    if not settings.nova_poshta_api_key:
        logger.error("NOVA_POSHTA_API_KEY is not configured in environment or .env file.")
        sys.exit(1)

    logger.info("Starting settlements sync from Nova Poshta API (Address/getCities)...")
    count = CitySearchEngine.sync_database(
        api_key=settings.nova_poshta_api_key,
        api_url=settings.nova_poshta_api_url,
    )
    logger.info(f"Settlements database sync finished successfully: {count} settlements saved.")


if __name__ == "__main__":
    main()
