"""Unit tests for Ukrainian apostrophe normalization across Nova Poshta client, AI extractor, and schemas."""

import pytest
from unittest.mock import AsyncMock, patch

from src.utils.text_cleaner import (
    normalize_apostrophes,
    get_city_search_variants,
    is_city_matched,
)
from src.ai.schemas import ParsedRecipientInfo
from src.ai.extractor import AIExtractor
from src.config import Settings
from src.nova_poshta.client import NovaPoshtaClient


def test_normalize_apostrophes():
    """Verify all Unicode apostrophe variants normalize to ASCII single quote."""
    # U+02BC (Modifier Letter Apostrophe - standard Ukrainian mobile keyboard)
    assert normalize_apostrophes("Камʼянське") == "Кам'янське"
    # U+2019 (Right Single Quotation Mark - iOS smart quote)
    assert normalize_apostrophes("Кам’янське") == "Кам'янське"
    # U+2018 (Left Single Quotation Mark)
    assert normalize_apostrophes("Кам‘янське") == "Кам'янське"
    # U+0060 (Grave Accent / backtick)
    assert normalize_apostrophes("Кам`янське") == "Кам'янське"
    # U+00B4 (Acute Accent)
    assert normalize_apostrophes("Кам´янське") == "Кам'янське"
    # U+02BB (Modifier Letter Turned Comma)
    assert normalize_apostrophes("Камʻянське") == "Кам'янське"
    # U+02B9 (Modifier Letter Prime)
    assert normalize_apostrophes("Камʹянське") == "Кам'янське"
    # U+2032 (Prime)
    assert normalize_apostrophes("Кам′янське") == "Кам'янське"
    # U+2033 (Double Prime)
    assert normalize_apostrophes("Кам″янське") == "Кам'янське"
    # None and empty
    assert normalize_apostrophes(None) == ""
    assert normalize_apostrophes("") == ""
    # Plain text without apostrophes
    assert normalize_apostrophes("Київ") == "Київ"


def test_get_city_search_variants():
    """Verify search variant generation for cities with and without apostrophes."""
    # Already normalized or with Unicode apostrophe
    vars_unicode = get_city_search_variants("Камʼянське")
    assert vars_unicode == ["Кам'янське"]

    vars_ios = get_city_search_variants("Кам’янське")
    assert vars_ios == ["Кам'янське"]

    # Input without apostrophe generates phonetic candidate variants
    vars_missing = get_city_search_variants("Камянське")
    assert "Камянське" in vars_missing
    assert "Кам'янське" in vars_missing

    vars_podilsky = get_city_search_variants("Камянець-Подільський")
    assert "Кам'янець-Подільський" in vars_podilsky

    # Plain city without labials or matching rules
    vars_kyiv = get_city_search_variants("Київ")
    assert vars_kyiv == ["Київ"]


def test_is_city_matched():
    """Verify city name matching across apostrophe variations and case."""
    assert is_city_matched("Камʼянське", "Кам'янське")
    assert is_city_matched("Кам’янське", "Кам'янське (Дніпропетровська обл)")
    assert is_city_matched("Кам'янське", "м. Камʼянське")
    assert is_city_matched("Кам`янське", "Кам'янське")
    assert not is_city_matched("Київ", "Одеса")
    assert not is_city_matched(None, "Кам'янське")
    assert not is_city_matched("Кам'янське", "")


def test_parsed_recipient_info_apostrophe_validator():
    """Verify Pydantic model automatically normalizes apostrophes on string fields."""
    info = ParsedRecipientInfo(
        city_name="Камʼянське",
        street_name="вул. Вʼячеслава Чорновола",
        last_name="Марʼяненко",
        first_name="Марʼяна",
        middle_name="Вʼячеславівна",
        cargo_description="Компʼютерні аксесуари",
    )
    assert info.city_name == "Кам'янське"
    assert info.street_name == "вул. В'ячеслава Чорновола"
    assert info.last_name == "Мар'яненко"
    assert info.first_name == "Мар'яна"
    assert info.middle_name == "В'ячеславівна"
    assert info.cargo_description == "Комп'ютерні аксесуари"


def test_heal_parsed_recipient_info_city_and_contact():
    """Verify regex heuristics heal recipient info when input text contains Unicode apostrophes."""
    raw_text = (
        "Переслано від BARBI\n"
        "НП 6, Камʼянське\n"
        "Плахотній Вадим Леонідович\n"
        "0668272396"
    )
    empty = ParsedRecipientInfo(is_recipient_info=True)
    healed = AIExtractor.heal_parsed_recipient_info(raw_text, empty)

    assert healed.city_name == "Кам'янське"
    assert healed.warehouse_number == 6
    assert healed.is_postomat is False
    assert healed.last_name == "Плахотній"
    assert healed.first_name == "Вадим"
    assert healed.middle_name == "Леонідович"
    assert healed.phone == "0668272396"


@pytest.mark.asyncio
async def test_search_city_queries_normalized_apostrophe():
    """Verify search_city queries Nova Poshta API using normalized ASCII apostrophe."""
    settings = Settings()
    client = NovaPoshtaClient(settings)

    with patch.object(client, "_post", new_callable=AsyncMock) as mock_post:
        # First call getCities, second call searchSettlements
        mock_post.side_effect = [
            {
                "data": [
                    {
                        "Ref": "db5c88ed-391c-11dd-90d9-001a92567626",
                        "Description": "Кам'янське",
                        "AreaDescription": "Дніпропетровська",
                    }
                ]
            },
            {
                "data": [
                    {
                        "Addresses": [
                            {
                                "DeliveryCity": "db5c88ed-391c-11dd-90d9-001a92567626",
                                "Ref": "e719541a-4b3a-11e4-ab6d-005056801329",
                            }
                        ]
                    }
                ]
            },
        ]

        # Call with mobile keyboard apostrophe U+02BC
        cities = await client.search_city("Камʼянське")

        assert len(cities) == 1
        assert cities[0].description == "Кам'янське"
        assert cities[0].settlement_ref == "e719541a-4b3a-11e4-ab6d-005056801329"

        # Verify FindByString passed to getCities was normalized to ASCII '
        first_call_args = mock_post.call_args_list[0]
        assert first_call_args.kwargs["called_method"] == "getCities"
        assert first_call_args.kwargs["method_properties"]["FindByString"] == "Кам'янське"


@pytest.mark.asyncio
async def test_search_city_fallback_for_missing_apostrophe():
    """Verify search_city tries candidate variants when input is missing an apostrophe."""
    settings = Settings()
    client = NovaPoshtaClient(settings)

    with patch.object(client, "_post", new_callable=AsyncMock) as mock_post:
        # 1. getCities for 'Камянське' -> returns empty
        # 2. getCities for 'Кам'янське' -> returns match
        # 3. searchSettlements for 'Кам'янське' -> returns settlement
        mock_post.side_effect = [
            {"data": []},
            {
                "data": [
                    {
                        "Ref": "db5c88ed-391c-11dd-90d9-001a92567626",
                        "Description": "Кам'янське",
                        "AreaDescription": "Дніпропетровська",
                    }
                ]
            },
            {"data": []},
        ]

        cities = await client.search_city("Камянське")
        assert len(cities) == 1
        assert cities[0].description == "Кам'янське"

        # Ensure first call was with 'Камянське', second call was fallback 'Кам'янське'
        assert mock_post.call_args_list[0].kwargs["method_properties"]["FindByString"] == "Камянське"
        assert mock_post.call_args_list[1].kwargs["method_properties"]["FindByString"] == "Кам'янське"


@pytest.mark.asyncio
async def test_search_street_normalizes_apostrophe():
    """Verify search_street normalizes street name with apostrophe."""
    settings = Settings()
    client = NovaPoshtaClient(settings)

    with patch.object(client, "_post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = {
            "data": [
                {
                    "Ref": "st-123",
                    "Description": "Чорновола В'ячеслава",
                    "StreetsType": "вул.",
                    "CityRef": "city-123",
                }
            ]
        }

        streets = await client.search_street("city-123", "вул. Вʼячеслава Чорновола")
        assert len(streets) == 1
        assert streets[0].description == "Чорновола В'ячеслава"

        # Check that getStreet was queried with normalized query string
        get_street_call = mock_post.call_args_list[0]
        assert get_street_call.kwargs["called_method"] == "getStreet"
        assert "В'ячеслава Чорновола" in get_street_call.kwargs["method_properties"]["FindByString"]


@pytest.mark.asyncio
async def test_handler_process_recipient_text_with_apostrophe(tmp_path):
    """Verify handler correctly processes forwarded message with Unicode apostrophe in city and name."""
    from src.bot.handlers import (
        register_handlers,
        PENDING_SESSIONS,
        USER_ACTIVE_SESSIONS,
    )
    from src.storage import UserSettingsManager
    from src.nova_poshta.models import WarehouseInfo, CityInfo

    storage_file = str(tmp_path / "user_settings.json")
    drafts_file = str(tmp_path / "user_drafts.json")
    storage = UserSettingsManager(filepath=storage_file, drafts_filepath=drafts_file)
    settings = Settings()

    # Create dummy bot & mock message
    bot_mock = AsyncMock()
    status_msg = AsyncMock()
    status_msg.edit_text = AsyncMock()
    status_msg.edit_reply_markup = AsyncMock()

    msg = AsyncMock()
    msg.from_user.id = 999123
    msg.chat.id = 999123
    msg.answer = AsyncMock(return_value=status_msg)
    msg.bot = bot_mock
    msg.text = (
        "Переслано від BARBI\n"
        "НП 6, Камʼянське\n"
        "Плахотній Вадим Леонідович\n"
        "0668272396"
    )

    from unittest.mock import MagicMock

    storage.update_user_settings(
        999123,
        nova_poshta_api_key="test_np_key",
        ai_api_key="test_ai_key",
    )

    register_handlers(
        settings=settings,
        ai_extractor=MagicMock(),
        np_client=MagicMock(),
        storage_manager=storage,
    )

    city_obj = CityInfo(
        Ref="city-kam-1",
        Description="Кам'янське",
        AreaDescription="Дніпропетровська",
    )
    wh_obj = WarehouseInfo(
        Ref="wh-6",
        Description="Відділення №6",
        Number="6",
        TypeOfWarehouse="branch",
        CityRef="city-kam-1",
        CityDescription="Кам'янське",
    )

    with patch("src.nova_poshta.client.NovaPoshtaClient.search_city", new_callable=AsyncMock) as mock_search_city, \
         patch("src.nova_poshta.client.NovaPoshtaClient.get_warehouse", new_callable=AsyncMock) as mock_get_wh, \
         patch("src.ai.extractor.AIExtractor.parse_text", new_callable=AsyncMock) as mock_ai_parse:

        mock_search_city.return_value = [city_obj]
        mock_get_wh.return_value = wh_obj
        mock_ai_parse.return_value = ParsedRecipientInfo(
            is_recipient_info=True,
            last_name="Плахотній",
            first_name="Вадим",
            middle_name="Леонідович",
            phone="0668272396",
            city_name="Камʼянське", # with unicode apostrophe!
            warehouse_number=6,
            is_postomat=False,
        )

        from src.bot.handlers import router

        await router._handle_combined_text_message(msg, msg.text, user_id=999123)

        # Check that search_city was called with normalized city name
        mock_search_city.assert_called_once()
        called_city = mock_search_city.call_args[0][0]
        assert called_city == "Кам'янське"

        # Check session is created without error
        assert 999123 in USER_ACTIVE_SESSIONS
        sess_id = USER_ACTIVE_SESSIONS[999123]
        session = PENDING_SESSIONS[sess_id]
        assert session["city"].description == "Кам'янське"
        assert session["warehouse"].number == "6"
        assert session["parsed_info"].last_name == "Плахотній"

