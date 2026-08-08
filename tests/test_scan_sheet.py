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


def test_is_recent_scansheet():
    from src.bot.handlers import _is_recent_scansheet
    now = datetime.datetime.now()

    today_str = now.strftime("%Y-%m-%d %H:%M:%S")
    yesterday_str = (now - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    three_days_ago_str = (now - datetime.timedelta(days=3)).strftime("%Y-%m-%d %H:%M:%S")

    assert _is_recent_scansheet(today_str, max_days=2) is True
    assert _is_recent_scansheet(yesterday_str, max_days=2) is True
    assert _is_recent_scansheet(three_days_ago_str, max_days=2) is False


def test_purge_old_or_sent_scansheets(tmp_path):
    import os
    from src.storage import UserSettingsManager, SavedScanSheet

    storage_file = os.path.join(tmp_path, "user_settings.json")
    drafts_file = os.path.join(tmp_path, "user_drafts.json")
    scansheets_file = os.path.join(tmp_path, "user_scansheets.json")
    manager = UserSettingsManager(
        filepath=storage_file,
        drafts_filepath=drafts_file,
        scansheets_filepath=scansheets_file,
    )

    s1 = SavedScanSheet(
        ref="sheet-ref-1",
        number="20450111",
        date_created="2026-08-08 10:00:00",
        count_of_documents=2,
        document_numbers=["204501", "204502"],
    )
    s2 = SavedScanSheet(
        ref="sheet-ref-2",
        number="20450222",
        date_created="2026-08-05 10:00:00",
        count_of_documents=1,
        document_numbers=["204503"],
    )

    manager.add_user_scansheet(555, s1)
    manager.add_user_scansheet(555, s2)
    assert len(manager.get_user_scansheets(555)) == 2

    # Purge s2 by ref
    purged = manager.purge_old_or_sent_scansheets(555, ["sheet-ref-2"])
    assert purged == 1
    remaining = manager.get_user_scansheets(555)
    assert len(remaining) == 1
    assert remaining[0].ref == "sheet-ref-1"

