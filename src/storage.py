"""Persistent user settings, created waybill drafts, and ScanSheet registers storage."""

import json
import os
import logging
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from src.config import Settings

logger = logging.getLogger(__name__)

SETTINGS_STORAGE_PATH = "data/user_settings.json"
DRAFTS_STORAGE_PATH = "data/user_drafts.json"
SCANSHEETS_STORAGE_PATH = "data/user_scansheets.json"


class SenderProfile(BaseModel):
    """Specific sender account / profile configuration."""

    id: str
    name: str = "Користувач 1"
    nova_poshta_api_key: Optional[str] = None
    sender_counterparty_ref: Optional[str] = None
    sender_contact_ref: Optional[str] = None
    sender_city_ref: Optional[str] = None
    sender_city_name: Optional[str] = None
    sender_address_ref: Optional[str] = None
    sender_warehouse_name: Optional[str] = None
    sender_phone: Optional[str] = None
    sender_name: Optional[str] = None
    sender_card_mask: Optional[str] = None
    sender_card_ref: Optional[str] = None
    cod_monthly_limit_sum: Optional[float] = 30000.0
    cod_monthly_limit_count: Optional[int] = 10
    cod_warning_enabled: bool = True


class UserCustomSettings(BaseModel):
    """User-specific credentials and configuration."""

    # AI Provider Settings (shared per Telegram user)
    ai_api_key: Optional[str] = None
    ai_base_url: Optional[str] = None
    ai_model: Optional[str] = None

    # Multi-sender profiles
    profiles: List[SenderProfile] = Field(default_factory=list)
    active_profile_id: Optional[str] = None

    # Mirrored active profile fields for 100% backward compatibility
    nova_poshta_api_key: Optional[str] = None
    sender_counterparty_ref: Optional[str] = None
    sender_contact_ref: Optional[str] = None
    sender_city_ref: Optional[str] = None
    sender_city_name: Optional[str] = None
    sender_address_ref: Optional[str] = None
    sender_warehouse_name: Optional[str] = None
    sender_phone: Optional[str] = None
    sender_name: Optional[str] = None
    sender_card_mask: Optional[str] = None
    sender_card_ref: Optional[str] = None
    cod_monthly_limit_sum: Optional[float] = 30000.0
    cod_monthly_limit_count: Optional[int] = 10
    cod_warning_enabled: bool = True


class SavedDraft(BaseModel):
    """Created waybill draft details."""

    ref: str
    int_doc_number: str
    recipient_name: str
    recipient_phone: str
    city_description: str
    warehouse_description: str
    payer_type: str
    cargo_description: str
    declared_value: float
    cod_amount: Optional[float] = None
    cod_payment_type: Optional[str] = None
    cost: float
    created_at: str
    is_light_return: bool = False
    scan_sheet_number: Optional[str] = None


class SavedScanSheet(BaseModel):
    """Created Nova Poshta ScanSheet (Register) details."""

    ref: str
    number: str
    date_created: str
    count_of_documents: int
    document_numbers: List[str] = Field(default_factory=list)


class UserSettingsManager:
    """Manages persistent loading and saving of user custom settings, waybill drafts, and ScanSheets."""

    def __init__(
        self,
        filepath: str = SETTINGS_STORAGE_PATH,
        drafts_filepath: str = DRAFTS_STORAGE_PATH,
        scansheets_filepath: str = SCANSHEETS_STORAGE_PATH,
    ):
        self.filepath = filepath
        self.drafts_filepath = drafts_filepath
        self.scansheets_filepath = scansheets_filepath
        self.data: Dict[str, UserCustomSettings] = {}
        self.drafts: Dict[str, List[SavedDraft]] = {}
        self.scansheets: Dict[str, List[SavedScanSheet]] = {}
        self.load()

    def load(self):
        """Load user settings, drafts, and scansheets from JSON files."""
        os.makedirs("data", exist_ok=True)
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)
                    self.data = {
                        uid: UserCustomSettings(**u_dict)
                        for uid, u_dict in raw_data.items()
                    }
            except Exception as e:
                logger.error(f"Error loading user settings from {self.filepath}: {e}")

        if os.path.exists(self.drafts_filepath):
            try:
                with open(self.drafts_filepath, "r", encoding="utf-8") as f:
                    raw_drafts = json.load(f)
                    self.drafts = {
                        uid: [SavedDraft(**d) for d in d_list]
                        for uid, d_list in raw_drafts.items()
                    }
            except Exception as e:
                logger.error(f"Error loading user drafts from {self.drafts_filepath}: {e}")

        if os.path.exists(self.scansheets_filepath):
            try:
                with open(self.scansheets_filepath, "r", encoding="utf-8") as f:
                    raw_sheets = json.load(f)
                    self.scansheets = {
                        uid: [
                            SavedScanSheet(**s) for s in s_list
                            if s.get("ref") and s.get("number") and str(s.get("number")).strip()
                        ]
                        for uid, s_list in raw_sheets.items()
                    }
            except Exception as e:
                logger.error(f"Error loading user scansheets from {self.scansheets_filepath}: {e}")

    def save_settings(self):
        """Save user settings to JSON file."""
        os.makedirs(os.path.dirname(self.filepath) or ".", exist_ok=True)
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                dump_dict = {
                    uid: u_obj.model_dump() for uid, u_obj in self.data.items()
                }
                json.dump(dump_dict, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving user settings to {self.filepath}: {e}")

    def save_drafts(self):
        """Save user drafts to JSON file."""
        os.makedirs(os.path.dirname(self.drafts_filepath) or ".", exist_ok=True)
        try:
            with open(self.drafts_filepath, "w", encoding="utf-8") as f:
                dump_dict = {
                    uid: [d.model_dump() for d in d_list]
                    for uid, d_list in self.drafts.items()
                }
                json.dump(dump_dict, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving user drafts to {self.drafts_filepath}: {e}")

    def save_scansheets(self):
        """Save user scansheets to JSON file."""
        os.makedirs(os.path.dirname(self.scansheets_filepath) or ".", exist_ok=True)
        try:
            with open(self.scansheets_filepath, "w", encoding="utf-8") as f:
                dump_dict = {
                    uid: [s.model_dump() for s in s_list]
                    for uid, s_list in self.scansheets.items()
                }
                json.dump(dump_dict, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Error saving user scansheets to {self.scansheets_filepath}: {e}")

    def _ensure_profile_migration(self, user_settings: UserCustomSettings) -> bool:
        """Migrate legacy top-level sender settings into profiles if profiles list is empty."""
        changed = False
        if not user_settings.profiles and user_settings.nova_poshta_api_key and user_settings.nova_poshta_api_key.strip():
            prof_name = user_settings.sender_name or "Користувач 1"
            prof = SenderProfile(
                id="prof_1",
                name=prof_name,
                nova_poshta_api_key=user_settings.nova_poshta_api_key,
                sender_counterparty_ref=user_settings.sender_counterparty_ref,
                sender_contact_ref=user_settings.sender_contact_ref,
                sender_city_ref=user_settings.sender_city_ref,
                sender_city_name=user_settings.sender_city_name,
                sender_address_ref=user_settings.sender_address_ref,
                sender_warehouse_name=user_settings.sender_warehouse_name,
                sender_phone=user_settings.sender_phone,
                sender_name=user_settings.sender_name,
                sender_card_mask=user_settings.sender_card_mask,
                sender_card_ref=user_settings.sender_card_ref,
                cod_monthly_limit_sum=user_settings.cod_monthly_limit_sum if user_settings.cod_monthly_limit_sum is not None else 30000.0,
                cod_monthly_limit_count=user_settings.cod_monthly_limit_count if user_settings.cod_monthly_limit_count is not None else 10,
                cod_warning_enabled=user_settings.cod_warning_enabled,
            )
            user_settings.profiles.append(prof)
            user_settings.active_profile_id = prof.id
            changed = True

        # Ensure active_profile_id is valid
        if user_settings.profiles:
            if not user_settings.active_profile_id or not any(p.id == user_settings.active_profile_id for p in user_settings.profiles):
                user_settings.active_profile_id = user_settings.profiles[0].id
                changed = True
            # Sync top-level mirrored fields from active profile
            active_p = next((p for p in user_settings.profiles if p.id == user_settings.active_profile_id), user_settings.profiles[0])
            user_settings.nova_poshta_api_key = active_p.nova_poshta_api_key
            user_settings.sender_counterparty_ref = active_p.sender_counterparty_ref
            user_settings.sender_contact_ref = active_p.sender_contact_ref
            user_settings.sender_city_ref = active_p.sender_city_ref
            user_settings.sender_city_name = active_p.sender_city_name
            user_settings.sender_address_ref = active_p.sender_address_ref
            user_settings.sender_warehouse_name = active_p.sender_warehouse_name
            user_settings.sender_phone = active_p.sender_phone
            user_settings.sender_name = active_p.sender_name
            user_settings.sender_card_mask = active_p.sender_card_mask
            user_settings.sender_card_ref = active_p.sender_card_ref
            user_settings.cod_monthly_limit_sum = active_p.cod_monthly_limit_sum
            user_settings.cod_monthly_limit_count = active_p.cod_monthly_limit_count
            user_settings.cod_warning_enabled = active_p.cod_warning_enabled
        return changed

    def get_user_settings(self, user_id: int) -> UserCustomSettings:
        """Get custom settings for a user ID, ensuring profiles are migrated."""
        uid_str = str(user_id)
        if uid_str not in self.data:
            return UserCustomSettings()
        settings_obj = self.data[uid_str]
        if self._ensure_profile_migration(settings_obj):
            self.save_settings()
        return settings_obj

    def get_sender_profiles(self, user_id: int) -> List[SenderProfile]:
        """Get all sender profiles for a user."""
        u_settings = self.get_user_settings(user_id)
        return list(u_settings.profiles)

    def get_active_profile(self, user_id: int) -> Optional[SenderProfile]:
        """Get the currently active sender profile for a user."""
        u_settings = self.get_user_settings(user_id)
        if not u_settings.profiles:
            return None
        for p in u_settings.profiles:
            if p.id == u_settings.active_profile_id:
                return p
        return u_settings.profiles[0]

    def set_active_profile(self, user_id: int, profile_id: str) -> Optional[SenderProfile]:
        """Set the active sender profile by ID."""
        uid_str = str(user_id)
        u_settings = self.get_user_settings(user_id)
        found = None
        for p in u_settings.profiles:
            if p.id == profile_id:
                found = p
                break
        if not found:
            return None

        u_settings.active_profile_id = found.id
        self._ensure_profile_migration(u_settings)
        self.data[uid_str] = u_settings
        self.save_settings()
        return found

    def add_sender_profile(
        self, user_id: int, profile: SenderProfile, set_active: bool = False
    ) -> SenderProfile:
        """Add a new sender profile for a user."""
        uid_str = str(user_id)
        u_settings = self.get_user_settings(user_id)

        # Check if ID already exists, generate unique if so
        existing_ids = {p.id for p in u_settings.profiles}
        if profile.id in existing_ids or not profile.id:
            profile.id = f"prof_{len(u_settings.profiles) + 1}"

        u_settings.profiles.append(profile)
        if set_active or not u_settings.active_profile_id:
            u_settings.active_profile_id = profile.id

        self._ensure_profile_migration(u_settings)
        self.data[uid_str] = u_settings
        self.save_settings()
        return profile

    def delete_sender_profile(self, user_id: int, profile_id: str) -> bool:
        """Delete a sender profile by ID (cannot delete if it's the only one)."""
        uid_str = str(user_id)
        u_settings = self.get_user_settings(user_id)
        if len(u_settings.profiles) <= 1:
            return False

        initial_len = len(u_settings.profiles)
        u_settings.profiles = [p for p in u_settings.profiles if p.id != profile_id]
        if len(u_settings.profiles) == initial_len:
            return False

        if u_settings.active_profile_id == profile_id:
            u_settings.active_profile_id = u_settings.profiles[0].id

        self._ensure_profile_migration(u_settings)
        self.data[uid_str] = u_settings
        self.save_settings()
        return True

    def update_sender_profile(
        self, user_id: int, profile_id: str, **kwargs
    ) -> Optional[SenderProfile]:
        """Update specific sender profile fields."""
        uid_str = str(user_id)
        u_settings = self.get_user_settings(user_id)
        found = None
        for p in u_settings.profiles:
            if p.id == profile_id:
                found = p
                break
        if not found:
            return None

        p_dict = found.model_dump()
        for k, v in kwargs.items():
            if hasattr(found, k):
                p_dict[k] = v
        updated_p = SenderProfile(**p_dict)

        u_settings.profiles = [
            updated_p if p.id == profile_id else p for p in u_settings.profiles
        ]
        self._ensure_profile_migration(u_settings)
        self.data[uid_str] = u_settings
        self.save_settings()
        return updated_p

    def update_user_settings(
        self,
        user_id: int,
        custom_settings: Optional[UserCustomSettings] = None,
        **kwargs,
    ):
        """Update fields for a user's custom settings and sync with active profile."""
        uid_str = str(user_id)
        current = self.get_user_settings(user_id)
        updated_dict = current.model_dump()

        if custom_settings is not None:
            for k, v in custom_settings.model_dump(exclude_none=True).items():
                updated_dict[k] = v

        for k, v in kwargs.items():
            if hasattr(current, k):
                updated_dict[k] = v

        updated_obj = UserCustomSettings(**updated_dict)
        self._ensure_profile_migration(updated_obj)

        # Also sync changed sender fields into active profile
        if updated_obj.profiles and updated_obj.active_profile_id:
            for p in updated_obj.profiles:
                if p.id == updated_obj.active_profile_id:
                    for k, v in kwargs.items():
                        if hasattr(p, k):
                            setattr(p, k, v)
                    break
            self._ensure_profile_migration(updated_obj)

        self.data[uid_str] = updated_obj
        self.save_settings()

    def reset_user_settings(self, user_id: int):
        """Reset custom settings for a user ID."""
        uid_str = str(user_id)
        if uid_str in self.data:
            del self.data[uid_str]
            self.save_settings()

    def is_user_configured(self, user_id: int) -> bool:
        """Return True if user has provided both their personal Nova Poshta API key and AI API key."""
        u_id = str(user_id)
        if u_id not in self.data:
            return False
        user_cfg = self.get_user_settings(user_id)
        has_np = bool(user_cfg.nova_poshta_api_key and user_cfg.nova_poshta_api_key.strip())
        has_ai = bool(user_cfg.ai_api_key and user_cfg.ai_api_key.strip())
        return has_np and has_ai

    def get_effective_settings(
        self, user_id: int, global_settings: Settings, profile_id: Optional[str] = None
    ) -> Settings:
        """Return a merged Settings object taking user overrides into account.
        If profile_id is specified, use that profile's credentials.
        Otherwise use the active profile.
        If user is not configured with credentials, blank out NP and AI credentials.
        """
        user_custom = self.get_user_settings(user_id)
        target_profile = None
        if profile_id:
            for p in user_custom.profiles:
                if p.id == profile_id:
                    target_profile = p
                    break
        if not target_profile:
            target_profile = self.get_active_profile(user_id)

        effective_dict = global_settings.model_dump()

        np_key = (
            target_profile.nova_poshta_api_key
            if target_profile
            else user_custom.nova_poshta_api_key
        )
        if np_key and np_key.strip():
            effective_dict["nova_poshta_api_key"] = np_key
            effective_dict["sender_counterparty_ref"] = (
                (target_profile.sender_counterparty_ref if target_profile else user_custom.sender_counterparty_ref) or ""
            )
            effective_dict["sender_contact_ref"] = (
                (target_profile.sender_contact_ref if target_profile else user_custom.sender_contact_ref) or ""
            )
            effective_dict["sender_city_ref"] = (
                (target_profile.sender_city_ref if target_profile else user_custom.sender_city_ref) or ""
            )
            effective_dict["sender_address_ref"] = (
                (target_profile.sender_address_ref if target_profile else user_custom.sender_address_ref) or ""
            )
            effective_dict["sender_phone"] = (
                (target_profile.sender_phone if target_profile else user_custom.sender_phone) or ""
            )
            effective_dict["sender_name"] = (
                (target_profile.sender_name if target_profile else user_custom.sender_name) or ""
            )
        else:
            effective_dict["nova_poshta_api_key"] = ""
            effective_dict["sender_counterparty_ref"] = ""
            effective_dict["sender_contact_ref"] = ""
            effective_dict["sender_city_ref"] = ""
            effective_dict["sender_address_ref"] = ""
            effective_dict["sender_phone"] = ""
            effective_dict["sender_name"] = ""

        if user_custom.ai_api_key and user_custom.ai_api_key.strip():
            effective_dict["ai_api_key"] = user_custom.ai_api_key
            effective_dict["ai_base_url"] = user_custom.ai_base_url or "https://api.openai.com/v1"
            if user_custom.ai_model:
                effective_dict["ai_model"] = user_custom.ai_model
        else:
            effective_dict["ai_api_key"] = ""
            effective_dict["ai_base_url"] = ""

        return Settings(**effective_dict)

    def add_user_draft(self, user_id: int, draft: SavedDraft):
        """Add a newly created draft to user's drafts list."""
        uid_str = str(user_id)
        if uid_str not in self.drafts:
            self.drafts[uid_str] = []
        self.drafts[uid_str].insert(0, draft)
        self.save_drafts()

    def get_user_drafts(self, user_id: int) -> List[SavedDraft]:
        """Get drafts list for user ID."""
        return self.drafts.get(str(user_id), [])

    def delete_user_draft(self, user_id: int, ref: str) -> bool:
        """Delete a draft by its Ref GUID or int_doc_number for user ID."""
        uid_str = str(user_id)
        if uid_str in self.drafts:
            initial_len = len(self.drafts[uid_str])
            self.drafts[uid_str] = [
                d for d in self.drafts[uid_str]
                if d.ref != ref and d.int_doc_number != ref
            ]
            if len(self.drafts[uid_str]) < initial_len:
                self.save_drafts()
                return True
        return False

    def purge_sent_drafts(self, user_id: int, sent_identifiers: List[str]) -> int:
        """Purge drafts matching any sent ref or document number for user ID."""
        uid_str = str(user_id)
        if uid_str not in self.drafts or not sent_identifiers:
            return 0
        
        sent_set = set(sent_identifiers)
        initial_len = len(self.drafts[uid_str])
        self.drafts[uid_str] = [
            d for d in self.drafts[uid_str]
            if d.ref not in sent_set and d.int_doc_number not in sent_set
        ]
        removed_count = initial_len - len(self.drafts[uid_str])
        if removed_count > 0:
            self.save_drafts()
        return removed_count

    def update_drafts_scansheet(
        self, user_id: int, doc_numbers: List[str], scansheet_number: Optional[str]
    ) -> int:
        """Update scan_sheet_number on user drafts matching given doc_numbers or refs."""
        uid_str = str(user_id)
        if uid_str not in self.drafts or not doc_numbers:
            return 0
        target_set = set(str(n).strip() for n in doc_numbers)
        updated = 0
        for d in self.drafts[uid_str]:
            if d.int_doc_number in target_set or d.ref in target_set:
                d.scan_sheet_number = scansheet_number
                updated += 1
        if updated > 0:
            self.save_drafts()
        return updated

    def add_user_scansheet(self, user_id: int, scansheet: SavedScanSheet):
        """Add a created ScanSheet register to user's storage."""
        if not scansheet.ref or not scansheet.number or not str(scansheet.number).strip():
            logger.warning(f"Ignoring attempt to save invalid scansheet with empty ref/number: {scansheet}")
            return
        uid_str = str(user_id)
        if uid_str not in self.scansheets:
            self.scansheets[uid_str] = []
        self.scansheets[uid_str].insert(0, scansheet)
        self.save_scansheets()

    def get_user_scansheets(self, user_id: int) -> List[SavedScanSheet]:
        """Get valid ScanSheets list for user ID."""
        sheets = self.scansheets.get(str(user_id), [])
        return [s for s in sheets if s.ref and s.number and str(s.number).strip()]

    def cleanup_invalid_scansheets(self, user_id: int) -> int:
        """Purge any saved scansheets with empty ref or number from storage."""
        uid_str = str(user_id)
        if uid_str not in self.scansheets:
            return 0
        initial_len = len(self.scansheets[uid_str])
        self.scansheets[uid_str] = [
            s for s in self.scansheets[uid_str]
            if s.ref and s.number and str(s.number).strip()
        ]
        removed_count = initial_len - len(self.scansheets[uid_str])
        if removed_count > 0:
            self.save_scansheets()
        return removed_count

    def delete_user_scansheet(self, user_id: int, ref: str) -> bool:
        """Delete a ScanSheet by Ref GUID for user ID."""
        uid_str = str(user_id)
        if uid_str in self.scansheets:
            initial_len = len(self.scansheets[uid_str])
            self.scansheets[uid_str] = [
                s for s in self.scansheets[uid_str] if s.ref != ref and s.number != ref
            ]
            if len(self.scansheets[uid_str]) < initial_len:
                self.save_scansheets()
                return True
        return False

    def remove_document_from_user_scansheet(
        self, user_id: int, scansheet_ref_or_number: str, doc_number: str
    ) -> Optional[SavedScanSheet]:
        """Remove a document number from a saved user scansheet, updating count and list."""
        uid_str = str(user_id)
        if uid_str not in self.scansheets:
            return None
        clean_doc = str(doc_number).strip()
        for s in self.scansheets[uid_str]:
            if s.ref == scansheet_ref_or_number or s.number == scansheet_ref_or_number:
                updated_nums = [d for d in s.document_numbers if d != clean_doc]
                if len(updated_nums) != len(s.document_numbers):
                    s.document_numbers = updated_nums
                    s.count_of_documents = len(updated_nums)
                    self.save_scansheets()
                return s
        return None

    def purge_old_or_sent_scansheets(self, user_id: int, refs: List[str]) -> int:
        """Purge ScanSheets matching any ref in refs list for user ID."""
        uid_str = str(user_id)
        if uid_str not in self.scansheets or not refs:
            return 0
        
        purge_set = set(refs)
        initial_len = len(self.scansheets[uid_str])
        self.scansheets[uid_str] = [
            s for s in self.scansheets[uid_str]
            if s.ref not in purge_set
        ]
        removed_count = initial_len - len(self.scansheets[uid_str])
        if removed_count > 0:
            self.save_scansheets()
        return removed_count
