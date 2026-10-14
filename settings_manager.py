import os
import json
import logging
from typing import Dict, Any, List
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

SETTINGS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings.json")
ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

def get_env_bool(key: str, default: bool) -> bool:
    val = os.getenv(key)
    if val is None:
        return default
    return val.strip().lower() in ("true", "1", "yes")

def get_env_list(key: str) -> List[str]:
    val = os.getenv(key, "")
    if not val:
        return []
    items = []
    for s in val.split(","):
        cleaned = s.strip().strip("\"'")
        if cleaned:
            items.append(cleaned)
    return items

DEFAULT_SETTINGS: Dict[str, Any] = {
    "telegram": {
        "api_id": os.getenv("API_ID", "").strip(),
        "api_hash": os.getenv("API_HASH", "").strip(),
        "bot_token": os.getenv("BOT_TOKEN", "").strip(),
        "phone_number": os.getenv("PHONE_NUMBER", "").strip(),
        "session_name": os.getenv("SESSION_NAME", "telegram_mirror_session").strip(),
        "session_string": os.getenv("SESSION_STRING", "").strip()
    },
    "web": {
        "host": os.getenv("WEB_HOST", "0.0.0.0").strip(),
        "port": int(os.getenv("PORT", os.getenv("WEB_PORT", "8080"))),
        "admin_password": os.getenv("ADMIN_PASSWORD", "admin123").strip()
    },
    "bot": {
        "copy_mode": os.getenv("COPY_MODE", "clean_copy").strip(),
        "action_delay": float(os.getenv("ACTION_DELAY", "2.0")),
        "mirror_edits": get_env_bool("MIRROR_EDITS", True),
        "auto_start": get_env_bool("AUTO_START", True),
        "remove_images": get_env_bool("REMOVE_IMAGES", False)
    },
    "channels": {
        "sources": get_env_list("SOURCE_CHANNELS"),
        "destinations": get_env_list("DESTINATION_CHANNELS"),
        "mappings": {}
    },
    "customizations": {
        "replace_all_urls": False,
        "global_replacement_url": os.getenv("GLOBAL_REPLACEMENT_URL", "").strip(),
        "url_rules": [],
        "replace_all_usernames": False,
        "global_replacement_username": os.getenv("GLOBAL_REPLACEMENT_USERNAME", "").strip(),
        "username_rules": [],
        "word_rules": [],
        "remove_all_urls": get_env_bool("REMOVE_LINKS", False),
        "remove_all_usernames": False,
        "remove_images": get_env_bool("REMOVE_IMAGES", False),
        "header": os.getenv("CUSTOM_HEADER", ""),
        "footer": os.getenv("CUSTOM_FOOTER", ""),
        "blacklist_keywords": get_env_list("BLACKLIST_KEYWORDS"),
        "whitelist_keywords": get_env_list("WHITELIST_KEYWORDS")
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
        data = {}
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception as e:
                logging.error(f"Failed to load settings from {self.filepath}: {e}")

        # Start with default structure
        merged = json.loads(json.dumps(DEFAULT_SETTINGS))

        # Deep merge file data
        for sec, val in data.items():
            if isinstance(val, dict) and sec in merged:
                merged[sec].update(val)
            else:
                merged[sec] = val

        # Ensure environment variables ALWAYS take precedence if explicitly defined in runtime env
        if os.getenv("API_ID"):
            merged["telegram"]["api_id"] = os.getenv("API_ID", "").strip()
        if os.getenv("API_HASH"):
            merged["telegram"]["api_hash"] = os.getenv("API_HASH", "").strip()
        if os.getenv("BOT_TOKEN"):
            merged["telegram"]["bot_token"] = os.getenv("BOT_TOKEN", "").strip()
        if os.getenv("SESSION_STRING"):
            merged["telegram"]["session_string"] = os.getenv("SESSION_STRING", "").strip()
        if os.getenv("PHONE_NUMBER"):
            merged["telegram"]["phone_number"] = os.getenv("PHONE_NUMBER", "").strip()
        if os.getenv("PORT"):
            merged["web"]["port"] = int(os.getenv("PORT", "8080"))
        if not data.get("channels", {}).get("sources") and os.getenv("SOURCE_CHANNELS"):
            srcs = get_env_list("SOURCE_CHANNELS")
            if srcs:
                merged["channels"]["sources"] = srcs
        if not data.get("channels", {}).get("destinations") and os.getenv("DESTINATION_CHANNELS"):
            dsts = get_env_list("DESTINATION_CHANNELS")
            if dsts:
                merged["channels"]["destinations"] = dsts
        if os.getenv("COPY_MODE"):
            merged["bot"]["copy_mode"] = os.getenv("COPY_MODE", "").strip()
        if os.getenv("ACTION_DELAY"):
            try:
                merged["bot"]["action_delay"] = float(os.getenv("ACTION_DELAY", "2.0"))
            except ValueError:
                pass
        if os.getenv("MIRROR_EDITS") is not None:
            merged["bot"]["mirror_edits"] = get_env_bool("MIRROR_EDITS", True)
        if os.getenv("REMOVE_IMAGES") is not None:
            rem_img = get_env_bool("REMOVE_IMAGES", False)
            merged["bot"]["remove_images"] = rem_img
            merged["customizations"]["remove_images"] = rem_img

        # Save merged state
        self._save(merged)
        return merged

    def _save(self, data: Dict[str, Any]):
        try:
            with open(self.filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.error(f"Failed to save settings to {self.filepath}: {e}")

    def sync_to_env_file(self):
        """Syncs key credentials and parameters to .env file if available."""
        try:
            tg = self._settings.get("telegram", {})
            web = self._settings.get("web", {})
            bot = self._settings.get("bot", {})
            ch = self._settings.get("channels", {})
            cust = self._settings.get("customizations", {})

            src_str = ",".join(ch.get("sources", []))
            dst_str = ",".join(ch.get("destinations", []))

            # Keep runtime process environment updated as well
            os.environ["SOURCE_CHANNELS"] = src_str
            os.environ["DESTINATION_CHANNELS"] = dst_str

            lines = [
                "# TeleMirror Pro - Auto-Generated Environment Configuration",
                f"API_ID={tg.get('api_id', '')}",
                f"API_HASH={tg.get('api_hash', '')}",
                f"BOT_TOKEN={tg.get('bot_token', '')}",
                f"PHONE_NUMBER={tg.get('phone_number', '')}",
                f"SESSION_STRING={tg.get('session_string', '')}",
                f"PORT={web.get('port', 8080)}",
                f"WEB_HOST={web.get('host', '0.0.0.0')}",
                f"ADMIN_PASSWORD={web.get('admin_password', 'admin123')}",
                f"COPY_MODE={bot.get('copy_mode', 'clean_copy')}",
                f"ACTION_DELAY={bot.get('action_delay', 2.0)}",
                f"MIRROR_EDITS={str(bot.get('mirror_edits', True)).lower()}",
                f"SOURCE_CHANNELS={src_str}",
                f"DESTINATION_CHANNELS={dst_str}",
                f"REMOVE_LINKS={str(cust.get('remove_all_urls', False)).lower()}",
                f"REMOVE_IMAGES={str(bot.get('remove_images', cust.get('remove_images', False))).lower()}",
                f"BLACKLIST_KEYWORDS={','.join(cust.get('blacklist_keywords', []))}",
                f"WHITELIST_KEYWORDS={','.join(cust.get('whitelist_keywords', []))}"
            ]
            with open(ENV_FILE, "w", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception as e:
            logging.warning(f"Could not sync to .env file: {e}")

    def generate_render_env_dict(self) -> Dict[str, str]:
        """Generates key-value dictionary formatted for Render Environment Variables."""
        tg = self._settings.get("telegram", {})
        web = self._settings.get("web", {})
        bot = self._settings.get("bot", {})
        ch = self._settings.get("channels", {})
        cust = self._settings.get("customizations", {})

        return {
            "API_ID": str(tg.get("api_id", "")),
            "API_HASH": str(tg.get("api_hash", "")),
            "BOT_TOKEN": str(tg.get("bot_token", "")),
            "PHONE_NUMBER": str(tg.get("phone_number", "")),
            "SESSION_STRING": str(tg.get("session_string", "")),
            "PORT": str(web.get("port", 10000)),
            "COPY_MODE": str(bot.get("copy_mode", "clean_copy")),
            "ACTION_DELAY": str(bot.get("action_delay", "2.0")),
            "MIRROR_EDITS": str(bot.get("mirror_edits", True)).lower(),
            "REMOVE_IMAGES": str(bot.get("remove_images", cust.get("remove_images", False))).lower(),
            "SOURCE_CHANNELS": ",".join(ch.get("sources", [])),
            "DESTINATION_CHANNELS": ",".join(ch.get("destinations", [])),
            "ADMIN_PASSWORD": str(web.get("admin_password", "admin123"))
        }

    def get_all(self) -> Dict[str, Any]:
        return self._settings

    def get(self, section: str, default=None):
        return self._settings.get(section, default)

    def update_section(self, section: str, new_values: Dict[str, Any]):
        if section not in self._settings:
            self._settings[section] = {}
        self._settings[section].update(new_values)
        self._save(self._settings)
        self.sync_to_env_file()

    def update_all(self, new_data: Dict[str, Any]):
        self._settings.update(new_data)
        self._save(self._settings)
        self.sync_to_env_file()

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
