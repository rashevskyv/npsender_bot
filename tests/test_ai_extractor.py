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
