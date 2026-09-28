"""Comprehensive unit tests for universal offline settlements database and fuzzy city search."""

import pytest
from unittest.mock import AsyncMock

from src.utils.city_search import CitySearchEngine, city_search_engine
from src.nova_poshta.client import NovaPoshtaClient
from src.ai.extractor import AIExtractor
from src.ai.schemas import ParsedRecipientInfo
from src.config import Settings


def test_city_search_engine_exact_ukrainian():
    """Verify exact lookups for standard Ukrainian city names."""
    major_cities = [
        ("Київ", "Київ"),
        ("Харків", "Харків"),
        ("Одеса", "Одеса"),
        ("Дніпро", "Дніпро"),
        ("Львів", "Львів"),
        ("Вінниця", "Вінниця"),
        ("Синельникове", "Синельникове"),
        ("Царичанка", "Царичанка"),
    ]
    for query, expected in major_cities:
        res = city_search_engine.resolve_canonical_city_name(query)
        assert res == expected, f"Expected '{expected}' for query '{query}', got '{res}'"


def test_city_search_engine_russian_transliterations():
    """Verify exact or near-exact lookups for Russian city names."""
    ru_cities = [
        ("Киев", "Київ"),
        ("Харьков", "Харків"),
        ("Одесса", "Одеса"),
        ("Днепр", "Дніпро"),
        ("Львов", "Львів"),
        ("Николаев", "Миколаїв"),
        ("Кривой Рог", "Кривий Ріг"),
        ("Запорожье", "Запоріжжя"),
        ("Шепетовка", "Шепетівка"),
        ("Синельниково", "Синельникове"),
        ("Ивано-Франковск", "Івано-Франківськ"),
    ]
    for query, expected in ru_cities:
        res = city_search_engine.resolve_canonical_city_name(query)
        assert res == expected, f"Expected '{expected}' for query '{query}', got '{res}'"


def test_city_search_engine_typos_and_vowel_confusions():
    """Verify systematic typo handling (i/y, e/ye vowel confusions, missing characters)."""
    typo_cases = [
        ("Сінельникове", "Синельникове"),
        ("Сенельниково", "Синельникове"),
        ("Винниця", "Вінниця"),
        ("Бравари", "Бровари"),
        ("Житомер", "Житомир"),
        ("Камянець-Подільский", "Кам'янець-Подільський"),
        ("Камянець-Подольський", "Кам'янець-Подільський"),
    ]
    for query, expected in typo_cases:
        res = city_search_engine.resolve_canonical_city_name(query)
        assert res == expected, f"Expected '{expected}' for query '{query}', got '{res}'"


def test_city_search_engine_renamed_cities():
    """Verify decommunized and renamed cities resolve to current official names."""
    renamed_cases = [
        ("Новомосковськ", "Самар"),
        ("Новомосковск", "Самар"),
        ("Червоноград", "Шептицький"),
        ("Дніпродзержинськ", "Кам'янське"),
        ("Днепродзержинск", "Кам'янське"),
        ("Кузнецовськ", "Вараш"),
        ("Кузнецовск", "Вараш"),
    ]
    for query, expected in renamed_cases:
        res = city_search_engine.resolve_canonical_city_name(query)
        assert res == expected, f"Expected '{expected}' for query '{query}', got '{res}'"


def test_city_search_engine_grammatical_inflections():
    """Verify handling of Ukrainian grammatical case endings and prepositions."""
    inflections = [
        ("Синельниковому", "Синельникове"),
        ("у Львові", "Львів"),
        ("в Одесі", "Одеса"),
        ("до Києва", "Київ"),
        ("в Полтаві", "Полтава"),
        ("у Сумах", "Суми"),
        ("в Тернополі", "Тернопіль"),
        ("Тернополе", "Тернопіль"),
        ("Царичанке", "Царичанка"),
        ("Коростене", "Коростень"),
    ]
    for query, expected in inflections:
        res = city_search_engine.resolve_canonical_city_name(query)
        assert res == expected, f"Expected '{expected}' for query '{query}', got '{res}'"


def test_city_search_engine_multi_word_settlements():
    """Verify multi-word settlements are accurately resolved."""
    multi_word_cases = [
        ("Великі Копані", "Великі Копані"),
        ("с.Великі Копані", "Великі Копані"),
        ("Кривий Ріг", "Кривий Ріг"),
        ("Кам'янець-Подільський", "Кам'янець-Подільський"),
    ]
    for query, expected in multi_word_cases:
        res = city_search_engine.resolve_canonical_city_name(query)
        assert res == expected, f"Expected '{expected}' for query '{query}', got '{res}'"


@pytest.mark.asyncio
async def test_nova_poshta_client_search_city_fuzzy_fallback():
    """Verify NovaPoshtaClient falls back to offline fuzzy search when API returns empty data."""
    client = NovaPoshtaClient(Settings())
    # Simulate API returning empty data on spelling variants
    client._post = AsyncMock(return_value={"data": []})

    results = await client.search_city("Сінельникове")
    assert len(results) > 0
    top = results[0]
    assert top.description == "Синельникове"
    assert top.ref == "69da419c-3f5d-11de-b509-001d92f78698"

    results_brov = await client.search_city("Бравари")
    assert len(results_brov) > 0
    assert results_brov[0].description == "Бровари"
    assert results_brov[0].ref == "db5c88d7-391c-11dd-90d9-001a92567626"


def test_ai_extractor_heal_with_fuzzy_cities():
    """Verify AIExtractor.heal_parsed_recipient_info utilizes fuzzy resolution for messy inputs."""
    extractor = AIExtractor(Settings())

    cases = [
        (
            "М.Сінельникове Отд.3 Аніконов Антон 0991234567",
            "Синельникове",
            3,
            "Аніконов",
            "Антон",
        ),
        (
            "Винниця Відділення 5 Петренко Петро 0671112233",
            "Вінниця",
            5,
            "Петренко",
            "Петро",
        ),
        (
            "Камянець-Подольский поштомат 1234 Іванов Іван 0501234567",
            "Кам'янець-Подільський",
            1234,
            "Іванов",
            "Іван",
        ),
        (
            "Бравари №2 Шевченко Тарас 0951234567",
            "Бровари",
            2,
            "Шевченко",
            "Тарас",
        ),
        (
            "с.Великі Копані відділення 1 Сидоренко Сидор 0661234567",
            "Великі Копані",
            1,
            "Сидоренко",
            "Сидор",
        ),
    ]

    for raw_text, exp_city, exp_wh, exp_last, exp_first in cases:
        healed = extractor.heal_parsed_recipient_info(raw_text, ParsedRecipientInfo())
        assert healed.city_name == exp_city, f"City mismatch for '{raw_text}': {healed.city_name} != {exp_city}"
        assert healed.warehouse_number == exp_wh, f"Warehouse mismatch for '{raw_text}': {healed.warehouse_number} != {exp_wh}"
        assert healed.last_name == exp_last, f"Last name mismatch for '{raw_text}': {healed.last_name} != {exp_last}"
        assert healed.first_name == exp_first, f"First name mismatch for '{raw_text}': {healed.first_name} != {exp_first}"
