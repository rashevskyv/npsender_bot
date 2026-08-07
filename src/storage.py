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


class UserCustomSettings(BaseModel):
    """User-specific credentials and configuration."""

    nova_poshta_api_key: Optional[str] = None
    ai_api_key: Optional[str] = None
    ai_base_url: Optional[str] = None
    sender_counterparty_ref: Optional[str] = None
    sender_contact_ref: Optional[str] = None
    sender_city_ref: Optional[str] = None
    sender_address_ref: Optional[str] = None
    sender_phone: Optional[str] = None
    sender_name: Optional[str] = None


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
    cost: float
    created_at: str


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
                        uid: [SavedScanSheet(**s) for s in s_list]
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

    def get_user_settings(self, user_id: int) -> UserCustomSettings:
        """Get custom settings for a user ID."""
        return self.data.get(str(user_id), UserCustomSettings())

    def update_user_settings(
        self,
        user_id: int,
        custom_settings: Optional[UserCustomSettings] = None,
        **kwargs,
    ):
        """Update fields for a user's custom settings."""
        uid_str = str(user_id)
        current = self.get_user_settings(user_id)
        updated_dict = current.model_dump()

        if custom_settings is not None:
            for k, v in custom_settings.model_dump(exclude_none=True).items():
                updated_dict[k] = v

        for k, v in kwargs.items():
            if hasattr(current, k):
                updated_dict[k] = v

        self.data[uid_str] = UserCustomSettings(**updated_dict)
        self.save_settings()

    def reset_user_settings(self, user_id: int):
        """Reset custom settings for a user ID."""
        uid_str = str(user_id)
        if uid_str in self.data:
            del self.data[uid_str]
            self.save_settings()

    def get_effective_settings(
        self, user_id: int, global_settings: Settings
    ) -> Settings:
        """Return a merged Settings object taking user overrides into account."""
        user_custom = self.get_user_settings(user_id)
        effective_dict = global_settings.model_dump()

        if user_custom.nova_poshta_api_key:
            effective_dict["nova_poshta_api_key"] = user_custom.nova_poshta_api_key
        if user_custom.ai_api_key:
            effective_dict["ai_api_key"] = user_custom.ai_api_key
        if user_custom.ai_base_url:
            effective_dict["ai_base_url"] = user_custom.ai_base_url
        if user_custom.sender_counterparty_ref:
            effective_dict["sender_counterparty_ref"] = (
                user_custom.sender_counterparty_ref
            )
        if user_custom.sender_contact_ref:
            effective_dict["sender_contact_ref"] = user_custom.sender_contact_ref
        if user_custom.sender_city_ref:
            effective_dict["sender_city_ref"] = user_custom.sender_city_ref
        if user_custom.sender_address_ref:
            effective_dict["sender_address_ref"] = user_custom.sender_address_ref
        if user_custom.sender_phone:
            effective_dict["sender_phone"] = user_custom.sender_phone

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
        """Delete a draft by its Ref GUID for user ID."""
        uid_str = str(user_id)
        if uid_str in self.drafts:
            initial_len = len(self.drafts[uid_str])
            self.drafts[uid_str] = [
                d for d in self.drafts[uid_str] if d.ref != ref
            ]
            if len(self.drafts[uid_str]) < initial_len:
                self.save_drafts()
                return True
        return False

    def add_user_scansheet(self, user_id: int, scansheet: SavedScanSheet):
        """Add a created ScanSheet register to user's storage."""
        uid_str = str(user_id)
        if uid_str not in self.scansheets:
            self.scansheets[uid_str] = []
        self.scansheets[uid_str].insert(0, scansheet)
        self.save_scansheets()

    def get_user_scansheets(self, user_id: int) -> List[SavedScanSheet]:
        """Get ScanSheets list for user ID."""
        return self.scansheets.get(str(user_id), [])

    def delete_user_scansheet(self, user_id: int, ref: str) -> bool:
        """Delete a ScanSheet by Ref GUID for user ID."""
        uid_str = str(user_id)
        if uid_str in self.scansheets:
            initial_len = len(self.scansheets[uid_str])
            self.scansheets[uid_str] = [
                s for s in self.scansheets[uid_str] if s.ref != ref
            ]
            if len(self.scansheets[uid_str]) < initial_len:
                self.save_scansheets()
                return True
        return False
