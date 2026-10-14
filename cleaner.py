import re
from typing import Tuple, Optional, Dict, Any, List
from settings_manager import settings_manager

URL_PATTERN = re.compile(
    r'(https?:\/\/(?:www\.|(?!www))[a-zA-Z0-9][a-zA-Z0-9-]+[a-zA-Z0-9]\.[^\s]{2,}|'
    r'www\.[a-zA-Z0-9][a-zA-Z0-9-]+[a-zA-Z0-9]\.[^\s]{2,}|'
    r'https?:\/\/(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}(?:\/[^\s]*)?|'
    r't\.me\/[a-zA-Z0-9_+/]+)',
    re.IGNORECASE
)

USERNAME_PATTERN = re.compile(r'(@[a-zA-Z0-9_]{4,32})')

def is_image_message(message: Any) -> bool:
    """
    Checks if a message contains an image/photo.
    Compatible with Telethon Message objects or custom mock structures.
    """
    if not message:
        return False
    # Check Telethon photo attribute
    if getattr(message, 'photo', None) is not None:
        return True

    # Check direct document attribute on message
    doc = getattr(message, 'document', None)
    if doc is not None:
        mime = getattr(doc, 'mime_type', '') or ''
        if mime.startswith('image/'):
            return True

    media = getattr(message, 'media', None)
    if not media:
        return False
    media_class = media.__class__.__name__
    if media_class == 'MessageMediaPhoto':
        return True
    if media_class == 'MessageMediaDocument' and hasattr(media, 'document') and media.document:
        mime = getattr(media.document, 'mime_type', '') or ''
        if mime.startswith('image/'):
            return True
    if getattr(media, 'mime_type', '').startswith('image/'):
        return True
    return False

def should_process_message(text: Optional[str], custom_settings: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """
    Checks whether a message should be processed based on whitelist/blacklist keywords.
    Returns (should_process, reason).
    """
    cust = custom_settings or settings_manager.get("customizations", {})
    text_check = (text or "").lower()

    # 1. Blacklist check
    blacklist = cust.get("blacklist_keywords", [])
    if blacklist:
        for word in blacklist:
            if word and str(word).lower() in text_check:
                return False, f"Contains blacklisted keyword: '{word}'"

    # 2. Whitelist check
    whitelist = cust.get("whitelist_keywords", [])
    if whitelist and any(w.strip() for w in whitelist):
        matched = any(w and str(w).lower() in text_check for w in whitelist if str(w).strip())
        if not matched:
            return False, "Does not match any whitelisted keywords"

    return True, "OK"

def transform_text(text: Optional[str], custom_settings: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """
    Applies text cleaning, URL replacements, username replacements,
    word rules, header/footer additions, and link filtering.
    """
    cust = custom_settings or settings_manager.get("customizations", {})
    if not text:
        header = cust.get("header", "").strip()
        footer = cust.get("footer", "").strip()
        result = ""
        if header:
            result = header + "\n\n" + result
        if footer:
            result = result + ("\n\n" if result else "") + footer
        return result.strip() if result else None

    modified_text = text

    # 1. Word / Phrase replacement rules
    word_rules: List[Dict[str, str]] = cust.get("word_rules", [])
    for rule in word_rules:
        match_str = rule.get("match", "")
        replace_str = rule.get("replace", "")
        if match_str:
            pattern = re.compile(re.escape(match_str), re.IGNORECASE)
            modified_text = pattern.sub(replace_str, modified_text)

    # 2. Specific Username replacement rules
    username_rules: List[Dict[str, str]] = cust.get("username_rules", [])
    for rule in username_rules:
        match_str = rule.get("match", "")
        replace_str = rule.get("replace", "")
        if match_str:
            if not match_str.startswith("@"):
                match_str = "@" + match_str
            if replace_str and not replace_str.startswith("@") and not replace_str.startswith("http"):
                replace_str = "@" + replace_str
            pattern = re.compile(re.escape(match_str), re.IGNORECASE)
            modified_text = pattern.sub(replace_str, modified_text)

    # 3. Global Username replacement or removal
    if cust.get("remove_all_usernames", False):
        modified_text = USERNAME_PATTERN.sub('', modified_text)
    elif cust.get("replace_all_usernames", False) and cust.get("global_replacement_username", "").strip():
        global_user = cust.get("global_replacement_username", "").strip()
        if not global_user.startswith("@"):
            global_user = "@" + global_user
        modified_text = USERNAME_PATTERN.sub(global_user, modified_text)

    # 4. Specific URL replacement rules
    url_rules: List[Dict[str, str]] = cust.get("url_rules", [])
    for rule in url_rules:
        match_str = rule.get("match", "")
        replace_str = rule.get("replace", "")
        if match_str:
            # Escape regex but handle potential protocol differences
            pattern = re.compile(re.escape(match_str), re.IGNORECASE)
            modified_text = pattern.sub(replace_str, modified_text)

    # 5. Global URL replacement or removal
    if cust.get("remove_all_urls", False):
        modified_text = URL_PATTERN.sub('', modified_text)
    elif cust.get("replace_all_urls", False) and cust.get("global_replacement_url", "").strip():
        global_url = cust.get("global_replacement_url", "").strip()
        modified_text = URL_PATTERN.sub(global_url, modified_text)

    # Clean up excessive newlines
    modified_text = re.sub(r'\n{3,}', '\n\n', modified_text).strip()

    # 6. Custom Header & Footer
    header = cust.get("header", "").strip()
    footer = cust.get("footer", "").strip()

    if header:
        modified_text = f"{header}\n\n{modified_text}"
    if footer:
        modified_text = f"{modified_text}\n\n{footer}"

    return modified_text.strip()
