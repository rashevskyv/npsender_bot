"""Persistent user settings and created waybill drafts storage."""

import json
import os
import logging
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from src.config import Settings

logger = logging.getLogger(__name__)

SETTINGS_STORAGE_PATH = "data/user_settings.json"
DRAFTS_STORAGE_PATH = "data/user_drafts.json"


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


class UserSettingsManager:
    """Manages persistent loading and saving of user custom settings and waybill drafts."""

    def __init__(
        self,
        filepath: str = SETTINGS_STORAGE_PATH,
        drafts_filepath: str = DRAFTS_STORAGE_PATH,
    ):
        self.filepath = filepath
        self.drafts_filepath = drafts_filepath
        self.data: Dict[str, UserCustomSettings] = {}
        self.drafts: Dict[str, List[SavedDraft]] = {}
        self.load()

    def load(self):
        """Load user settings and drafts from JSON files."""
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)
                    self.data = {
                        uid: UserCustomSettings(**u_dict)
                        for uid, u_dict in raw_data.items()
                    }
            except Exception as e:
                logger.error(f"Failed to load user settings: {e}")
                self.data = {}

        if os.path.exists(self.drafts_filepath):
            try:
                with open(self.drafts_filepath, "r", encoding="utf-8") as f:
                    raw_drafts = json.load(f)
                    self.drafts = {
                        uid: [SavedDraft(**d) for d in d_list]
                        for uid, d_list in raw_drafts.items()
                    }
            except Exception as e:
                logger.error(f"Failed to load drafts: {e}")
                self.drafts = {}

    def save(self):
        """Save user settings to JSON file."""
        try:
            os.makedirs(os.path.dirname(self.filepath), exist_ok=True)
            raw_data = {uid: u_dict.model_dump() for uid, u_dict in self.data.items()}
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(raw_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save user settings: {e}")

    def save_drafts(self):
        """Save user drafts to JSON file."""
        try:
            os.makedirs(os.path.dirname(self.drafts_filepath), exist_ok=True)
            raw_drafts = {
                uid: [d.model_dump() for d in d_list]
                for uid, d_list in self.drafts.items()
            }
            with open(self.drafts_filepath, "w", encoding="utf-8") as f:
                json.dump(raw_drafts, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Failed to save drafts: {e}")

    def get_user_settings(self, user_id: int) -> UserCustomSettings:
        """Get custom settings for a specific user ID."""
        return self.data.get(str(user_id), UserCustomSettings())

    def update_user_settings(self, user_id: int, settings: UserCustomSettings):
        """Update and save custom settings for a user ID."""
        self.data[str(user_id)] = settings
        self.save()

    def reset_user_settings(self, user_id: int):
        """Reset custom settings for a user ID."""
        if str(user_id) in self.data:
            del self.data[str(user_id)]
            self.save()

    def get_effective_settings(
        self, user_id: int, global_settings: Settings
    ) -> Settings:
        """Combine user custom settings with global defaults."""
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
        # Prepend new draft
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
