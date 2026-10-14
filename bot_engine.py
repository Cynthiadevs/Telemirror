import os
import re
import asyncio
import logging
from datetime import datetime
from collections import deque
from typing import Dict, List, Any, Optional, Union

from telethon import TelegramClient, events, errors, utils
from telethon.sessions import StringSession, SQLiteSession
from telethon.tl.custom import Message
from telethon.tl.types import (
    MessageMediaPoll,
    MessageMediaWebPage,
    MessageMediaDice,
    MessageMediaContact,
    MessageMediaGeo
)

from settings_manager import settings_manager
from cleaner import should_process_message, transform_text, is_image_message
from album_manager import AlbumManager

# ==============================================================================
# Live Log Broadcaster for WebSocket Terminal
# ==============================================================================
class LogBroadcaster(logging.Handler):
    def __init__(self, maxlen: int = 300):
        super().__init__()
        self.logs = deque(maxlen=maxlen)
        self.subscribers = set()

    def emit(self, record):
        log_entry = {
            "time": datetime.now().strftime("%H:%M:%S"),
            "level": record.levelname,
            "message": self.format(record)
        }
        self.logs.append(log_entry)
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(log_entry)
            except Exception:
                pass

    def subscribe(self, queue: asyncio.Queue):
        self.subscribers.add(queue)

    def unsubscribe(self, queue: asyncio.Queue):
        self.subscribers.discard(queue)

log_broadcaster = LogBroadcaster()
formatter = logging.Formatter("%(message)s")
log_broadcaster.setFormatter(formatter)
logging.getLogger().addHandler(log_broadcaster)
logger = logging.getLogger("BotEngine")

# ==============================================================================
# Channel Identifier Helpers & Robust Matching
# ==============================================================================
def _clean_channel_identifier(val: Any) -> Optional[Union[int, str]]:
    """Normalizes an individual channel identifier (username, numeric ID, or URL)."""
    if val is None:
        return None
    if isinstance(val, int):
        return val
    s = str(val).strip().strip("'\"")
    if not s:
        return None
    # If purely digits or negative digits (e.g. -100123456789 or 123456789)
    if s.lstrip("-").isdigit():
        try:
            return int(s)
        except ValueError:
            pass
    # Strip URL prefixes
    s = re.sub(r'^https?://t\.me/', '', s, flags=re.IGNORECASE)
    s = re.sub(r'^t\.me/', '', s, flags=re.IGNORECASE)
    s = s.strip().strip("'\"")
    if not s:
        return None
    # If now purely digits after stripping
    if s.lstrip("-").isdigit():
        try:
            return int(s)
        except ValueError:
            pass
    # If invite link / joinchat, keep as is
    if s.startswith("+") or s.startswith("joinchat/"):
        return s
    # Ensure leading @ for usernames
    if not s.startswith("@"):
        s = f"@{s}"
    return s

def _split_and_clean_channels(val: Any) -> List[Union[int, str]]:
    """Splits lists, comma/newline-separated strings, and cleans each channel."""
    if val is None:
        return []
    if isinstance(val, int):
        return [val]
    if isinstance(val, (list, tuple, set)):
        results = []
        for item in val:
            results.extend(_split_and_clean_channels(item))
        return results
    raw_str = str(val).strip()
    if not raw_str:
        return []
    tokens = re.split(r'[\r\n,;]+', raw_str)
    cleaned = []
    for token in tokens:
        c = _clean_channel_identifier(token)
        if c is not None and c not in cleaned:
            cleaned.append(c)
    return cleaned

def _is_chat_match(configured_src: Any, incoming_chat_id: int, resolved_entity: Any = None) -> bool:
    """
    Robustly matches an incoming Telegram chat ID (-100... / -... / ...) against a configured source,
    accounting for raw channel IDs (without -100), username matches, and resolved Telethon entities.
    """
    if configured_src is None or incoming_chat_id is None:
        return False

    # 1. Direct integer or string comparison
    if configured_src == incoming_chat_id:
        return True
    src_str = str(configured_src).strip().strip("'\"")
    inc_str = str(incoming_chat_id).strip()
    if src_str == inc_str:
        return True

    # 2. Extract raw IDs (stripping -100 or -)
    def to_raw(val: Any) -> Optional[int]:
        s = str(val).strip().strip("'\"")
        if s.startswith("-100"):
            s = s[4:]
        elif s.startswith("-"):
            s = s[1:]
        return int(s) if s.isdigit() else None

    src_raw = to_raw(src_str)
    inc_raw = to_raw(inc_str)
    if src_raw is not None and inc_raw is not None and src_raw == inc_raw:
        return True

    # 3. Check against resolved_entity if available
    if resolved_entity is not None:
        # First verify resolved_entity corresponds to configured_src
        ent_matches_src = False
        ent_id = getattr(resolved_entity, 'id', None)
        ent_username = getattr(resolved_entity, 'username', None)

        if ent_id is not None and (ent_id == src_raw or getattr(resolved_entity, 'id', None) == configured_src):
            ent_matches_src = True
        elif ent_username:
            clean_ent_u = ent_username.lower().lstrip('@')
            clean_src_u = src_str.lower().lstrip('@')
            clean_src_u = re.sub(r'^https?://t\.me/', '', clean_src_u, flags=re.IGNORECASE)
            clean_src_u = re.sub(r'^t\.me/', '', clean_src_u, flags=re.IGNORECASE).lstrip('@')
            if clean_ent_u == clean_src_u:
                ent_matches_src = True
        else:
            ent_title = getattr(resolved_entity, 'title', None)
            if ent_title and ent_title.lower() == src_str.lower():
                ent_matches_src = True

        if ent_matches_src:
            try:
                peer_id = utils.get_peer_id(resolved_entity)
                if peer_id == incoming_chat_id:
                    return True
            except Exception:
                pass

            if ent_id is not None:
                if ent_id == incoming_chat_id:
                    return True
                if inc_raw is not None and ent_id == inc_raw:
                    return True

    return False

# ==============================================================================
# Bot Engine
# ==============================================================================
class BotEngine:
    def __init__(self):
        self.status = "stopped"  # "stopped", "starting", "running", "error"
        self.error_message: Optional[str] = None
        self.client: Optional[TelegramClient] = None
        self.user_info: Optional[Dict[str, Any]] = None
        self.bot_mode: str = "userbot"  # "userbot" or "bot_token"
        self.resolved_sources: Dict[Any, Any] = {}
        self.resolved_destinations: Dict[Any, Any] = {}
        self.message_history_map: Dict[tuple, List[tuple]] = {}
        self.album_manager = AlbumManager(callback=self._handle_album_batch)
        self.started_at: Optional[datetime] = None
        self._lock = asyncio.Lock()

    def export_session_string(self) -> Optional[str]:
        """Exports active or stored session as a Telethon StringSession for cloud deployment."""
        if self.client and self.client.session:
            try:
                if isinstance(self.client.session, StringSession):
                    return self.client.session.save()
                elif isinstance(self.client.session, SQLiteSession):
                    s_str = StringSession()
                    s_str._server_address = self.client.session.server_address
                    s_str._port = self.client.session.port
                    s_str._auth_key = self.client.session.auth_key
                    s_str._takeout_id = self.client.session.takeout_id
                    return s_str.save()
            except Exception as e:
                logger.warning(f"Could not export string session from active client: {e}")

        # Try from settings or disk session file
        tg_cfg = settings_manager.get("telegram", {})
        session_str = tg_cfg.get("session_string") or os.getenv("SESSION_STRING", "")
        if session_str:
            return session_str

        session_name = tg_cfg.get("session_name", "telegram_mirror_session")
        session_file = f"{session_name}.session"
        if os.path.exists(session_file):
            try:
                s_sql = SQLiteSession(session_name)
                s_str = StringSession()
                s_str._server_address = s_sql.server_address
                s_str._port = s_sql.port
                s_str._auth_key = s_sql.auth_key
                s_str._takeout_id = s_sql.takeout_id
                encoded = s_str.save()
                if encoded:
                    return encoded
            except Exception as e:
                logger.warning(f"Could not export string session from SQLite file: {e}")
        return None

    def get_status(self) -> Dict[str, Any]:
        # Live client connection state check
        is_conn = bool(self.client and self.client.is_connected())
        if self.status == "running" and not is_conn:
            self.status = "stopped"
            self.started_at = None

        uptime_seconds = None
        uptime_str = "Offline"
        if self.status == "running" and self.started_at:
            uptime_seconds = int((datetime.now() - self.started_at).total_seconds())
            hours, rem = divmod(uptime_seconds, 3600)
            mins, secs = divmod(rem, 60)
            if hours > 0:
                uptime_str = f"{hours}h {mins}m {secs}s"
            elif mins > 0:
                uptime_str = f"{mins}m {secs}s"
            else:
                uptime_str = f"{secs}s"

        channels_cfg = settings_manager.get("channels", {})
        cfg_sources = _split_and_clean_channels(channels_cfg.get("sources", []))
        cfg_dests = _split_and_clean_channels(channels_cfg.get("destinations", []))

        # Live channel details
        resolved_src_list = []
        for src_k, entity in self.resolved_sources.items():
            ent_id = getattr(entity, 'id', str(src_k))
            peer_id = None
            try:
                peer_id = utils.get_peer_id(entity)
            except Exception:
                pass
            resolved_src_list.append({
                "key": str(src_k),
                "id": ent_id,
                "peer_id": peer_id,
                "title": getattr(entity, 'title', str(src_k)),
                "username": f"@{entity.username}" if getattr(entity, 'username', None) else None,
                "status": "connected"
            })

        resolved_dst_list = []
        for dst_k, entity in self.resolved_destinations.items():
            ent_id = getattr(entity, 'id', str(dst_k))
            peer_id = None
            try:
                peer_id = utils.get_peer_id(entity)
            except Exception:
                pass
            resolved_dst_list.append({
                "key": str(dst_k),
                "id": ent_id,
                "peer_id": peer_id,
                "title": getattr(entity, 'title', str(dst_k)),
                "username": f"@{entity.username}" if getattr(entity, 'username', None) else None,
                "status": "connected"
            })

        remove_images = settings_manager.get("bot", {}).get("remove_images", False) or \
                        settings_manager.get("customizations", {}).get("remove_images", False)

        return {
            "status": self.status,
            "error_message": self.error_message,
            "user_info": self.user_info,
            "bot_mode": self.bot_mode,
            "is_connected": is_conn,
            "uptime_seconds": uptime_seconds,
            "uptime_str": uptime_str,
            "started_at": self.started_at.strftime("%Y-%m-%d %H:%M:%S") if self.started_at else None,
            "sources_count": len(self.resolved_sources),
            "destinations_count": len(self.resolved_destinations),
            "configured_sources_count": len(cfg_sources),
            "configured_destinations_count": len(cfg_dests),
            "resolved_sources": resolved_src_list,
            "resolved_destinations": resolved_dst_list,
            "remove_images": bool(remove_images),
            "has_session_string": bool(self.export_session_string()),
            "stats": settings_manager.get("stats", {})
        }

    async def start(self) -> bool:
        async with self._lock:
            if self.status in ("running", "starting"):
                return True

            self.status = "starting"
            self.error_message = None
            logger.info("🚀 Starting Bot Engine...")

            # Clean up any lingering client
            if self.client:
                try:
                    if self.client.is_connected():
                        await self.client.disconnect()
                except Exception:
                    pass
                self.client = None

            tg_cfg = settings_manager.get("telegram", {})
            api_id_val = tg_cfg.get("api_id")
            api_hash = tg_cfg.get("api_hash", "").strip()
            bot_token = tg_cfg.get("bot_token", "").strip()
            phone = tg_cfg.get("phone_number", "").strip()
            session_string = (tg_cfg.get("session_string") or os.getenv("SESSION_STRING", "")).strip()
            session_name = tg_cfg.get("session_name", "telegram_mirror_session").strip()

            if not api_id_val or not api_hash:
                self.status = "error"
                self.error_message = "Telegram API ID and API Hash are required. Obtain from https://my.telegram.org."
                logger.error(f"❌ {self.error_message}")
                return False

            try:
                api_id = int(api_id_val)
            except ValueError:
                self.status = "error"
                self.error_message = "API_ID must be a valid integer."
                logger.error(f"❌ {self.error_message}")
                return False

            try:
                # Determine connection mode: Bot Token vs Userbot (StringSession or SQLite)
                if bot_token:
                    self.bot_mode = "bot_token"
                    logger.info("🤖 Starting in Bot Token mode...")
                    self.client = TelegramClient(StringSession(), api_id, api_hash)
                    await self.client.start(bot_token=bot_token)
                elif session_string:
                    self.bot_mode = "userbot"
                    logger.info("🔑 Starting with StringSession (Headless Cloud Userbot)...")
                    self.client = TelegramClient(StringSession(session_string), api_id, api_hash)
                    await self.client.connect()
                    if not await self.client.is_user_authorized():
                        self.status = "error"
                        self.error_message = "Provided SESSION_STRING is expired or unauthorized. Please re-export session string."
                        logger.error(f"❌ {self.error_message}")
                        await self.client.disconnect()
                        self.client = None
                        return False
                else:
                    self.bot_mode = "userbot"
                    logger.info(f"📁 Starting with local session file '{session_name}'...")
                    self.client = TelegramClient(session_name, api_id, api_hash)
                    await self.client.connect()
                    if not await self.client.is_user_authorized():
                        self.status = "error"
                        self.error_message = "Session not authenticated. Please run export tool or configure SESSION_STRING / BOT_TOKEN."
                        logger.error(f"❌ {self.error_message}")
                        await self.client.disconnect()
                        self.client = None
                        return False

                me = await self.client.get_me()
                self.user_info = {
                    "id": me.id,
                    "first_name": me.first_name,
                    "username": me.username,
                    "phone": getattr(me, 'phone', None),
                    "is_bot": getattr(me, 'bot', False)
                }
                logger.info(f"✅ Connected as: {me.first_name} (@{me.username or 'NoUsername'}) [ID: {me.id}]")

                # Resolve channels
                await self._resolve_channels()

                # Register event handlers
                self.client.add_event_handler(self._on_new_message, events.NewMessage)
                self.client.add_event_handler(self._on_message_edited, events.MessageEdited)

                self.status = "running"
                self.started_at = datetime.now()
                logger.info("✨ Bot Engine is active and listening for messages.")
                return True

            except Exception as e:
                self.status = "error"
                self.error_message = str(e)
                logger.error(f"❌ Error starting bot: {e}", exc_info=True)
                if self.client and self.client.is_connected():
                    try:
                        await self.client.disconnect()
                    except Exception:
                        pass
                self.client = None
                return False

    async def stop(self) -> bool:
        async with self._lock:
            logger.info("🛑 Stopping Bot Engine...")
            try:
                if self.client:
                    if self.client.is_connected():
                        await self.client.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting client: {e}")
            finally:
                self.client = None

            self.status = "stopped"
            self.started_at = None
            self.error_message = None
            self.resolved_sources.clear()
            self.resolved_destinations.clear()
            logger.info("👋 Bot Engine stopped.")
            return True

    async def restart(self) -> bool:
        await self.stop()
        await asyncio.sleep(0.5)
        return await self.start()

    async def _resolve_single_destination(self, dst: Any) -> Optional[Any]:
        """Resolves a single destination identifier, checking cache, direct lookup, and dialog cache."""
        if not self.client or not self.client.is_connected():
            return None

        if hasattr(dst, 'id'):
            return dst

        cleaned = _clean_channel_identifier(dst)
        if cleaned is None:
            return None

        # Check existing resolved dictionary
        if cleaned in self.resolved_destinations:
            return self.resolved_destinations[cleaned]
        if dst in self.resolved_destinations:
            return self.resolved_destinations[dst]

        # Check by ID or username match in currently resolved
        for k, ent in self.resolved_destinations.items():
            if _is_chat_match(cleaned, getattr(ent, 'id', 0), ent):
                return ent

        # Attempt direct resolution via Telethon
        candidates = [cleaned]
        if isinstance(cleaned, int) and cleaned > 0:
            try:
                candidates.append(int(f"-100{cleaned}"))
            except ValueError:
                pass
        elif isinstance(cleaned, str) and cleaned.startswith("-100"):
            try:
                candidates.append(int(cleaned))
            except ValueError:
                pass

        for cand in candidates:
            try:
                entity = await self.client.get_entity(cand)
                if entity:
                    self.resolved_destinations[cleaned] = entity
                    logger.info(f"📤 Destination resolved: {getattr(entity, 'title', str(dst))} (ID: {getattr(entity, 'id', '')})")
                    return entity
            except Exception:
                continue

        # Fallback: scan active user dialogs
        try:
            async for dialog in self.client.iter_dialogs(limit=100):
                if _is_chat_match(cleaned, dialog.id, dialog.entity):
                    self.resolved_destinations[cleaned] = dialog.entity
                    logger.info(f"📤 Destination matched from dialogs: {dialog.title} (ID: {dialog.id})")
                    return dialog.entity
        except Exception as e:
            logger.debug(f"Dialog fallback check notice for '{dst}': {e}")

        logger.warning(f"⚠️ Could not resolve destination '{dst}'")
        return None

    async def _resolve_single_source(self, src: Any) -> Optional[Any]:
        """Resolves a single source identifier, checking cache, direct lookup, and dialog cache."""
        if not self.client or not self.client.is_connected():
            return None

        if hasattr(src, 'id'):
            return src

        cleaned = _clean_channel_identifier(src)
        if cleaned is None:
            return None

        if cleaned in self.resolved_sources:
            return self.resolved_sources[cleaned]
        if src in self.resolved_sources:
            return self.resolved_sources[src]

        for k, ent in self.resolved_sources.items():
            if _is_chat_match(cleaned, getattr(ent, 'id', 0), ent):
                return ent

        candidates = [cleaned]
        if isinstance(cleaned, int) and cleaned > 0:
            try:
                candidates.append(int(f"-100{cleaned}"))
            except ValueError:
                pass
        elif isinstance(cleaned, str) and cleaned.startswith("-100"):
            try:
                candidates.append(int(cleaned))
            except ValueError:
                pass

        for cand in candidates:
            try:
                entity = await self.client.get_entity(cand)
                if entity:
                    self.resolved_sources[cleaned] = entity
                    logger.info(f"📥 Source resolved: {getattr(entity, 'title', str(src))} (ID: {getattr(entity, 'id', '')})")
                    return entity
            except Exception:
                continue

        try:
            async for dialog in self.client.iter_dialogs(limit=100):
                if _is_chat_match(cleaned, dialog.id, dialog.entity):
                    self.resolved_sources[cleaned] = dialog.entity
                    logger.info(f"📥 Source matched from dialogs: {dialog.title} (ID: {dialog.id})")
                    return dialog.entity
        except Exception as e:
            logger.debug(f"Dialog fallback check notice for '{src}': {e}")

        logger.warning(f"⚠️ Could not resolve source '{src}'")
        return None

    async def _resolve_channels(self):
        """Pre-caches dialogs and resolves all sources & destinations without dropping active mappings."""
        if not self.client or not self.client.is_connected():
            return

        logger.info("🔄 Pre-caching dialogs and resolving channels...")
        try:
            # Pre-cache dialog entities into Telethon's internal entity cache
            await self.client.get_dialogs(limit=100)
        except Exception as e:
            logger.debug(f"Pre-caching dialogs notice: {e}")

        channels_cfg = settings_manager.get("channels", {})
        raw_sources = channels_cfg.get("sources", [])
        raw_destinations = channels_cfg.get("destinations", [])
        mappings = channels_cfg.get("mappings", {})

        # Normalize sources
        all_sources = []
        for s in raw_sources:
            all_sources.extend(_split_and_clean_channels(s))
        for s in mappings.keys():
            all_sources.extend(_split_and_clean_channels(s))

        # Normalize destinations
        all_destinations = []
        for d in raw_destinations:
            all_destinations.extend(_split_and_clean_channels(d))
        for dst_list in mappings.values():
            all_destinations.extend(_split_and_clean_channels(dst_list))

        new_resolved_sources = {}
        for src in all_sources:
            entity = await self._resolve_single_source(src)
            if entity:
                new_resolved_sources[src] = entity

        new_resolved_destinations = {}
        for dst in all_destinations:
            entity = await self._resolve_single_destination(dst)
            if entity:
                new_resolved_destinations[dst] = entity

        # Atomic dictionary swap to avoid dropping messages during resolution
        self.resolved_sources = new_resolved_sources
        self.resolved_destinations = new_resolved_destinations
        logger.info(f"✅ Channel resolution complete: {len(self.resolved_sources)} sources, {len(self.resolved_destinations)} destinations ready.")

    async def _get_destinations_for_chat(self, chat_id: int) -> List[Any]:
        """
        Returns list of resolved destination entities for an incoming chat ID.
        Supports multiple destinations, dynamic on-demand resolution, and deduplication.
        """
        channels_cfg = settings_manager.get("channels", {})
        mappings = channels_cfg.get("mappings", {})
        default_destinations = channels_cfg.get("destinations", [])
        configured_sources = channels_cfg.get("sources", [])

        norm_sources = _split_and_clean_channels(configured_sources)
        norm_default_dests = _split_and_clean_channels(default_destinations)

        dest_keys = []

        # 1. Check custom routing mappings first
        for src_key, dst_val in mappings.items():
            cleaned_keys = _split_and_clean_channels(src_key)
            for ck in cleaned_keys:
                resolved_src = self.resolved_sources.get(ck)
                if not resolved_src:
                    for k, ent in self.resolved_sources.items():
                        if _is_chat_match(ck, chat_id, ent):
                            resolved_src = ent
                            break
                if _is_chat_match(ck, chat_id, resolved_src):
                    dest_keys.extend(_split_and_clean_channels(dst_val))

        # 2. Check general sources if no mapping triggered
        if not dest_keys:
            is_monitored = False
            for src in norm_sources:
                resolved_src = self.resolved_sources.get(src)
                if not resolved_src:
                    for k, ent in self.resolved_sources.items():
                        if _is_chat_match(src, chat_id, ent):
                            resolved_src = ent
                            break
                if _is_chat_match(src, chat_id, resolved_src):
                    is_monitored = True
                    break

            if is_monitored:
                dest_keys = norm_default_dests

        # 3. Resolve each destination entity dynamically if missing, and deduplicate by peer ID
        resolved = []
        seen_peers = set()

        for d in dest_keys:
            entity = await self._resolve_single_destination(d)
            if entity:
                try:
                    p_id = utils.get_peer_id(entity)
                except Exception:
                    p_id = getattr(entity, 'id', str(d))
                if p_id not in seen_peers:
                    seen_peers.add(p_id)
                    resolved.append(entity)
            else:
                logger.warning(f"⚠️ Destination '{d}' could not be resolved - skipping this target")

        return resolved

    async def _safe_send(self, action_func, *args, **kwargs):
        delay = float(settings_manager.get("bot", {}).get("action_delay", 2.0))
        for attempt in range(3):
            try:
                res = await action_func(*args, **kwargs)
                if delay > 0:
                    await asyncio.sleep(delay)
                return res
            except errors.FloodWaitError as e:
                logger.warning(f"⚠️ FloodWaitError: Sleeping for {e.seconds}s...")
                await asyncio.sleep(e.seconds + 1)
            except errors.ChatForwardsRestrictedError:
                raise
            except errors.ChatWriteForbiddenError:
                logger.error("❌ Destination write forbidden. Ensure bot/user is admin with post permissions.")
                break
            except Exception as e:
                logger.error(f"❌ Send failed: {e}")
                await asyncio.sleep(2.0)
        return None

    async def _copy_single_message(self, message: Message, dest_entity):
        text = message.raw_text or message.message
        allowed, reason = should_process_message(text)
        settings_manager.increment_stat("total_messages_processed")

        if not allowed:
            settings_manager.increment_stat("total_messages_skipped")
            logger.info(f"⏭️ Skipping message #{message.id} ({reason})")
            return None

        copy_mode = settings_manager.get("bot", {}).get("copy_mode", "clean_copy")
        remove_images = settings_manager.get("bot", {}).get("remove_images", False) or \
                        settings_manager.get("customizations", {}).get("remove_images", False)
        dest_title = getattr(dest_entity, 'title', str(dest_entity))
        logger.info(f"📤 Mirroring message #{message.id} -> '{dest_title}'")

        is_image = is_image_message(message)

        if copy_mode == "forward":
            if remove_images and is_image:
                caption_or_text = transform_text(text)
                if not caption_or_text:
                    settings_manager.increment_stat("total_messages_skipped")
                    logger.info(f"⏭️ Skipping message #{message.id} (Image removed and no text/caption to post)")
                    return None
                remove_links = settings_manager.get("customizations", {}).get("remove_all_urls", False)
                logger.info(f"🖼️ Removed image on message #{message.id} -> posting text caption only")
                sent = await self._safe_send(
                    self.client.send_message,
                    entity=dest_entity,
                    message=caption_or_text,
                    parse_mode='html',
                    link_preview=not remove_links
                )
            else:
                try:
                    sent = await self._safe_send(
                        self.client.forward_messages,
                        entity=dest_entity,
                        messages=message
                    )
                except errors.ChatForwardsRestrictedError:
                    logger.warning(f"⚠️ Source restricts forward on message #{message.id}. Falling back to clean copy...")
                    caption_or_text = transform_text(text)
                    remove_links = settings_manager.get("customizations", {}).get("remove_all_urls", False)
                    if message.media and not isinstance(message.media, (MessageMediaWebPage, MessageMediaPoll, MessageMediaDice, MessageMediaContact, MessageMediaGeo)):
                        sent = await self._safe_send(
                            self.client.send_file,
                            entity=dest_entity,
                            file=message.media,
                            caption=caption_or_text,
                            parse_mode='html'
                        )
                    else:
                        sent = await self._safe_send(
                            self.client.send_message,
                            entity=dest_entity,
                            message=caption_or_text,
                            parse_mode='html',
                            link_preview=not remove_links
                        )
        else:
            caption_or_text = transform_text(text)
            remove_links = settings_manager.get("customizations", {}).get("remove_all_urls", False)

            if remove_images and is_image:
                if not caption_or_text:
                    settings_manager.increment_stat("total_messages_skipped")
                    logger.info(f"⏭️ Skipping message #{message.id} (Image removed and no text/caption to post)")
                    return None
                logger.info(f"🖼️ Removed image on message #{message.id} -> posting text caption only")
                sent = await self._safe_send(
                    self.client.send_message,
                    entity=dest_entity,
                    message=caption_or_text,
                    parse_mode='html',
                    link_preview=not remove_links
                )
            elif message.media and not isinstance(message.media, (MessageMediaWebPage, MessageMediaPoll, MessageMediaDice, MessageMediaContact, MessageMediaGeo)):
                sent = await self._safe_send(
                    self.client.send_file,
                    entity=dest_entity,
                    file=message.media,
                    caption=caption_or_text,
                    parse_mode='html'
                )
            elif isinstance(message.media, MessageMediaPoll):
                sent = await self._safe_send(
                    self.client.send_message,
                    entity=dest_entity,
                    file=message.media
                )
            else:
                if not caption_or_text:
                    return None
                sent = await self._safe_send(
                    self.client.send_message,
                    entity=dest_entity,
                    message=caption_or_text,
                    parse_mode='html',
                    link_preview=not remove_links
                )

        if sent:
            settings_manager.increment_stat("total_messages_mirrored")
            settings_manager.set_stat("last_mirrored_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        return sent

    async def _handle_album_batch(self, grouped_id: int, messages: List[Message]):
        if not messages or not self.client:
            return

        first_msg = messages[0]
        chat_id = first_msg.chat_id
        destinations = await self._get_destinations_for_chat(chat_id)
        if not destinations:
            return

        combined_text = " ".join([m.raw_text or m.message or "" for m in messages if m.raw_text or m.message])
        allowed, reason = should_process_message(combined_text)
        settings_manager.increment_stat("total_messages_processed")

        if not allowed:
            settings_manager.increment_stat("total_messages_skipped")
            logger.info(f"⏭️ Skipping album group {grouped_id} ({reason})")
            return

        main_caption = ""
        for m in messages:
            if m.raw_text or m.message:
                main_caption = m.raw_text or m.message
                break

        transformed_caption = transform_text(main_caption)
        copy_mode = settings_manager.get("bot", {}).get("copy_mode", "clean_copy")
        remove_images = settings_manager.get("bot", {}).get("remove_images", False) or \
                        settings_manager.get("customizations", {}).get("remove_images", False)
        remove_links = settings_manager.get("customizations", {}).get("remove_all_urls", False)

        if remove_images:
            non_image_messages = [m for m in messages if not is_image_message(m)]
            if not non_image_messages:
                # All items were images
                if not transformed_caption:
                    settings_manager.increment_stat("total_messages_skipped")
                    logger.info(f"⏭️ Skipping album group {grouped_id} (All media were images and remove_images is active, no text/caption to post)")
                    return
                for dest in destinations:
                    try:
                        dest_title = getattr(dest, 'title', str(dest))
                        logger.info(f"🖼️ Removed all images from album #{grouped_id} -> posting text caption only to '{dest_title}'")
                        sent = await self._safe_send(
                            self.client.send_message,
                            entity=dest,
                            message=transformed_caption,
                            parse_mode='html',
                            link_preview=not remove_links
                        )
                        if sent:
                            settings_manager.increment_stat("total_messages_mirrored")
                            settings_manager.set_stat("last_mirrored_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                    except Exception as e:
                        logger.error(f"❌ Error sending text-only album to destination: {e}")
                return
            else:
                media_files = [m.media for m in non_image_messages if m.media]
        else:
            media_files = [m.media for m in messages if m.media]

        for dest in destinations:
            try:
                dest_title = getattr(dest, 'title', str(dest))
                logger.info(f"📸 Sending album ({len(media_files)} items) -> '{dest_title}'")
                sent = None
                if copy_mode == "forward":
                    try:
                        sent = await self._safe_send(
                            self.client.forward_messages,
                            entity=dest,
                            messages=[m.id for m in messages],
                            from_peer=chat_id
                        )
                    except errors.ChatForwardsRestrictedError:
                        logger.warning(f"⚠️ Source restricts forward on album #{grouped_id}. Falling back to clean copy...")
                        sent = await self._safe_send(
                            self.client.send_file,
                            entity=dest,
                            file=media_files,
                            caption=transformed_caption,
                            parse_mode='html'
                        )
                else:
                    sent = await self._safe_send(
                        self.client.send_file,
                        entity=dest,
                        file=media_files,
                        caption=transformed_caption,
                        parse_mode='html'
                    )
                if sent:
                    settings_manager.increment_stat("total_messages_mirrored")
                    settings_manager.set_stat("last_mirrored_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            except Exception as e:
                logger.error(f"❌ Error sending album #{grouped_id} to destination {getattr(dest, 'title', str(dest))}: {e}", exc_info=True)

    async def _on_new_message(self, event: events.NewMessage.Event):
        message = event.message
        chat_id = event.chat_id

        destinations = await self._get_destinations_for_chat(chat_id)
        if not destinations:
            return

        if message.grouped_id:
            await self.album_manager.add_message(message)
            return

        for dest in destinations:
            try:
                sent = await self._copy_single_message(message, dest)
                if sent and settings_manager.get("bot", {}).get("mirror_edits", True):
                    key = (chat_id, message.id)
                    if key not in self.message_history_map:
                        self.message_history_map[key] = []
                    sent_id = sent.id if hasattr(sent, 'id') else (sent[0].id if isinstance(sent, list) and sent else None)
                    if sent_id:
                        dest_id = getattr(dest, 'id', None)
                        if dest_id:
                            self.message_history_map[key].append((dest_id, sent_id))
            except Exception as e:
                logger.error(f"❌ Error copying message #{message.id} to destination {getattr(dest, 'title', str(dest))}: {e}", exc_info=True)

    async def _on_message_edited(self, event: events.MessageEdited.Event):
        if not settings_manager.get("bot", {}).get("mirror_edits", True) or not self.client:
            return

        message = event.message
        chat_id = event.chat_id
        key = (chat_id, message.id)

        if key in self.message_history_map:
            new_text = transform_text(message.raw_text or message.message)
            for dest_id, dest_msg_id in self.message_history_map[key]:
                try:
                    logger.info(f"✏️ Syncing edit for message #{message.id} -> dest #{dest_msg_id}")
                    await self.client.edit_message(
                        entity=dest_id,
                        message=dest_msg_id,
                        text=new_text,
                        parse_mode='html'
                    )
                except Exception as e:
                    logger.warning(f"Could not sync edit for message #{dest_msg_id}: {e}")

    async def fetch_user_dialogs(self) -> List[Dict[str, Any]]:
        """Queries Telegram for channels/groups the bot/user is part of."""
        if not self.client or not self.client.is_connected():
            return []

        dialogs_list = []
        try:
            async for dialog in self.client.iter_dialogs(limit=100):
                if dialog.is_channel or dialog.is_group:
                    username = f"@{dialog.entity.username}" if hasattr(dialog.entity, 'username') and dialog.entity.username else None
                    dialogs_list.append({
                        "id": dialog.id,
                        "title": dialog.title,
                        "username": username,
                        "is_channel": dialog.is_channel,
                        "is_group": dialog.is_group,
                        "unread_count": dialog.unread_count
                    })
        except Exception as e:
            logger.error(f"Error fetching dialogs: {e}")
        return dialogs_list

bot_engine = BotEngine()
