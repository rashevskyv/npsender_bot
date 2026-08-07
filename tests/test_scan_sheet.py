"""Unit tests for ScanSheet Register management, barcode generation, and draft filtering."""

import datetime
from src.nova_poshta.models import ScanSheetInfo
from src.storage import SavedDraft
from src.utils.barcode_gen import generate_code128_barcode
from src.bot.handlers import filter_user_drafts


def test_scan_sheet_info_model():
    data = {
        "Ref": "abc-123-sheet",
        "Number": "20450999999999",
        "DateTime": "2026-08-07 14:00:00",
        "CountOfDocuments": 3,
    }
    sheet = ScanSheetInfo(**data)
    assert sheet.ref == "abc-123-sheet"
    assert sheet.number == "20450999999999"
    assert sheet.count_of_documents == 3


def test_barcode_generation():
    png_bytes = generate_code128_barcode("20450999999999")
    assert isinstance(png_bytes, bytes)
    assert len(png_bytes) > 500
    # PNG signature header check: \x89PNG
    assert png_bytes[:4] == b"\x89PNG"


def test_filter_user_drafts():
    today_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    yesterday_morning = (datetime.datetime.now() - datetime.timedelta(days=1)).replace(
        hour=9, minute=0, second=0
    ).strftime("%Y-%m-%d %H:%M:%S")
    yesterday_evening = (datetime.datetime.now() - datetime.timedelta(days=1)).replace(
        hour=18, minute=0, second=0
    ).strftime("%Y-%m-%d %H:%M:%S")

    draft1 = SavedDraft(
        ref="ref1",
        int_doc_number="204501",
        recipient_name="Іван Іванов",
        recipient_phone="380971111111",
        city_description="Київ",
        warehouse_description="Відділення №1",
        payer_type="Recipient",
        cargo_description="сувенір",
        declared_value=500.0,
        cost=80.0,
        created_at=today_str,
    )

    draft2 = SavedDraft(
        ref="ref2",
        int_doc_number="204502",
        recipient_name="Петро Петренко",
        recipient_phone="380972222222",
        city_description="Одеса",
        warehouse_description="Поштомат №200",
        payer_type="Recipient",
        cargo_description="планшет",
        declared_value=2000.0,
        cost=100.0,
        created_at=yesterday_morning,
    )

    draft3 = SavedDraft(
        ref="ref3",
        int_doc_number="204503",
        recipient_name="Сидор Сидоренко",
        recipient_phone="380973333333",
        city_description="Дніпро",
        warehouse_description="Відділення №5",
        payer_type="Sender",
        cargo_description="сувенір великий",
        declared_value=1500.0,
        cost=120.0,
        created_at=yesterday_evening,
    )

    all_drafts = [draft1, draft2, draft3]

    # Filter today
    today_filtered = filter_user_drafts(all_drafts, time_period="today")
    assert len(today_filtered) == 1
    assert today_filtered[0].ref == "ref1"

    # Filter yesterday
    yesterday_filtered = filter_user_drafts(all_drafts, time_period="yesterday")
    assert len(yesterday_filtered) == 2

    # Filter yesterday before noon
    yesterday_noon_filtered = filter_user_drafts(all_drafts, time_period="yesterday_before_noon")
    assert len(yesterday_noon_filtered) == 1
    assert yesterday_noon_filtered[0].ref == "ref2"

    # Filter by cargo description query "сувенір"
    cargo_filtered = filter_user_drafts(all_drafts, cargo_query="сувенір")
    assert len(cargo_filtered) == 2
    assert {d.ref for d in cargo_filtered} == {"ref1", "ref3"}
