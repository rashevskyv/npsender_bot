"""Unit tests for AI extractor."""

import pytest
from src.ai.schemas import ParsedRecipientInfo


def test_parsed_recipient_info_full_name():
    info = ParsedRecipientInfo(
        first_name="Іван",
        last_name="Іванов",
        middle_name="Іванович",
        phone="380971234567",
        city_name="Київ",
        warehouse_number=5,
    )
    assert info.full_name == "Іванов Іван Іванович"
    assert info.phone == "380971234567"
    assert info.city_name == "Київ"
    assert info.warehouse_number == 5


def test_parsed_recipient_info_partial():
    info = ParsedRecipientInfo(
        first_name="Петро",
        last_name="Петренко",
    )
    assert info.full_name == "Петренко Петро"
    assert info.middle_name is None
    assert info.is_postomat is False


def test_parsed_recipient_info_conversational():
    info = ParsedRecipientInfo(
        is_recipient_info=False,
        conversational_response="Привіт! Я AI бот для створення ТТН Нової Пошти.",
    )
    assert info.is_recipient_info is False
    assert info.conversational_response == "Привіт! Я AI бот для створення ТТН Нової Пошти."


def test_parsed_recipient_info_contextual():
    prev = ParsedRecipientInfo(
        first_name="Роман",
        last_name="Юрченко",
        phone="380995360818",
        city_name="Київ",
        warehouse_number=26584,
        is_postomat=True,
    )
    # Simulate update with cargo description
    updated = ParsedRecipientInfo(
        **{**prev.model_dump(), "cargo_description": "сувенір"}
    )
    assert updated.full_name == "Юрченко Роман"
    assert updated.cargo_description == "сувенір"
    assert updated.warehouse_number == 26584


def test_parsed_recipient_info_multipart():
    # Message 1 (reposted): Name & Phone only
    msg1_parsed = ParsedRecipientInfo(
        first_name="Роман",
        last_name="Юрченко",
        phone="380995360818",
    )
    assert msg1_parsed.city_name is None
    assert msg1_parsed.warehouse_number is None

    # Message 2 (reposted): City & Postomat
    merged_parsed = ParsedRecipientInfo(
        **{
            **msg1_parsed.model_dump(exclude_none=True),
            "city_name": "Київ",
            "warehouse_number": 26584,
            "is_postomat": True,
        }
    )
    assert merged_parsed.full_name == "Юрченко Роман"
    assert merged_parsed.phone == "380995360818"
    assert merged_parsed.city_name == "Київ"
    assert merged_parsed.warehouse_number == 26584
    assert merged_parsed.is_postomat is True


def test_parsed_recipient_info_null_coercion():
    data = {
        "is_recipient_info": True,
        "is_postomat": None,
        "warehouse_number": "26584",
        "declared_value": "1500 грн",
        "last_name": "Юрченко",
        "first_name": "Роман",
    }
    parsed = ParsedRecipientInfo(**data)
    assert parsed.is_postomat is False
    assert parsed.warehouse_number == 26584
    assert parsed.full_name == "Юрченко Роман"


def test_field_update_preserves_location():
    # Initial full waybill details
    prev = ParsedRecipientInfo(
        first_name="Андрій",
        last_name="Сирбу",
        phone="380933608646",
        city_name="Черкаське",
        warehouse_number=47988,
        is_postomat=True,
        cargo_description="Посилка",
        declared_value=500.0,
    )

    # User sends follow-up update: "Опис вантажу - планшет"
    updated = ParsedRecipientInfo(
        **{
            **prev.model_dump(exclude_none=True),
            "cargo_description": "планшет",
        }
    )

    assert updated.cargo_description == "планшет"
    assert updated.city_name == prev.city_name
    assert updated.warehouse_number == prev.warehouse_number
    assert updated.is_postomat == prev.is_postomat


def test_ai_register_filter_result_model():
    from src.ai.schemas import AIRegisterFilterResult

    res = AIRegisterFilterResult(
        action="create",
        selected_doc_numbers=["20451506611097", "20451506611098"],
        summary="2 накладні",
        explanation="Вибрано всі чернетки",
    )
    assert res.action == "create"
    assert len(res.selected_doc_numbers) == 2
    assert "20451506611097" in res.selected_doc_numbers


@pytest.mark.asyncio
async def test_filter_drafts_for_register_mocked(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    from src.config import Settings
    from src.ai.extractor import AIExtractor

    settings = Settings(
        telegram_bot_token="fake_bot_token",
        nova_poshta_api_key="fake_np_key",
        ai_api_key="fake_ai_key",
    )
    extractor = AIExtractor(settings)

    mock_chat = MagicMock()
    mock_chat.completions.create = AsyncMock()

    mock_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = (
        '{"action": "create", "selected_doc_numbers": ["20451506611097"], '
        '"summary": "1 накладна (Залужна Юлія, Кривий Ріг)", '
        '"explanation": "Вибрано конкретну ТТН"}'
    )
    mock_response.choices = [mock_choice]
    mock_chat.completions.create.return_value = mock_response

    monkeypatch.setattr(extractor, "client", MagicMock(chat=mock_chat))

    drafts = [
        {
            "int_doc_number": "20451506611097",
            "ref": "ref1",
            "recipient_name": "Залужна Юлія",
            "city_description": "Кривий Ріг",
            "warehouse_description": "Відділення №42",
            "cargo_description": "Посилка",
            "declared_value": 15000.0,
            "cod_amount": 15000.0,
            "created_at": "2026-08-08 16:51:27",
        }
    ]

    result = await extractor.filter_drafts_for_register(
        user_prompt="Створи мені реєстр з накладної 20451506611097",
        drafts=drafts,
    )

    assert result.action == "create"
    assert result.selected_doc_numbers == ["20451506611097"]
    assert "Залужна Юлія" in result.summary


@pytest.mark.asyncio
async def test_filter_drafts_for_register_fallback(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    from src.config import Settings
    from src.ai.extractor import AIExtractor

    settings = Settings(
        telegram_bot_token="fake_bot_token",
        nova_poshta_api_key="fake_np_key",
        ai_api_key="fake_ai_key",
    )
    extractor = AIExtractor(settings)

    mock_chat = MagicMock()
    # Simulate AI failure
    mock_chat.completions.create = AsyncMock(side_effect=RuntimeError("AI network error"))
    monkeypatch.setattr(extractor, "client", MagicMock(chat=mock_chat))

    drafts = [
        {"int_doc_number": "204501"},
        {"int_doc_number": "204502"},
    ]

    result = await extractor.filter_drafts_for_register(
        user_prompt="Створи мені реєстр з усіх чернеток",
        drafts=drafts,
    )

    assert result.action == "create"
    assert result.selected_doc_numbers == ["204501", "204502"]
