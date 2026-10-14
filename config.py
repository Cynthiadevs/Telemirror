import os
import json
import logging
from typing import Dict, List, Union
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def parse_channel_list(val: str) -> List[Union[str, int]]:
    """Parse comma-separated channels (usernames or numeric IDs)."""
    if not val:
        return []
    channels = []
    for item in val.split(","):
        cleaned = item.strip()
        if not cleaned:
            continue
        # Check if it's a numeric ID (e.g. -100123456789 or 123456789)
        if cleaned.lstrip("-").isdigit():
            channels.append(int(cleaned))
        else:
            channels.append(cleaned)
    return channels

def parse_json_dict(val: str) -> Dict[str, str]:
    """Parse JSON string safely into a dict."""
    if not val:
        return {}
    try:
        data = json.loads(val)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception as e:
        logging.warning(f"Failed to parse JSON dict from config: {e}")
    return {}

def parse_str_list(val: str) -> List[str]:
    """Parse comma-separated strings."""
    if not val:
        return []
    return [item.strip().lower() for item in val.split(",") if item.strip()]

# Telegram credentials
API_ID_RAW = os.getenv("API_ID", "").strip()
API_ID = int(API_ID_RAW) if API_ID_RAW.isdigit() else None
API_HASH = os.getenv("API_HASH", "").strip()
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
PHONE_NUMBER = os.getenv("PHONE_NUMBER", "").strip()
SESSION_NAME = os.getenv("SESSION_NAME", "telegram_mirror_session").strip()
SESSION_STRING = os.getenv("SESSION_STRING", "").strip()

# Channel routing
SOURCE_CHANNELS = parse_channel_list(os.getenv("SOURCE_CHANNELS", ""))
DESTINATION_CHANNELS = parse_channel_list(os.getenv("DESTINATION_CHANNELS", ""))
CHANNEL_MAPPING_RAW = parse_json_dict(os.getenv("CHANNEL_MAPPING", ""))

# Behavior
COPY_MODE = os.getenv("COPY_MODE", "clean_copy").strip().lower()  # 'clean_copy' or 'forward'
ACTION_DELAY = float(os.getenv("ACTION_DELAY", "2.0"))
MIRROR_EDITS = os.getenv("MIRROR_EDITS", "false").strip().lower() in ("true", "1", "yes")

# Filtering and text transformation
REMOVE_LINKS = os.getenv("REMOVE_LINKS", "false").strip().lower() in ("true", "1", "yes")
REMOVE_IMAGES = os.getenv("REMOVE_IMAGES", "false").strip().lower() in ("true", "1", "yes")
REPLACE_MAP = parse_json_dict(os.getenv("REPLACE_MAP", ""))
BLACKLIST_KEYWORDS = parse_str_list(os.getenv("BLACKLIST_KEYWORDS", ""))
WHITELIST_KEYWORDS = parse_str_list(os.getenv("WHITELIST_KEYWORDS", ""))
CUSTOM_HEADER = os.getenv("CUSTOM_HEADER", "").strip()
CUSTOM_FOOTER = os.getenv("CUSTOM_FOOTER", "").strip()

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
LOG_FILE = os.getenv("LOG_FILE", "mirror_bot.log").strip()

def get_routing_map() -> Dict[Union[str, int], List[Union[str, int]]]:
    """
    Returns a unified mapping of source channel -> list of destination channels.
    """
    routing = {}
    
    # Check explicit mapping first
    if CHANNEL_MAPPING_RAW:
        for src, dst in CHANNEL_MAPPING_RAW.items():
            src_key = int(src) if src.lstrip("-").isdigit() else src
            dst_val = int(dst) if str(dst).lstrip("-").isdigit() else dst
            routing[src_key] = [dst_val]
    elif SOURCE_CHANNELS and DESTINATION_CHANNELS:
        for src in SOURCE_CHANNELS:
            routing[src] = list(DESTINATION_CHANNELS)
            
    return routing

def validate_config() -> List[str]:
    """Validate core configuration and return any error messages."""
    errors = []
    if not API_ID:
        errors.append("API_ID is missing or invalid. Obtain it from https://my.telegram.org")
    if not API_HASH:
        errors.append("API_HASH is missing. Obtain it from https://my.telegram.org")
    if not PHONE_NUMBER:
        errors.append("PHONE_NUMBER is missing. Set your phone number in .env (e.g. +1234567890)")
    if not SOURCE_CHANNELS and not CHANNEL_MAPPING_RAW:
        errors.append("No SOURCE_CHANNELS or CHANNEL_MAPPING configured.")
    if not DESTINATION_CHANNELS and not CHANNEL_MAPPING_RAW:
        errors.append("No DESTINATION_CHANNELS or CHANNEL_MAPPING configured.")
    return errors
