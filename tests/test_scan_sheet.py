"""Unit tests for ScanSheet Register management, barcode generation, and draft filtering."""

import datetime
import pytest
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

    # Test hyphen preservation for registers like 105-79184007
    png_bytes_hyphen = generate_code128_barcode("105-79184007")
    assert isinstance(png_bytes_hyphen, bytes)
    assert len(png_bytes_hyphen) > 500
    assert png_bytes_hyphen[:4] == b"\x89PNG"


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


@pytest.mark.asyncio
async def test_fetch_user_active_drafts_combines_and_filters(tmp_path):
    import os
    import pytest
    from unittest.mock import AsyncMock, MagicMock
    from src.storage import UserSettingsManager, SavedDraft
    from src.nova_poshta.models import WaybillItemInfo
    from src.bot.handlers import fetch_user_active_drafts

    storage_file = os.path.join(tmp_path, "user_settings.json")
    drafts_file = os.path.join(tmp_path, "user_drafts.json")
    scansheets_file = os.path.join(tmp_path, "user_scansheets.json")
    manager = UserSettingsManager(
        filepath=storage_file,
        drafts_filepath=drafts_file,
        scansheets_filepath=scansheets_file,
    )

    # Local draft 1: active
    local_draft1 = SavedDraft(
        ref="local-ref-1",
        int_doc_number="20451506611097",
        recipient_name="Залужна Юлія",
        recipient_phone="380675641704",
        city_description="Кривий Ріг",
        warehouse_description="Відділення №42",
        payer_type="Recipient",
        cargo_description="Посилка",
        declared_value=15000.0,
        cost=90.0,
        created_at="2026-08-08 16:51:27",
    )
    # Local draft 2: deleted on NP
    local_draft2 = SavedDraft(
        ref="local-ref-2",
        int_doc_number="20451506125831",
        recipient_name="Ковальчук Р О",
        recipient_phone="380631344371",
        city_description="Житомир",
        warehouse_description="Відділення №19",
        payer_type="Recipient",
        cargo_description="Сувенір",
        declared_value=500.0,
        cost=90.0,
        created_at="07.08.2026",
    )
    manager.add_user_draft(12345, local_draft1)
    manager.add_user_draft(12345, local_draft2)

    # Mock NP client
    mock_np_client = MagicMock()
    # NP server returns 1 live draft (same number) + 1 another live draft (already shipped)
    mock_np_client.get_internet_document_list = AsyncMock(return_value=[
        WaybillItemInfo(
            int_doc_number="20451506611097",
            ref="np-ref-1",
            state_name="Чернетка",
            recipient_name="Залужна Юлія",
            recipient_phone="380675641704",
            city_recipient="Кривий Ріг",
            address_recipient="Відділення №42",
            cost=90.0,
            declared_value=15000.0,
            cod_amount=15000.0,
            cod_payment_type="card",
            payer_type="Recipient",
            description="Посилка",
        ),
        WaybillItemInfo(
            int_doc_number="20451506619999",
            ref="np-ref-shipped",
            state_name="Прямує до міста",
            recipient_name="Іван Іванов",
            city_recipient="Київ",
            address_recipient="Відділення №1",
            cost=100.0,
            description="Вже їде",
        )
    ])

    mock_np_client.get_documents_status = AsyncMock(return_value={
        "20451506611097": {"is_shipped": False, "is_deleted": False, "is_draft": True, "status": "Чернетка"},
        "20451506619999": {"is_shipped": True, "is_deleted": False, "is_draft": False, "status": "У дорозі"},
        "20451506125831": {"is_shipped": True, "is_deleted": False, "is_draft": False, "status": "Відмова від отримання"},
    })

    active = await fetch_user_active_drafts(12345, mock_np_client, manager)
    assert len(active) == 1
    assert active[0]["int_doc_number"] == "20451506611097"
    assert active[0]["recipient_name"] == "Залужна Юлія"
    assert active[0]["city_description"] == "Кривий Ріг"

    # Verify that deleted draft was purged from storage manager
    saved_drafts = manager.get_user_drafts(12345)
    assert len(saved_drafts) == 1
    assert saved_drafts[0].int_doc_number == "20451506611097"


@pytest.mark.asyncio
async def test_process_register_callback_deletes_caption_or_text(tmp_path):
    import os
    from unittest.mock import AsyncMock, MagicMock, patch
    from src.config import Settings
    from src.storage import UserSettingsManager, SavedScanSheet
    from src.bot.keyboards import RegisterActionCallback
    from src.bot.handlers import register_handlers
    from aiogram import Router

    storage_file = os.path.join(tmp_path, "user_settings.json")
    drafts_file = os.path.join(tmp_path, "user_drafts.json")
    scansheets_file = os.path.join(tmp_path, "user_scansheets.json")
    manager = UserSettingsManager(
        filepath=storage_file,
        drafts_filepath=drafts_file,
        scansheets_filepath=scansheets_file,
    )

    s1 = SavedScanSheet(
        ref="sheet-to-delete",
        number="105-79184007",
        date_created="2026-08-09 17:20:00",
        count_of_documents=1,
        document_numbers=["20451506611097"],
    )
    manager.add_user_scansheet(999, s1)

    # Mock callback with photo/caption
    mock_callback = MagicMock()
    mock_callback.from_user.id = 999
    mock_callback.answer = AsyncMock()
    mock_callback.message.photo = [MagicMock()]
    mock_callback.message.caption = "Some caption"
    mock_callback.message.edit_caption = AsyncMock()
    mock_callback.message.edit_text = AsyncMock()

    # Register handlers with this manager
    register_handlers(
        settings=Settings(TELEGRAM_BOT_TOKEN="dummy"),
        ai_extractor=MagicMock(),
        np_client=MagicMock(),
        storage_manager=manager,
    )

    # Call delete handler directly or through simulated logic
    from src.bot.handlers import router
    matching_handlers = [h.callback for h in router.callback_query.handlers if "process_register_callback" in str(h.callback)]
    handler = matching_handlers[-1] if matching_handlers else None

    callback_data = RegisterActionCallback(action="delete", ref="sheet-to-delete")

    if handler:
        with patch("src.nova_poshta.client.NovaPoshtaClient.delete_scan_sheet", new_callable=AsyncMock) as mock_delete_ss:
            mock_delete_ss.return_value = True
            await handler(mock_callback, callback_data)
            mock_callback.message.edit_caption.assert_called_once()
            mock_delete_ss.assert_called_once_with("sheet-to-delete")
            assert len(manager.get_user_scansheets(999)) == 0


@pytest.mark.asyncio
async def test_process_draft_callback_barcode(tmp_path):
    import os
    from unittest.mock import AsyncMock, MagicMock
    from src.config import Settings
    from src.storage import UserSettingsManager, SavedDraft
    from src.bot.keyboards import DraftActionCallback
    from src.bot.handlers import register_handlers, router

    storage_file = os.path.join(tmp_path, "user_settings.json")
    drafts_file = os.path.join(tmp_path, "user_drafts.json")
    scansheets_file = os.path.join(tmp_path, "user_scansheets.json")
    manager = UserSettingsManager(
        filepath=storage_file,
        drafts_filepath=drafts_file,
        scansheets_filepath=scansheets_file,
    )

    d1 = SavedDraft(
        ref="ref-barcode-test",
        int_doc_number="20451512966104",
        recipient_name="Кузло Антон",
        recipient_phone="380986026819",
        city_description="Рівне",
        warehouse_description="Відділення 13",
        payer_type="Recipient",
        cargo_description="Приставка",
        declared_value=8000.0,
        cost=90.0,
        created_at="2026-08-17 11:30:10",
    )
    manager.add_user_draft(111, d1)

    mock_callback = MagicMock()
    mock_callback.from_user.id = 111
    mock_callback.answer = AsyncMock()
    mock_callback.message.reply_photo = AsyncMock()

    register_handlers(
        settings=Settings(TELEGRAM_BOT_TOKEN="dummy"),
        ai_extractor=MagicMock(),
        np_client=MagicMock(),
        storage_manager=manager,
    )

    matching_handlers = [h.callback for h in router.callback_query.handlers if "process_draft_callback" in str(h.callback)]
    handler = matching_handlers[-1] if matching_handlers else None
    assert handler is not None

    callback_data = DraftActionCallback(action="barcode", ref="20451512966104")
    await handler(mock_callback, callback_data)

    mock_callback.message.reply_photo.assert_called_once()
    call_args = mock_callback.message.reply_photo.call_args
    assert "20451512966104" in call_args.kwargs["caption"]


def test_barcode_generation_empty_data_raises_value_error():
    """Verify that empty, whitespace or None barcode input raises ValueError rather than IndexError."""
    with pytest.raises(ValueError, match="Barcode data cannot be empty"):
        generate_code128_barcode("")

    with pytest.raises(ValueError, match="Barcode data cannot be empty"):
        generate_code128_barcode("   ")

    with pytest.raises(ValueError, match="Barcode data cannot be empty"):
        generate_code128_barcode(None)


def test_ai_register_filter_result_parsing():
    """Verify AIRegisterFilterResult schema parses target_item_index and actions."""
    from src.ai.schemas import AIRegisterFilterResult

    res = AIRegisterFilterResult(
        action="remove_waybill",
        target_item_index="№2",
        target_doc_number="20451531036290",
        target_recipient="Кожин",
    )
    assert res.action == "remove_waybill"
    assert res.target_item_index == 2
    assert res.target_doc_number == "20451531036290"
    assert res.target_recipient == "Кожин"


@pytest.mark.asyncio
async def test_filter_drafts_fallback_remove_waybill():
    """Verify programmatic fallback in AIExtractor identifies removal requests."""
    from src.ai.extractor import AIExtractor
    from src.config import Settings
    from unittest.mock import AsyncMock, MagicMock

    settings = Settings(TELEGRAM_BOT_TOKEN="dummy", AI_API_KEY="dummy")
    extractor = AIExtractor(settings)

    # Mock client to throw exception and force fallback
    extractor.client = MagicMock()
    extractor.client.chat.completions.create = AsyncMock(side_effect=RuntimeError("AI Down"))

    drafts = [{"int_doc_number": "204501"}, {"int_doc_number": "204502"}]

    # Test #1: ordinal index №2
    res = await extractor.filter_drafts_for_register("Прибери, будь ласка, з реєстру накладну №2.", drafts)
    assert res.action == "remove_waybill"
    assert res.target_item_index == 2

    # Test #2: word "другу"
    res2 = await extractor.filter_drafts_for_register("Видали другу накладну з реєстру", drafts)
    assert res2.action == "remove_waybill"
    assert res2.target_item_index == 2

    # Test #3: TTN number
    res3 = await extractor.filter_drafts_for_register("Прибери 20451531036290 з реєстру", drafts)
    assert res3.action == "remove_waybill"
    assert res3.target_doc_number == "20451531036290"

    # Test #4: delete register
    res4 = await extractor.filter_drafts_for_register("Видали цей реєстр", drafts)
    assert res4.action == "delete_register"


@pytest.mark.asyncio
async def test_create_scan_sheet_validation_and_remove_documents():
    """Verify create_scan_sheet raises error on empty number and remove_documents_from_scan_sheet works."""
    from src.nova_poshta.client import NovaPoshtaClient
    from src.config import Settings
    from unittest.mock import AsyncMock

    client = NovaPoshtaClient(Settings(NOVA_POSHTA_API_KEY="test_key"))

    # When NP returns empty number / errors
    client._post = AsyncMock(return_value={
        "success": True,
        "data": [{
            "Ref": "",
            "Number": "",
            "Errors": [{"Number": "20451531219131", "Error": "Накладна вже у реєстрі"}]
        }]
    })

    with pytest.raises(RuntimeError, match="Помилка створення реєстру"):
        await client.create_scan_sheet(["doc-ref-1"])

    # When NP succeeds with valid scan sheet
    client._post = AsyncMock(return_value={
        "success": True,
        "data": [{
            "Ref": "new-sheet-ref",
            "Number": "105-88899900",
            "CountOfDocuments": 2,
            "DateTime": "2026-09-08 19:30:00"
        }]
    })
    sheet = await client.create_scan_sheet(["doc-ref-1", "doc-ref-2"])
    assert sheet.number == "105-88899900"
    assert sheet.ref == "new-sheet-ref"
    assert sheet.count_of_documents == 2

    # Test remove_documents_from_scan_sheet
    client._post = AsyncMock(return_value={"success": True, "data": []})
    ok = await client.remove_documents_from_scan_sheet(["doc-ref-2"])
    assert ok is True
    client._post.assert_called_once_with(
        model_name="ScanSheet",
        called_method="removeDocuments",
        method_properties={"DocumentRefs": ["doc-ref-2"]},
    )


def test_storage_remove_document_from_user_scansheet(tmp_path):
    """Verify storage_manager updates document numbers and count when removing a doc from scansheet."""
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
        ref="sheet-ref-abc",
        number="105-80149920",
        date_created="2026-09-08 19:13:47",
        count_of_documents=3,
        document_numbers=["20451531219131", "20451531036290", "20451531340827"],
    )
    manager.add_user_scansheet(777, s1)

    updated = manager.remove_document_from_user_scansheet(777, "105-80149920", "20451531036290")
    assert updated is not None
    assert updated.count_of_documents == 2
    assert updated.document_numbers == ["20451531219131", "20451531340827"]

    # Verify persisted
    saved_list = manager.get_user_scansheets(777)
    assert len(saved_list) == 1
    assert saved_list[0].count_of_documents == 2
    assert "20451531036290" not in saved_list[0].document_numbers


@pytest.mark.asyncio
async def test_handle_text_remove_waybill_from_register(tmp_path):
    """Verify end-to-end processing of user text 'Прибери, будь ласка, з реєстру накладну №2.'"""
    import os
    from unittest.mock import AsyncMock, MagicMock
    from src.config import Settings
    from src.storage import UserSettingsManager, SavedScanSheet
    from src.ai.schemas import ParsedRecipientInfo, AIRegisterFilterResult
    from src.bot.handlers import router, register_handlers, USER_LAST_SCANSHEET_CONTEXT

    storage_file = os.path.join(tmp_path, "user_settings.json")
    drafts_file = os.path.join(tmp_path, "user_drafts.json")
    scansheets_file = os.path.join(tmp_path, "user_scansheets.json")
    manager = UserSettingsManager(
        filepath=storage_file,
        drafts_filepath=drafts_file,
        scansheets_filepath=scansheets_file,
    )

    manager.update_user_settings(888, nova_poshta_api_key="np_key_888", ai_api_key="ai_key_888")

    s1 = SavedScanSheet(
        ref="sheet-ref-123",
        number="105-80149920",
        date_created="2026-09-08 19:13:47",
        count_of_documents=3,
        document_numbers=["20451531219131", "20451531036290", "20451531340827"],
    )
    manager.add_user_scansheet(888, s1)

    # Set up user context
    USER_LAST_SCANSHEET_CONTEXT[888] = {
        "ref": "sheet-ref-123",
        "number": "105-80149920",
        "date_created": "2026-09-08 19:13:47",
        "count_of_documents": 3,
        "items": [
            {
                "index": 1,
                "int_doc_number": "20451531219131",
                "ref": "ref-1",
                "recipient_name": "Згирча Юлія Віорелівна",
                "city_description": "Тернопіль",
                "warehouse_description": "Відділення №7",
                "cargo_description": "Посилка",
                "declared_value": 500.0,
                "cod_amount": 0.0,
            },
            {
                "index": 2,
                "int_doc_number": "20451531036290",
                "ref": "ref-2",
                "recipient_name": "Кожин Олександр Миколайович",
                "city_description": "Кам'янське",
                "warehouse_description": "Відділення №14",
                "cargo_description": "Планшет",
                "declared_value": 10100.0,
                "cod_amount": 10100.0,
            },
            {
                "index": 3,
                "int_doc_number": "20451531340827",
                "ref": "ref-3",
                "recipient_name": "Верич Костянтин Миколайович",
                "city_description": "Запоріжжя",
                "warehouse_description": "Поштомат №40400",
                "cargo_description": "3DS",
                "declared_value": 5000.0,
                "cod_amount": 5000.0,
            },
        ],
    }

    from unittest.mock import patch
    with patch("src.ai.extractor.AIExtractor.parse_text", new_callable=AsyncMock) as mock_parse_text, \
         patch("src.ai.extractor.AIExtractor.filter_drafts_for_register", new_callable=AsyncMock) as mock_filter_drafts, \
         patch("src.nova_poshta.client.NovaPoshtaClient.remove_documents_from_scan_sheet", new_callable=AsyncMock) as mock_remove_docs, \
         patch("src.nova_poshta.client.NovaPoshtaClient.get_internet_document_list", new_callable=AsyncMock) as mock_get_docs:

        mock_parse_text.return_value = ParsedRecipientInfo(
            is_recipient_info=False,
            is_register_intent=True,
            register_action="remove_waybill",
        )
        mock_filter_drafts.return_value = AIRegisterFilterResult(
            action="remove_waybill",
            target_item_index=2,
            target_doc_number="20451531036290",
            target_recipient="Кожин Олександр",
        )
        mock_remove_docs.return_value = True
        mock_get_docs.return_value = []

        settings = Settings(
            TELEGRAM_BOT_TOKEN="dummy",
            SENDER_REF="sender_ref_test",
            SENDER_CONTACT_REF="contact_ref_test",
            SENDER_PHONE="380991234567",
        )
        register_handlers(settings, MagicMock(), MagicMock(), manager)

        # Mock Telegram message
        mock_msg = MagicMock()
        mock_msg.from_user.id = 888
        mock_status = MagicMock()
        mock_status.edit_text = AsyncMock()
        mock_status.delete = AsyncMock()
        mock_msg.answer = AsyncMock(return_value=mock_status)
        mock_msg.answer_photo = AsyncMock()

        await router._handle_combined_text_message(mock_msg, "Прибери, будь ласка, з реєстру накладну №2.", user_id=888)

        # Verify remove_documents_from_scan_sheet called with 20451531036290 and scan_sheet_ref
        mock_remove_docs.assert_called_once()
        rem_call_args = mock_remove_docs.call_args[0][0]
        assert "20451531036290" in rem_call_args or "ref-2" in rem_call_args
        assert mock_remove_docs.call_args.kwargs.get("scan_sheet_ref") == "sheet-ref-123"

    # Verify storage updated
    saved_after = manager.get_user_scansheets(888)
    assert len(saved_after) == 1
    assert saved_after[0].count_of_documents == 2
    assert "20451531036290" not in saved_after[0].document_numbers

    # Verify answer_photo called with updated barcode for register 105-80149920 and caption
    mock_msg.answer_photo.assert_called_once()
    photo_caption = mock_msg.answer_photo.call_args.kwargs["caption"]
    assert "20451531036290" in photo_caption
    assert "успішно вилучено з реєстру" in photo_caption
    assert "105-80149920" in photo_caption
    assert "2" in photo_caption
    assert "20451531219131" in photo_caption
    assert "20451531340827" in photo_caption


def test_storage_scansheet_validation_and_cleanup(tmp_path):
    import os
    import json
    from src.storage import UserSettingsManager, SavedScanSheet

    scansheets_file = os.path.join(tmp_path, "user_scansheets.json")
    # Prepopulate with 1 corrupt entry (empty ref/number) and 1 valid entry
    with open(scansheets_file, "w", encoding="utf-8") as f:
        json.dump({
            "999": [
                {"ref": "", "number": "", "date_created": "2026-09-08 19:14:38", "count_of_documents": 1, "document_numbers": ["20451531219131"]},
                {"ref": "reg-valid-1", "number": "105-80149920", "date_created": "2026-09-08 19:13:47", "count_of_documents": 3, "document_numbers": ["20451531219131", "20451531036290", "20451531340827"]}
            ]
        }, f)

    manager = UserSettingsManager(
        filepath=os.path.join(tmp_path, "s.json"),
        drafts_filepath=os.path.join(tmp_path, "d.json"),
        scansheets_filepath=scansheets_file,
    )

    # Corrupt entry should be filtered out on load
    sheets = manager.get_user_scansheets(999)
    assert len(sheets) == 1
    assert sheets[0].number == "105-80149920"

    # Attempting to add invalid scansheet should be ignored
    manager.add_user_scansheet(999, SavedScanSheet(ref="", number="", date_created="now", count_of_documents=0))
    sheets_after_add = manager.get_user_scansheets(999)
    assert len(sheets_after_add) == 1


@pytest.mark.asyncio
async def test_remove_waybill_recovers_from_storage_when_context_empty(tmp_path):
    """Test that when USER_LAST_SCANSHEET_CONTEXT is empty, bot recovers from valid storage register and removes item #2."""
    import os
    from src.storage import UserSettingsManager, SavedScanSheet
    from src.bot.handlers import register_handlers, router, USER_LAST_SCANSHEET_CONTEXT
    from src.config import Settings
    from src.ai.schemas import ParsedRecipientInfo, AIRegisterFilterResult
    from unittest.mock import patch, AsyncMock, MagicMock

    USER_LAST_SCANSHEET_CONTEXT.clear()

    storage_file = os.path.join(tmp_path, "user_settings.json")
    drafts_file = os.path.join(tmp_path, "user_drafts.json")
    scansheets_file = os.path.join(tmp_path, "user_scansheets.json")
    manager = UserSettingsManager(
        filepath=storage_file,
        drafts_filepath=drafts_file,
        scansheets_filepath=scansheets_file,
    )

    # Add valid register with 3 documents
    valid_sheet = SavedScanSheet(
        ref="reg-ref-xyz",
        number="105-80149920",
        date_created="2026-09-08 19:13:47",
        count_of_documents=3,
        document_numbers=["20451531219131", "20451531036290", "20451531340827"],
    )
    manager.update_user_settings(777, nova_poshta_api_key="np_key_777", ai_api_key="ai_key_777")
    manager.add_user_scansheet(777, valid_sheet)

    with patch("src.ai.extractor.AIExtractor.parse_text", new_callable=AsyncMock) as mock_parse_text, \
         patch("src.ai.extractor.AIExtractor.filter_drafts_for_register", new_callable=AsyncMock) as mock_filter_drafts, \
         patch("src.nova_poshta.client.NovaPoshtaClient.remove_documents_from_scan_sheet", new_callable=AsyncMock) as mock_remove_docs, \
         patch("src.nova_poshta.client.NovaPoshtaClient.get_internet_document_list", new_callable=AsyncMock) as mock_get_docs, \
         patch("src.nova_poshta.client.NovaPoshtaClient.get_scan_sheet_documents", new_callable=AsyncMock) as mock_get_ss_docs:

        mock_parse_text.return_value = ParsedRecipientInfo(
            is_recipient_info=False,
            is_register_intent=True,
            register_action="remove_waybill",
        )
        mock_filter_drafts.return_value = AIRegisterFilterResult(
            action="remove_waybill",
            target_item_index=2,
        )
        mock_remove_docs.return_value = True
        mock_get_docs.return_value = []
        mock_get_ss_docs.return_value = [
            {"Number": "20451531219131", "Ref": "doc-guid-1"},
            {"Number": "20451531036290", "Ref": "doc-guid-2"},
            {"Number": "20451531340827", "Ref": "doc-guid-3"},
        ]

        settings = Settings(
            TELEGRAM_BOT_TOKEN="dummy",
            SENDER_REF="sender_ref_test",
            SENDER_CONTACT_REF="contact_ref_test",
            SENDER_PHONE="380991234567",
        )
        register_handlers(settings, MagicMock(), MagicMock(), manager)

        mock_msg = MagicMock()
        mock_msg.from_user.id = 777
        mock_status = MagicMock()
        mock_status.edit_text = AsyncMock()
        mock_status.delete = AsyncMock()
        mock_msg.answer = AsyncMock(return_value=mock_status)
        mock_msg.answer_photo = AsyncMock()

        await router._handle_combined_text_message(mock_msg, "Прибери, будь ласка, з реєстру накладну №2.", user_id=777)

        # Verify remove_documents_from_scan_sheet called with 2nd document ("20451531036290" / "doc-guid-2") and scan_sheet_ref="reg-ref-xyz"
        mock_remove_docs.assert_called_once()
        rem_args = mock_remove_docs.call_args[0][0]
        assert "20451531036290" in rem_args or "doc-guid-2" in rem_args
        assert mock_remove_docs.call_args.kwargs.get("scan_sheet_ref") == "reg-ref-xyz"

        # Verify storage updated: 3 -> 2 documents, 20451531036290 removed
        saved = manager.get_user_scansheets(777)
        assert len(saved) == 1
        assert saved[0].count_of_documents == 2
        assert saved[0].document_numbers == ["20451531219131", "20451531340827"]

        # Verify answer_photo sent with 2 remaining waybills
        mock_msg.answer_photo.assert_called_once()
        cap = mock_msg.answer_photo.call_args.kwargs["caption"]
        assert "Залишилось накладних:* 2" in cap
        assert "20451531219131" in cap
        assert "20451531340827" in cap
        assert "20451531036290" in cap  # mentioned as removed


@pytest.mark.asyncio
async def test_scan_sheet_info_populated_by_name_and_lists():
    info = ScanSheetInfo(
        Ref="ref-123",
        Number="105-80150900",
        DateTime="2026-09-08 19:52:45",
        CountOfDocuments=1,
        success_documents=["20451531340827"],
        error_documents=[{
            "number": "20451531036290",
            "error": "Документ уже знаходиться у реєстрі",
            "scansheet_number": "105-80149920",
        }],
    )
    assert info.ref == "ref-123"
    assert info.number == "105-80150900"
    assert info.count_of_documents == 1
    assert info.success_documents == ["20451531340827"]
    assert len(info.error_documents) == 1
    assert info.error_documents[0]["scansheet_number"] == "105-80149920"

    # Also test instantiation with field names (ref, number) thanks to ConfigDict(populate_by_name=True)
    info2 = ScanSheetInfo(
        ref="ref-456",
        number="105-99999999",
        count_of_documents=2,
    )
    assert info2.ref == "ref-456"
    assert info2.number == "105-99999999"
    assert info2.count_of_documents == 2


@pytest.mark.asyncio
async def test_create_scan_sheet_client_partial_success_with_warnings():
    from src.config import Settings
    from src.nova_poshta.client import NovaPoshtaClient
    from unittest.mock import patch, AsyncMock

    settings = Settings(
        TELEGRAM_BOT_TOKEN="dummy",
        NOVA_POSHTA_API_KEY="test_np_key",
    )
    client = NovaPoshtaClient(settings)

    mock_post_res = {
        "success": True,
        "data": [
            {
                "Ref": "new-scansheet-ref",
                "Number": "105-80150900",
                "DateTime": "2026-09-08 19:52:45",
                "Errors": [],
                "Success": [],
                "Warnings": [],
                "Data": {
                    "Success": [
                        {"Ref": "doc-ref-1", "Number": "20451531340827"}
                    ],
                    "Errors": [],
                    "Warnings": [
                        {
                            "Ref": "doc-ref-2",
                            "Number": "20451531036290",
                            "Warning": "Документ уже знаходиться у реєстрі",
                            "ScanSheetNumber": "105-80149920",
                        }
                    ],
                },
            }
        ],
        "errors": [],
    }

    with patch.object(client, "_post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_post_res

        res = await client.create_scan_sheet(["doc-ref-1", "doc-ref-2"])
        assert res.ref == "new-scansheet-ref"
        assert res.number == "105-80150900"
        # Crucial check: count must be 1, NOT 2!
        assert res.count_of_documents == 1
        assert res.success_documents == ["20451531340827"]
        assert len(res.error_documents) == 1
        assert res.error_documents[0]["number"] == "20451531036290"
        assert res.error_documents[0]["scansheet_number"] == "105-80149920"


@pytest.mark.asyncio
async def test_create_scan_sheet_client_complete_failure_raises_informative_error():
    from src.config import Settings
    from src.nova_poshta.client import NovaPoshtaClient
    from unittest.mock import patch, AsyncMock

    settings = Settings(
        TELEGRAM_BOT_TOKEN="dummy",
        NOVA_POSHTA_API_KEY="test_np_key",
    )
    client = NovaPoshtaClient(settings)

    mock_post_res = {
        "success": True,
        "data": [
            {
                "Ref": "",
                "Number": "",
                "DateTime": "2026-09-08 19:52:45",
                "Errors": [],
                "Success": [],
                "Warnings": [],
                "Data": {
                    "Success": [],
                    "Errors": [],
                    "Warnings": [
                        {
                            "Ref": "doc-ref-1",
                            "Number": "20451531340827",
                            "Warning": "Документ уже знаходиться у реєстрі",
                            "ScanSheetNumber": "105-80150900",
                        }
                    ],
                },
            }
        ],
        "errors": [],
    }

    with patch.object(client, "_post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_post_res

        with pytest.raises(RuntimeError) as exc_info:
            await client.create_scan_sheet(["doc-ref-1"])

        err_msg = str(exc_info.value)
        assert "20451531340827" in err_msg
        assert "105-80150900" in err_msg
        assert "знаходиться у реєстрі" in err_msg


@pytest.mark.asyncio
async def test_handler_create_scansheet_partial_success_shows_correct_count_and_warnings(tmp_path):
    import os
    from src.bot.handlers import register_handlers, router, USER_LAST_SCANSHEET_CONTEXT
    from src.storage import UserSettingsManager, SavedDraft
    from src.config import Settings
    from src.ai.schemas import ParsedRecipientInfo, AIRegisterFilterResult
    from src.nova_poshta.models import ScanSheetInfo
    from unittest.mock import patch, AsyncMock, MagicMock

    USER_LAST_SCANSHEET_CONTEXT.clear()

    storage_file = os.path.join(tmp_path, "user_settings.json")
    drafts_file = os.path.join(tmp_path, "user_drafts.json")
    scansheets_file = os.path.join(tmp_path, "user_scansheets.json")
    manager = UserSettingsManager(
        filepath=storage_file,
        drafts_filepath=drafts_file,
        scansheets_filepath=scansheets_file,
    )

    manager.update_user_settings(888, nova_poshta_api_key="np_key_888", ai_api_key="ai_key_888")

    # Add 2 drafts in storage
    d1 = SavedDraft(
        ref="ref-ttn-1",
        int_doc_number="20451531340827",
        recipient_name="Верич Костянтин",
        recipient_phone="380991111111",
        city_description="Київ",
        warehouse_description="Відділення №1",
        payer_type="Recipient",
        cargo_description="сувенір",
        declared_value=500.0,
        cost=80.0,
        created_at="2026-09-08 17:43:37",
    )
    d2 = SavedDraft(
        ref="ref-ttn-2",
        int_doc_number="20451531036290",
        recipient_name="Кожин Олександр",
        recipient_phone="380992222222",
        city_description="Львів",
        warehouse_description="Відділення №5",
        payer_type="Recipient",
        cargo_description="документи",
        declared_value=300.0,
        cost=60.0,
        created_at="2026-09-08 14:29:50",
    )
    manager.add_user_draft(888, d1)
    manager.add_user_draft(888, d2)

    settings = Settings(
        TELEGRAM_BOT_TOKEN="dummy",
        SENDER_REF="sender_ref_test",
        SENDER_CONTACT_REF="contact_ref_test",
        SENDER_PHONE="380991234567",
    )
    register_handlers(settings, MagicMock(), MagicMock(), manager)

    with patch("src.ai.extractor.AIExtractor.parse_text", new_callable=AsyncMock) as mock_parse_text, \
         patch("src.ai.extractor.AIExtractor.filter_drafts_for_register", new_callable=AsyncMock) as mock_filter_drafts, \
         patch("src.nova_poshta.client.NovaPoshtaClient.create_scan_sheet", new_callable=AsyncMock) as mock_create_ss, \
         patch("src.nova_poshta.client.NovaPoshtaClient.get_internet_document_list", new_callable=AsyncMock) as mock_get_docs, \
         patch("src.nova_poshta.client.NovaPoshtaClient.get_documents_status", new_callable=AsyncMock) as mock_get_statuses:

        mock_parse_text.return_value = ParsedRecipientInfo(
            is_recipient_info=False,
            is_register_intent=True,
            register_action="create",
        )
        mock_filter_drafts.return_value = AIRegisterFilterResult(
            action="create",
            selected_doc_numbers=["20451531340827", "20451531036290"],
            summary="2 накладних",
        )
        # Simulate partial success from API: only 20451531340827 was added; 20451531036290 was rejected (already in 105-80149920)
        mock_create_ss.return_value = ScanSheetInfo(
            Ref="new-reg-ref-888",
            Number="105-80150900",
            DateTime="2026-09-08 19:52:45",
            CountOfDocuments=1,
            success_documents=["20451531340827"],
            error_documents=[{
                "number": "20451531036290",
                "error": "Документ уже знаходиться у реєстрі",
                "scansheet_number": "105-80149920",
            }],
        )
        mock_get_docs.return_value = []
        mock_get_statuses.return_value = {
            "20451531340827": {"is_draft": True},
            "20451531036290": {"is_draft": True},
        }

        mock_msg = MagicMock()
        mock_msg.from_user.id = 888
        mock_status = MagicMock()
        mock_status.edit_text = AsyncMock()
        mock_status.delete = AsyncMock()
        mock_msg.answer = AsyncMock(return_value=mock_status)
        mock_msg.answer_photo = AsyncMock()

        await router._handle_combined_text_message(mock_msg, "Створи реєстр з усіх чернеток.", user_id=888)

        # Verify saved scansheet has count=1 and only the succeeded doc
        saved_sheets = manager.get_user_scansheets(888)
        assert len(saved_sheets) == 1
        assert saved_sheets[0].number == "105-80150900"
        assert saved_sheets[0].count_of_documents == 1
        assert saved_sheets[0].document_numbers == ["20451531340827"]

        # Verify USER_LAST_SCANSHEET_CONTEXT has count=1 and 1 item
        ctx = USER_LAST_SCANSHEET_CONTEXT.get(888)
        assert ctx is not None
        assert ctx["count_of_documents"] == 1
        assert len(ctx["items"]) == 1
        assert ctx["items"][0]["int_doc_number"] == "20451531340827"

        # Verify draft was updated with scansheet number in storage
        user_drafts = manager.get_user_drafts(888)
        d1_updated = next(d for d in user_drafts if d.int_doc_number == "20451531340827")
        assert d1_updated.scan_sheet_number == "105-80150900"

        # Verify message photo caption
        mock_msg.answer_photo.assert_called_once()
        caption = mock_msg.answer_photo.call_args.kwargs["caption"]
        assert "Кількість накладних:* 1" in caption
        assert "20451531340827" in caption
        assert "Не додано до цього реєстру:" in caption
        assert "20451531036290" in caption
        assert "105-80149920" in caption


@pytest.mark.asyncio
async def test_fetch_user_active_drafts_propagates_scan_sheet_number(tmp_path):
    import os
    from src.bot.handlers import fetch_user_active_drafts
    from src.storage import UserSettingsManager
    from src.nova_poshta.client import NovaPoshtaClient
    from src.nova_poshta.models import WaybillItemInfo
    from unittest.mock import AsyncMock, MagicMock

    storage_file = os.path.join(tmp_path, "user_settings.json")
    drafts_file = os.path.join(tmp_path, "user_drafts.json")
    scansheets_file = os.path.join(tmp_path, "user_scansheets.json")
    manager = UserSettingsManager(
        filepath=storage_file,
        drafts_filepath=drafts_file,
        scansheets_filepath=scansheets_file,
    )

    mock_client = MagicMock(spec=NovaPoshtaClient)
    mock_client.get_internet_document_list = AsyncMock(return_value=[
        WaybillItemInfo(
            int_doc_number="20451531340827",
            ref="ref1",
            state_name="Чернетка",
            recipient_name="Верич К.",
            city_recipient="Київ",
            address_recipient="Відділення 1",
            cost=80.0,
            description="Посилка",
            scan_sheet_number="105-80150900",
        ),
        WaybillItemInfo(
            int_doc_number="20451532860941",
            ref="ref2",
            state_name="Чернетка",
            recipient_name="Даниленко В.",
            city_recipient="Львів",
            address_recipient="Відділення 2",
            cost=90.0,
            description="Посилка",
            scan_sheet_number=None,
        )
    ])
    mock_client.get_documents_status = AsyncMock(return_value={
        "20451531340827": {"is_draft": True},
        "20451532860941": {"is_draft": True},
    })

    drafts = await fetch_user_active_drafts(999, mock_client, manager)
    assert len(drafts) == 2
    d1 = next(d for d in drafts if d["int_doc_number"] == "20451531340827")
    d2 = next(d for d in drafts if d["int_doc_number"] == "20451532860941")
    assert d1["scan_sheet_number"] == "105-80150900"
    assert d2["scan_sheet_number"] is None





