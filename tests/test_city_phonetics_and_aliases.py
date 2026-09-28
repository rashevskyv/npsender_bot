"""Unit tests for Ukrainian city phonetic normalization, toponym aliases, and robust search variant resolution."""

import pytest
from unittest.mock import AsyncMock, patch

from src.utils.text_cleaner import (
    clean_city_name,
    get_city_search_variants,
    is_city_matched,
    CITY_TOPONYM_ALIASES,
)
from src.ai.schemas import ParsedRecipientInfo
from src.ai.extractor import AIExtractor
from src.config import Settings
from src.nova_poshta.client import NovaPoshtaClient


def test_clean_city_name():
    """Verify clean_city_name removes settlement prefixes, quotes, and normalizes apostrophes."""
    assert clean_city_name("М.Сінельникове") == "Сінельникове"
    assert clean_city_name("м. Сінельникове") == "Сінельникове"
    assert clean_city_name("місто Синельникове") == "Синельникове"
    assert clean_city_name("г. Синельниково") == "Синельниково"
    assert clean_city_name("г.Синельниково") == "Синельниково"
    assert clean_city_name("город Одесса") == "Одесса"
    assert clean_city_name("смт. Козелець") == "Козелець"
    assert clean_city_name("смт Козелець") == "Козелець"
    assert clean_city_name("с. Маяки") == "Маяки"
    assert clean_city_name("село Маяки") == "Маяки"
    assert clean_city_name("пос. Нове") == "Нове"
    assert clean_city_name("«Камʼянське»") == "Кам'янське"
    assert clean_city_name('"Київ"') == "Київ"
    assert clean_city_name("") == ""
    assert clean_city_name(None) == ""


def test_get_city_search_variants_phonetics_and_aliases():
    """Verify search variants generated for phonetic confusion, aliases, and Russian transliterations."""
    # Synelnykove (i -> y interchange, -o -> -e)
    vars_sin = get_city_search_variants("Сінельникове")
    assert "Сінельникове" in vars_sin
    assert "Синельникове" in vars_sin

    vars_sino = get_city_search_variants("Синельниково")
    assert "Синельниково" in vars_sino
    assert "Синельникове" in vars_sino

    vars_sino2 = get_city_search_variants("Сінельниково")
    assert "Синельникове" in vars_sino2

    # Renamed / Decommunized cities
    vars_novomosk = get_city_search_variants("Новомосковськ")
    assert "Новомосковськ" in vars_novomosk
    assert "Самар" in vars_novomosk

    vars_chervono = get_city_search_variants("Червоноград")
    assert "Червоноград" in vars_chervono
    assert "Шептицький" in vars_chervono

    vars_novograd = get_city_search_variants("Новоград-Волинський")
    assert "Новоград-Волинський" in vars_novograd
    assert "Звягель" in vars_novograd

    vars_kuzn = get_city_search_variants("Кузнецовськ")
    assert "Кузнецовськ" in vars_kuzn
    assert "Вараш" in vars_kuzn

    vars_dndz = get_city_search_variants("Дніпродзержинськ")
    assert "Кам'янське" in vars_dndz

    # Vowel y -> i interchange
    vars_vin = get_city_search_variants("Винниця")
    assert "Вінниця" in vars_vin

    # Plain city without labials or matching rules remains clean
    vars_kyiv = get_city_search_variants("Київ")
    assert vars_kyiv == ["Київ"]


def test_is_city_matched_comprehensive():
    """Verify is_city_matched recognizes phonetic variants, aliases, and prefix differences."""
    # Phonetic i/y and spelling variants
    assert is_city_matched("Сінельникове", "Синельникове")
    assert is_city_matched("М.Сінельникове", "Синельникове (Дніпропетровська обл)")
    assert is_city_matched("Синельниково", "Синельникове")
    assert is_city_matched("г. Синельниково", "м. Синельникове")
    assert is_city_matched("Винниця", "Вінниця")

    # Decommunized aliases
    assert is_city_matched("Новомосковськ", "Самар")
    assert is_city_matched("Червоноград", "Шептицький")
    assert is_city_matched("Звягель", "Новоград-Волинський")
    assert is_city_matched("Дніпродзержинськ", "Кам'янське")
    assert is_city_matched("Кузнецовськ", "Вараш")

    # Negative matches
    assert not is_city_matched("Київ", "Одеса")
    assert not is_city_matched("Харків", "Львів")
    assert not is_city_matched(None, "Синельникове")
    assert not is_city_matched("Синельникове", "")


def test_parsed_recipient_info_city_cleaning_and_aliases():
    """Verify ParsedRecipientInfo field validator automatically cleans prefixes and resolves aliases."""
    info1 = ParsedRecipientInfo(city_name="М.Сінельникове")
    assert info1.city_name == "Синельникове"

    info2 = ParsedRecipientInfo(city_name="м. Новомосковськ")
    assert info2.city_name == "Самар"

    info3 = ParsedRecipientInfo(city_name="Червоноград")
    assert info3.city_name == "Шептицький"

    info4 = ParsedRecipientInfo(city_name="м. Київ")
    assert info4.city_name == "Київ"


def test_heal_parsed_recipient_info_exact_user_screenshot():
    """Verify healing with exact user screenshot text:
    Оцінка 19000, планшет
    Переслано від Anton
    М.Сінельникове Отд.3
    Аніконов Антон Юрійович
    0638900050
    """
    raw_text = (
        "Оцінка 19000, планшет\n\n"
        "Переслано від Anton\n"
        "М.Сінельникове Отд.3\n"
        "Аніконов Антон Юрійович\n"
        "0638900050"
    )
    parsed = ParsedRecipientInfo()
    healed = AIExtractor.heal_parsed_recipient_info(raw_text, parsed)

    assert healed.city_name == "Синельникове"
    assert healed.warehouse_number == 3
    assert healed.is_postomat is False
    assert healed.phone == "0638900050"
    assert healed.last_name == "Аніконов"
    assert healed.first_name == "Антон"
    assert healed.middle_name == "Юрійович"
    assert healed.declared_value == 19000.0


@pytest.mark.asyncio
async def test_search_city_with_phonetic_variant_lookup():
    """Verify search_city queries variants in order and succeeds when fallback variant matches."""
    settings = Settings(
        telegram_bot_token="test_token",
        nova_poshta_api_key="test_api_key",
        ai_api_key="test_key",
    )
    client = NovaPoshtaClient(settings)

    # First variant "Сінельникове" returns empty data, second variant "Синельникове" returns city
    async def mock_post(model_name, called_method, method_properties):
        if called_method == "getCities":
            query = method_properties.get("FindByString")
            if query == "Синельникове":
                return {
                    "data": [
                        {
                            "Ref": "ref-synelnykove-1",
                            "Description": "Синельникове",
                            "AreaDescription": "Дніпропетровська",
                            "RegionsDescription": "Синельниківський",
                        }
                    ]
                }
            return {"data": []}
        elif called_method == "searchSettlements":
            return {
                "data": [
                    {
                        "Addresses": [
                            {
                                "DeliveryCity": "ref-synelnykove-1",
                                "Ref": "settle-ref-synelnykove-1",
                            }
                        ]
                    }
                ]
            }
        return {"data": []}

    with patch.object(client, "_post", side_effect=mock_post):
        cities = await client.search_city("Сінельникове")
        assert len(cities) == 1
        assert cities[0].description == "Синельникове"
        assert cities[0].ref == "ref-synelnykove-1"
        assert cities[0].settlement_ref == "settle-ref-synelnykove-1"


@pytest.mark.asyncio
async def test_search_city_fallback_to_search_settlements():
    """Verify search_city falls back to searchSettlements if getCities returns empty data across all variants."""
    settings = Settings(
        telegram_bot_token="test_token",
        nova_poshta_api_key="test_api_key",
        ai_api_key="test_key",
    )
    client = NovaPoshtaClient(settings)

    async def mock_post(model_name, called_method, method_properties):
        if called_method == "getCities":
            return {"data": []}
        elif called_method == "searchSettlements":
            query = method_properties.get("CityName")
            if query == "Синельникове":
                return {
                    "data": [
                        {
                            "Addresses": [
                                {
                                    "DeliveryCity": "delivery-city-synelnykove",
                                    "Ref": "settlement-ref-synelnykove",
                                    "MainDescription": "Синельникове",
                                    "Area": "Дніпропетровська",
                                    "Region": "Синельниківський",
                                }
                            ]
                        }
                    ]
                }
            return {"data": []}
        return {"data": []}

    with patch.object(client, "_post", side_effect=mock_post):
        cities = await client.search_city("Сінельникове")
        assert len(cities) == 1
        assert cities[0].description == "Синельникове"
        assert cities[0].ref == "delivery-city-synelnykove"
        assert cities[0].settlement_ref == "settlement-ref-synelnykove"
