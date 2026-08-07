"""Unit tests for UserSettingsManager and per-user storage."""

import os
import pytest
from src.config import Settings
from src.storage import UserSettingsManager, UserCustomSettings, SavedDraft


def test_user_settings_manager(tmp_path):
    storage_file = os.path.join(tmp_path, "user_settings.json")
    drafts_file = os.path.join(tmp_path, "user_drafts.json")
    manager = UserSettingsManager(filepath=storage_file, drafts_filepath=drafts_file)

    # Initially empty
    u1 = manager.get_user_settings(12345)
    assert u1.nova_poshta_api_key is None

    # Update settings
    custom = UserCustomSettings(
        nova_poshta_api_key="custom_np_key_123",
        sender_phone="380991112233",
        sender_name="Custom Sender",
    )
    manager.update_user_settings(12345, custom)

    # Re-read
    u1_updated = manager.get_user_settings(12345)
    assert u1_updated.nova_poshta_api_key == "custom_np_key_123"
    assert u1_updated.sender_phone == "380991112233"

    # Effective settings
    global_s = Settings(
        TELEGRAM_BOT_TOKEN="dummy",
        NOVA_POSHTA_API_KEY="default_np_key",
    )
    eff = manager.get_effective_settings(12345, global_s)
    assert eff.nova_poshta_api_key == "custom_np_key_123"
    assert eff.sender_phone == "380991112233"

    # Reset settings
    manager.reset_user_settings(12345)
    eff_reset = manager.get_effective_settings(12345, global_s)
    assert eff_reset.nova_poshta_api_key == "default_np_key"


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

    # Delete draft
    success = manager.delete_user_draft(999, "ref-123-abc")
    assert success is True
    assert len(manager.get_user_drafts(999)) == 0
