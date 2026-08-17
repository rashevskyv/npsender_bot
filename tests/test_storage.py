"""Unit tests for UserSettingsManager and per-user storage."""

import os
import pytest
from src.config import Settings
from src.storage import UserSettingsManager, UserCustomSettings, SavedDraft


def test_user_settings_manager(tmp_path):
    storage_file = os.path.join(tmp_path, "user_settings.json")
    drafts_file = os.path.join(tmp_path, "user_drafts.json")
    manager = UserSettingsManager(filepath=storage_file, drafts_filepath=drafts_file)

    # Initially unconfigured
    assert manager.is_user_configured(12345) is False
    u1 = manager.get_user_settings(12345)
    assert u1.nova_poshta_api_key is None
    assert u1.ai_api_key is None

    global_s = Settings(
        TELEGRAM_BOT_TOKEN="dummy",
        NOVA_POSHTA_API_KEY="global_admin_np_key",
        AI_API_KEY="global_admin_ai_key",
    )

    # Unconfigured user must NOT leak global admin's NP API key or AI API key
    eff_uncfg = manager.get_effective_settings(12345, global_s)
    assert eff_uncfg.nova_poshta_api_key == ""
    assert eff_uncfg.sender_phone == ""
    assert eff_uncfg.ai_api_key == ""
    assert eff_uncfg.ai_base_url == ""

    # Update user custom settings with NP key only -> still unconfigured until AI key added
    custom_np = UserCustomSettings(
        nova_poshta_api_key="custom_np_key_123",
        sender_phone="380991112233",
        sender_name="Custom Sender",
    )
    manager.update_user_settings(12345, custom_np)
    assert manager.is_user_configured(12345) is False

    # Add AI API key -> now fully configured
    manager.update_user_settings(12345, ai_api_key="custom_ai_key_999", ai_model="gpt-4o-mini")
    assert manager.is_user_configured(12345) is True

    eff = manager.get_effective_settings(12345, global_s)
    assert eff.nova_poshta_api_key == "custom_np_key_123"
    assert eff.sender_phone == "380991112233"
    assert eff.ai_api_key == "custom_ai_key_999"
    assert eff.ai_model == "gpt-4o-mini"

    # Reset settings
    manager.reset_user_settings(12345)
    assert manager.is_user_configured(12345) is False
    eff_reset = manager.get_effective_settings(12345, global_s)
    assert eff_reset.nova_poshta_api_key == ""
    assert eff_reset.ai_api_key == ""


def test_drafts_management(tmp_path):
    storage_file = os.path.join(tmp_path, "user_settings.json")
    drafts_file = os.path.join(tmp_path, "user_drafts.json")
    manager = UserSettingsManager(filepath=storage_file, drafts_filepath=drafts_file)

    draft = SavedDraft(
        ref="ref-123-abc",
        int_doc_number="20451506103956",
        recipient_name="Іванов Іван",
        recipient_phone="380971234567",
        city_description="Київ",
        warehouse_description="Відділення 1",
        payer_type="Recipient",
        cargo_description="Посилка",
        declared_value=500.0,
        cost=100.0,
        created_at="08.08.2026",
    )

    manager.add_user_draft(999, draft)
    drafts = manager.get_user_drafts(999)
    assert len(drafts) == 1
    assert drafts[0].ref == "ref-123-abc"
    assert drafts[0].int_doc_number == "20451506103956"

    # Delete draft by int_doc_number
    success = manager.delete_user_draft(999, "20451506103956")
    assert success is True
    assert len(manager.get_user_drafts(999)) == 0

    # Add again and delete by ref
    manager.add_user_draft(999, draft)
    assert len(manager.get_user_drafts(999)) == 1
    success = manager.delete_user_draft(999, "ref-123-abc")
    assert success is True
    assert len(manager.get_user_drafts(999)) == 0


def test_purge_sent_drafts(tmp_path):
    storage_file = os.path.join(tmp_path, "user_settings.json")
    drafts_file = os.path.join(tmp_path, "user_drafts.json")
    manager = UserSettingsManager(filepath=storage_file, drafts_filepath=drafts_file)

    d1 = SavedDraft(
        ref="ref-1",
        int_doc_number="204501",
        recipient_name="Іван",
        recipient_phone="380971111111",
        city_description="Київ",
        warehouse_description="Відділення 1",
        payer_type="Recipient",
        cargo_description="Посилка 1",
        declared_value=500.0,
        cost=80.0,
        created_at="08.08.2026",
    )
    d2 = SavedDraft(
        ref="ref-2",
        int_doc_number="204502",
        recipient_name="Петро",
        recipient_phone="380972222222",
        city_description="Одеса",
        warehouse_description="Відділення 2",
        payer_type="Sender",
        cargo_description="Посилка 2",
        declared_value=600.0,
        cost=90.0,
        created_at="08.08.2026",
    )

    manager.add_user_draft(111, d1)
    manager.add_user_draft(111, d2)
    assert len(manager.get_user_drafts(111)) == 2

    # Purge d1 by int_doc_number
    purged = manager.purge_sent_drafts(111, ["204501"])
    assert purged == 1
    remaining = manager.get_user_drafts(111)
    assert len(remaining) == 1
    assert remaining[0].ref == "ref-2"

