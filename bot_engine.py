import asyncio
import logging
from datetime import datetime
from collections import deque
from typing import Dict, List, Any, Optional, Union

from telethon import TelegramClient, events, errors
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
from cleaner import should_process_message, transform_text
from album_manager import AlbumManager

# ==============================================================================
# Live Log Handler for Admin Dashboard
# ==============================================================================
class LogBroadcaster(logging.Handler):
    def __init__(self, maxlen: int = 200):
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
# Bot Engine
# ==============================================================================
class BotEngine:
    def __init__(self):
        self.status = "stopped"  # "stopped", "starting", "running", "error"
        self.error_message: Optional[str] = None
        self.client: Optional[TelegramClient] = None
        self.user_info: Optional[Dict[str, Any]] = None
        self.resolved_sources: Dict[Any, Any] = {}
        self.resolved_destinations: Dict[Any, Any] = {}
        self.message_history_map: Dict[tuple, List[tuple]] = {}
        self.album_manager = AlbumManager(callback=self._handle_album_batch)
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

        # Try from local settings or disk session file
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
        return {
            "status": self.status,
            "error_message": self.error_message,
            "user_info": self.user_info,
            "sources_count": len(self.resolved_sources),
            "destinations_count": len(self.resolved_destinations),
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

            tg_cfg = settings_manager.get("telegram", {})
            api_id_val = tg_cfg.get("api_id")
            api_hash = tg_cfg.get("api_hash", "").strip()
            phone = tg_cfg.get("phone_number", "").strip()
            session_string = (tg_cfg.get("session_string") or os.getenv("SESSION_STRING", "")).strip()
            session_name = tg_cfg.get("session_name", "telegram_mirror_session").strip()

            if not api_id_val or not api_hash:
                self.status = "error"
                self.error_message = "Telegram API ID or API Hash is missing."
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
                # Prioritize StringSession if present (standard for Render / Cloud headless deployments)
                if session_string:
                    logger.info("🔑 Initializing TelegramClient with StringSession (Headless Cloud Mode)...")
                    session = StringSession(session_string)
                else:
                    logger.info(f"📁 Initializing TelegramClient with session file '{session_name}'...")
                    session = session_name

                self.client = TelegramClient(session, api_id, api_hash)
                
                # Check if authorized or start
                await self.client.connect()
                if not await self.client.is_user_authorized():
                    self.status = "error"
                    self.error_message = "Session not authenticated. For Render deployment, set SESSION_STRING in Environment Variables or export from active session."
                    logger.warning(f"⚠️ {self.error_message}")
                    await self.client.disconnect()
                    return False

                me = await self.client.get_me()
                self.user_info = {
                    "id": me.id,
                    "first_name": me.first_name,
                    "username": me.username,
                    "phone": me.phone
                }
                logger.info(f"✅ Connected as: {me.first_name} (@{me.username or 'NoUsername'})")

                # Resolve configured channels
                await self._resolve_channels()

                # Register event handlers
                self.client.add_event_handler(self._on_new_message, events.NewMessage)
                self.client.add_event_handler(self._on_message_edited, events.MessageEdited)

                self.status = "running"
                logger.info("✨ Bot Engine is active and listening for messages.")
                return True

            except Exception as e:
                self.status = "error"
                self.error_message = str(e)
                logger.error(f"❌ Error starting bot: {e}", exc_info=True)
                if self.client and self.client.is_connected():
                    await self.client.disconnect()
                return False

    async def stop(self) -> bool:
        async with self._lock:
            logger.info("🛑 Stopping Bot Engine...")
            try:
                if self.client and self.client.is_connected():
                    await self.client.disconnect()
            except Exception as e:
                logger.error(f"Error disconnecting client: {e}")
            
            self.status = "stopped"
            self.error_message = None
            self.resolved_sources.clear()
            self.resolved_destinations.clear()
            logger.info("👋 Bot Engine stopped.")
            return True

    async def restart(self) -> bool:
        await self.stop()
        await asyncio.sleep(1)
        return await self.start()

    async def _resolve_channels(self):
        self.resolved_sources.clear()
        self.resolved_destinations.clear()

        channels_cfg = settings_manager.get("channels", {})
        sources = channels_cfg.get("sources", [])
        destinations = channels_cfg.get("destinations", [])
        mappings = channels_cfg.get("mappings", {})

        # Resolve all sources
        all_sources = set(sources)
        for s in mappings.keys():
            all_sources.add(s)

        for src in all_sources:
            try:
                clean_src = int(src) if str(src).lstrip("-").isdigit() else src
                entity = await self.client.get_entity(clean_src)
                self.resolved_sources[clean_src] = entity
                logger.info(f"📥 Source channel resolved: {getattr(entity, 'title', str(src))} ({getattr(entity, 'id', '')})")
            except Exception as e:
                logger.warning(f"Could not resolve source channel '{src}': {e}")

        # Resolve all destinations
        all_destinations = set(destinations)
        for dst_list in mappings.values():
            if isinstance(dst_list, list):
                for d in dst_list:
                    all_destinations.add(d)
            elif isinstance(dst_list, (str, int)):
                all_destinations.add(dst_list)

        for dst in all_destinations:
            try:
                clean_dst = int(dst) if str(dst).lstrip("-").isdigit() else dst
                entity = await self.client.get_entity(clean_dst)
                self.resolved_destinations[clean_dst] = entity
                logger.info(f"📤 Destination channel resolved: {getattr(entity, 'title', str(dst))} ({getattr(entity, 'id', '')})")
            except Exception as e:
                logger.warning(f"Could not resolve destination channel '{dst}': {e}")

    def _get_destinations_for_chat(self, chat_id: int) -> List[Any]:
        channels_cfg = settings_manager.get("channels", {})
        mappings = channels_cfg.get("mappings", {})
        default_destinations = channels_cfg.get("destinations", [])

        dest_keys = []
        # Check specific mapping for numeric ID or prefixed
        for src_key, dst_val in mappings.items():
            clean_key = int(src_key) if str(src_key).lstrip("-").isdigit() else src_key
            if clean_key == chat_id or (clean_key in self.resolved_sources and getattr(self.resolved_sources[clean_key], 'id', None) == chat_id):
                if isinstance(dst_val, list):
                    dest_keys.extend(dst_val)
                else:
                    dest_keys.append(dst_val)

        # If no explicit mapping, fallback to default destinations if source is in sources list
        if not dest_keys:
            is_monitored = False
            for src in channels_cfg.get("sources", []):
                clean_src = int(src) if str(src).lstrip("-").isdigit() else src
                if clean_src == chat_id:
                    is_monitored = True
                    break
                if clean_src in self.resolved_sources and getattr(self.resolved_sources[clean_src], 'id', None) == chat_id:
                    is_monitored = True
                    break

            if is_monitored:
                dest_keys = default_destinations

        resolved = []
        for d in dest_keys:
            clean_d = int(d) if str(d).lstrip("-").isdigit() else d
            if clean_d in self.resolved_destinations:
                resolved.append(self.resolved_destinations[clean_d])
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
            except errors.ChatWriteForbiddenError:
                logger.error("❌ Destination write forbidden. Ensure user is admin with post permissions.")
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
        dest_title = getattr(dest_entity, 'title', str(dest_entity))
        logger.info(f"📤 Mirroring message #{message.id} -> '{dest_title}'")

        if copy_mode == "forward":
            sent = await self._safe_send(
                self.client.forward_messages,
                entity=dest_entity,
                messages=message
            )
        else:
            caption_or_text = transform_text(text)
            if message.media and not isinstance(message.media, (MessageMediaWebPage, MessageMediaPoll, MessageMediaDice, MessageMediaContact, MessageMediaGeo)):
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
                remove_links = settings_manager.get("customizations", {}).get("remove_all_urls", False)
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
        if not messages:
            return

        first_msg = messages[0]
        chat_id = first_msg.chat_id
        destinations = self._get_destinations_for_chat(chat_id)
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
        media_files = [m.media for m in messages if m.media]
        copy_mode = settings_manager.get("bot", {}).get("copy_mode", "clean_copy")

        for dest in destinations:
            dest_title = getattr(dest, 'title', str(dest))
            logger.info(f"📸 Sending album ({len(media_files)} items) -> '{dest_title}'")
            if copy_mode == "forward":
                await self._safe_send(
                    self.client.forward_messages,
                    entity=dest,
                    messages=[m.id for m in messages],
                    from_peer=chat_id
                )
            else:
                await self._safe_send(
                    self.client.send_file,
                    entity=dest,
                    file=media_files,
                    caption=transformed_caption,
                    parse_mode='html'
                )
            settings_manager.increment_stat("total_messages_mirrored")
            settings_manager.set_stat("last_mirrored_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    async def _on_new_message(self, event: events.NewMessage.Event):
        message = event.message
        chat_id = event.chat_id

        destinations = self._get_destinations_for_chat(chat_id)
        if not destinations:
            return

        if message.grouped_id:
            await self.album_manager.add_message(message)
            return

        for dest in destinations:
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

    async def _on_message_edited(self, event: events.MessageEdited.Event):
        if not settings_manager.get("bot", {}).get("mirror_edits", True):
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
        """Queries Telegram for channels/groups the user account is part of."""
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
