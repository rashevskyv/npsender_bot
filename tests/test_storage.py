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


def test_multi_user_sender_profiles(tmp_path):
    storage_file = os.path.join(tmp_path, "user_settings.json")
    drafts_file = os.path.join(tmp_path, "user_drafts.json")
    manager = UserSettingsManager(filepath=storage_file, drafts_filepath=drafts_file)

    global_s = Settings(
        TELEGRAM_BOT_TOKEN="dummy",
        NOVA_POSHTA_API_KEY="global_key",
        AI_API_KEY="global_ai",
    )

    # 1. Test legacy settings auto-migration to profile
    legacy_cfg = UserCustomSettings(
        nova_poshta_api_key="np_key_vlad",
        sender_name="Мартинюк Влад",
        sender_phone="380971112233",
        sender_city_ref="city_ref_kyiv",
        sender_city_name="Київ",
        sender_address_ref="wh_ref_1",
        sender_warehouse_name="Відділення №1",
        cod_monthly_limit_sum=30000.0,
        cod_monthly_limit_count=10,
        ai_api_key="ai_key_vlad",
    )
    manager.update_user_settings(1001, legacy_cfg)

    profiles = manager.get_sender_profiles(1001)
    assert len(profiles) == 1
    p1 = profiles[0]
    assert p1.name == "Мартинюк Влад"
    assert p1.nova_poshta_api_key == "np_key_vlad"
    assert p1.sender_phone == "380971112233"
    assert p1.sender_city_name == "Київ"

    active_p = manager.get_active_profile(1001)
    assert active_p is not None
    assert active_p.id == p1.id

    # 2. Add second profile (Olena)
    from src.storage import SenderProfile
    p2 = SenderProfile(
        id="prof_olena",
        name="Мартинюк Олена",
        nova_poshta_api_key="np_key_olena",
        sender_phone="380989998877",
        sender_name="Мартинюк Олена",
        sender_city_name="Київ",
        cod_monthly_limit_sum=30000.0,
    )
    manager.add_sender_profile(1001, p2, set_active=False)

    all_profs = manager.get_sender_profiles(1001)
    assert len(all_profs) == 2
    # Active should still be p1
    assert manager.get_active_profile(1001).id == p1.id

    # 3. Test get_effective_settings for active vs specific profile
    eff_active = manager.get_effective_settings(1001, global_s)
    assert eff_active.nova_poshta_api_key == "np_key_vlad"
    assert eff_active.sender_phone == "380971112233"
    assert eff_active.ai_api_key == "ai_key_vlad"  # AI key shared

    eff_olena = manager.get_effective_settings(1001, global_s, profile_id="prof_olena")
    assert eff_olena.nova_poshta_api_key == "np_key_olena"
    assert eff_olena.sender_phone == "380989998877"
    assert eff_olena.ai_api_key == "ai_key_vlad"  # AI key still shared!

    # 4. Switch active profile to Olena
    switched = manager.set_active_profile(1001, "prof_olena")
    assert switched is not None
    assert switched.name == "Мартинюк Олена"
    assert manager.get_active_profile(1001).id == "prof_olena"

    eff_new_active = manager.get_effective_settings(1001, global_s)
    assert eff_new_active.nova_poshta_api_key == "np_key_olena"
    assert eff_new_active.sender_phone == "380989998877"

    # 5. Update specific profile
    updated = manager.update_sender_profile(1001, "prof_olena", sender_warehouse_name="Відділення №5")
    assert updated.sender_warehouse_name == "Відділення №5"
    assert manager.get_active_profile(1001).sender_warehouse_name == "Відділення №5"

    # 6. Delete profile
    # If active profile is deleted, another profile becomes active
    assert manager.delete_sender_profile(1001, "prof_olena") is True
    assert len(manager.get_sender_profiles(1001)) == 1
    assert manager.get_active_profile(1001).id == p1.id

    # Cannot delete the only remaining profile
    assert manager.delete_sender_profile(1001, p1.id) is False
    assert len(manager.get_sender_profiles(1001)) == 1


