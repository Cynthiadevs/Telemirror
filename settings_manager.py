import os
import json
import logging
from typing import Dict, Any, List
from dotenv import load_dotenv

load_dotenv()

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")

DEFAULT_SETTINGS: Dict[str, Any] = {
    "telegram": {
        "api_id": os.getenv("API_ID", "").strip(),
        "api_hash": os.getenv("API_HASH", "").strip(),
        "phone_number": os.getenv("PHONE_NUMBER", "").strip(),
        "session_name": os.getenv("SESSION_NAME", "telegram_mirror_session").strip(),
        "session_string": os.getenv("SESSION_STRING", "").strip()
    },
    "web": {
        "host": os.getenv("WEB_HOST", "0.0.0.0"),
        "port": int(os.getenv("PORT", os.getenv("WEB_PORT", "8080"))),
        "admin_password": os.getenv("ADMIN_PASSWORD", "admin123")
    },
    "bot": {
        "copy_mode": os.getenv("COPY_MODE", "clean_copy"),
        "action_delay": float(os.getenv("ACTION_DELAY", "2.0")),
        "mirror_edits": os.getenv("MIRROR_EDITS", "true").lower() in ("true", "1", "yes"),
        "auto_start": True
    },
    "channels": {
        "sources": [s.strip() for s in os.getenv("SOURCE_CHANNELS", "").split(",") if s.strip()],
        "destinations": [d.strip() for d in os.getenv("DESTINATION_CHANNELS", "").split(",") if d.strip()],
        "mappings": {}
    },
    "customizations": {
        "replace_all_urls": False,
        "global_replacement_url": "",
        "url_rules": [
            # {"match": "t.me/original_link", "replace": "https://t.me/my_promo_link"}
        ],
        "replace_all_usernames": False,
        "global_replacement_username": "",
        "username_rules": [
            # {"match": "@old_admin", "replace": "@my_admin"}
        ],
        "word_rules": [
            # {"match": "OldBrand", "replace": "MyBrand"}
        ],
        "remove_all_urls": False,
        "remove_all_usernames": False,
        "header": os.getenv("CUSTOM_HEADER", ""),
        "footer": os.getenv("CUSTOM_FOOTER", ""),
        "blacklist_keywords": [k.strip() for k in os.getenv("BLACKLIST_KEYWORDS", "").split(",") if k.strip()],
        "whitelist_keywords": [k.strip() for k in os.getenv("WHITELIST_KEYWORDS", "").split(",") if k.strip()]
    },
    "stats": {
        "total_messages_processed": 0,
        "total_messages_mirrored": 0,
        "total_messages_skipped": 0,
        "last_mirrored_at": None
    }
}

class SettingsManager:
    def __init__(self, filepath: str = SETTINGS_FILE):
        self.filepath = filepath
        self._settings = self._load()

    def _load(self) -> Dict[str, Any]:
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    # Merge with default settings to ensure all keys exist
                    merged = DEFAULT_SETTINGS.copy()
                    for k, v in data.items():
                        if isinstance(v, dict) and k in merged:
                            merged_sub = merged[k].copy()
                            merged_sub.update(v)
                            # If env vars are set, override empty values
                            for sub_k, sub_v in DEFAULT_SETTINGS[k].items():
                                if sub_v and (not merged_sub.get(sub_k) or str(merged_sub.get(sub_k)).strip() == ""):
                                    merged_sub[sub_k] = sub_v
                            merged[k] = merged_sub
                        else:
                            merged[k] = v
                    return merged
            except Exception as e:
                logging.error(f"Failed to load settings from {self.filepath}: {e}")
        
        # If file doesn't exist, create it from defaults
        self._save(DEFAULT_SETTINGS)
        return DEFAULT_SETTINGS.copy()

    def _save(self, data: Dict[str, Any]):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error(f"Failed to save settings to {self.filepath}: {e}")

    def get_all(self) -> Dict[str, Any]:
        return self._settings

    def get(self, section: str, default=None):
        return self._settings.get(section, default)

    def update_section(self, section: str, new_values: Dict[str, Any]):
        if section not in self._settings:
            self._settings[section] = {}
        self._settings[section].update(new_values)
        self._save(self._settings)

    def update_all(self, new_data: Dict[str, Any]):
        self._settings.update(new_data)
        self._save(self._settings)

    def increment_stat(self, stat_name: str, amount: int = 1):
        if "stats" not in self._settings:
            self._settings["stats"] = {}
        self._settings["stats"][stat_name] = self._settings["stats"].get(stat_name, 0) + amount
        self._save(self._settings)

    def set_stat(self, stat_name: str, value: Any):
        if "stats" not in self._settings:
            self._settings["stats"] = {}
        self._settings["stats"][stat_name] = value
        self._save(self._settings)

settings_manager = SettingsManager()
