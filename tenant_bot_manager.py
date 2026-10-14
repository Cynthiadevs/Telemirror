import os
import re
import json
import asyncio
import logging
from datetime import datetime
from collections import deque
from typing import Dict, List, Any, Optional, Set

from telethon import TelegramClient, events, errors, utils
from telethon.sessions import StringSession
from telethon.tl.types import (
    MessageMediaPoll,
    MessageMediaWebPage,
    MessageMediaDice,
    MessageMediaContact,
    MessageMediaGeo
)

from database import db
from auth_manager import auth_manager
from cleaner import should_process_message, transform_text, is_image_message
from album_manager import AlbumManager

logger = logging.getLogger("TenantBotManager")

class TenantBotInstance:
    """Represents an isolated, running Telethon userbot instance for a single user."""
    def __init__(self, user_id: int):
        self.user_id = user_id
        self.client: Optional[TelegramClient] = None
        self.status: str = "stopped"  # "stopped", "starting", "running", "error"
        self.error_message: str = ""
        self.resolved_sources: List[Any] = []
        self.resolved_destinations: List[Any] = []
        self.resolved_mappings: Dict[Any, List[Any]] = {}
        self.album_manager: Optional[AlbumManager] = None
        self.logs: deque = deque(maxlen=200)
        self.log_subscribers: Set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    def log(self, level: str, message: str):
        now_str = datetime.now().strftime("%H:%M:%S")
        entry = {"time": now_str, "level": level, "message": message}
        self.logs.append(entry)
        for q in list(self.log_subscribers):
            try:
                q.put_nowait(entry)
            except Exception:
                pass

    def get_db_record(self) -> Optional[Dict[str, Any]]:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_bots WHERE user_id = ?", (self.user_id,))
            row = cursor.fetchone()
            if not row:
                return None
            data = dict(row)
            try:
                data["config"] = json.loads(data.get("config_json") or "{}")
            except Exception:
                data["config"] = {}
            try:
                data["stats"] = json.loads(data.get("stats_json") or "{}")
            except Exception:
                data["stats"] = {}
            return data

    def update_stats(self, processed: int = 0, mirrored: int = 0, skipped: int = 0):
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT stats_json FROM user_bots WHERE user_id = ?", (self.user_id,))
            row = cursor.fetchone()
            stats = {}
            if row and row["stats_json"]:
                try:
                    stats = json.loads(row["stats_json"])
                except Exception:
                    stats = {}

            stats["total_messages_processed"] = stats.get("total_messages_processed", 0) + processed
            stats["total_messages_mirrored"] = stats.get("total_messages_mirrored", 0) + mirrored
            stats["total_messages_skipped"] = stats.get("total_messages_skipped", 0) + skipped
            if mirrored > 0:
                stats["last_mirrored_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("UPDATE user_bots SET stats_json = ?, updated_at = ? WHERE user_id = ?", (json.dumps(stats), now, self.user_id))
            conn.commit()

    async def _resolve_channels(self):
        """Resolves source and destination channels from database configuration."""
        data = self.get_db_record()
        if not data:
            return

        cfg = data.get("config", {})
        channels = cfg.get("channels", {})
        sources = channels.get("sources", [])
        destinations = channels.get("destinations", [])
        mappings = channels.get("mappings", {})

        self.resolved_sources = []
        self.resolved_destinations = []
        self.resolved_mappings = {}

        if not self.client or not self.client.is_connected():
            return

        async def resolve_entity(ident: Any):
            ident_str = str(ident).strip()
            if not ident_str:
                return None
            try:
                if (ident_str.startswith("-") or ident_str.isdigit()) and not ident_str.startswith("@"):
                    val = int(ident_str)
                    return await self.client.get_input_entity(val)
                return await self.client.get_input_entity(ident_str)
            except Exception as e:
                self.log("WARN", f"Could not resolve channel '{ident_str}': {e}")
                return None

        # Resolve sources
        for s in sources:
            ent = await resolve_entity(s)
            if ent:
                self.resolved_sources.append(ent)
                self.log("INFO", f"Resolved source channel: {s}")

        # Resolve destinations
        for d in destinations:
            ent = await resolve_entity(d)
            if ent:
                self.resolved_destinations.append(ent)
                self.log("INFO", f"Resolved destination channel: {d}")

        # Resolve mappings
        for src, dests in mappings.items():
            s_ent = await resolve_entity(src)
            if s_ent:
                d_ents = []
                dest_list = dests if isinstance(dests, list) else [dests]
                for d in dest_list:
                    d_ent = await resolve_entity(d)
                    if d_ent:
                        d_ents.append(d_ent)
                if d_ents:
                    self.resolved_mappings[s_ent] = d_ents

    async def start(self) -> bool:
        async with self._lock:
            if self.status == "running":
                return True

            # Verify active subscription
            sub = auth_manager.check_user_subscription(self.user_id)
            if not sub.get("is_subscribed"):
                self.status = "error"
                self.error_message = f"Subscription required: {sub.get('message', 'Inactive')}"
                self.log("ERROR", self.error_message)
                return False

            data = self.get_db_record()
            if not data:
                self.status = "error"
                self.error_message = "User bot record not found."
                return False

            session_str = data.get("session_string")
            bot_token = data.get("bot_token")
            api_id_val = data.get("api_id")
            api_hash_val = data.get("api_hash")

            if not (session_str or bot_token):
                self.status = "error"
                self.error_message = "Telegram session string or bot token is missing. Please complete onboarding."
                self.log("ERROR", self.error_message)
                return False

            if not (api_id_val and api_hash_val):
                self.status = "error"
                self.error_message = "Telegram API ID and API Hash are required."
                self.log("ERROR", self.error_message)
                return False

            try:
                self.status = "starting"
                self.log("INFO", "Initializing Telegram client...")
                api_id = int(str(api_id_val).strip())
                api_hash = str(api_hash_val).strip()

                session = StringSession(session_str) if session_str else StringSession()
                self.client = TelegramClient(session, api_id, api_hash)

                if bot_token:
                    await self.client.start(bot_token=bot_token)
                else:
                    await self.client.start()

                me = await self.client.get_me()
                username = f"@{me.username}" if me.username else me.first_name
                self.log("SUCCESS", f"Connected as {username} (ID: {me.id})")

                await self._resolve_channels()
                self.album_manager = AlbumManager(
                    client=self.client,
                    transform_fn=transform_text,
                    should_process_fn=should_process_message,
                    get_targets_fn=self._get_targets_for_message,
                    custom_settings=data.get("config", {}).get("customizations", {}),
                    copy_mode="clean_copy",
                    logger=logger
                )

                self._register_event_handlers()
                self.status = "running"
                self.error_message = ""

                # Save status in DB
                with db.get_connection() as conn:
                    conn.execute("UPDATE user_bots SET status = 'running', error_message = '' WHERE user_id = ?", (self.user_id,))
                    conn.commit()

                self.log("SUCCESS", f"Mirror engine is ACTIVE. Listening on {len(self.resolved_sources)} sources.")
                return True

            except Exception as e:
                self.status = "error"
                self.error_message = str(e)
                self.log("ERROR", f"Failed to start bot engine: {e}")
                with db.get_connection() as conn:
                    conn.execute("UPDATE user_bots SET status = 'error', error_message = ? WHERE user_id = ?", (str(e), self.user_id))
                    conn.commit()
                return False

    def _get_targets_for_message(self, message: Any) -> List[Any]:
        # Check specific mappings first
        for s_ent, d_ents in self.resolved_mappings.items():
            try:
                if message.chat_id == utils.get_peer_id(s_ent):
                    return d_ents
            except Exception:
                pass
        return self.resolved_destinations

    def _register_event_handlers(self):
        if not self.client:
            return

        @self.client.on(events.NewMessage)
        async def on_new_message(event):
            try:
                if not self.resolved_sources:
                    return

                # Check if chat matches any resolved source
                matches = False
                for src in self.resolved_sources:
                    try:
                        if event.chat_id == utils.get_peer_id(src):
                            matches = True
                            break
                    except Exception:
                        pass
                if not matches:
                    return

                self.update_stats(processed=1)
                data = self.get_db_record()
                cust = data.get("config", {}).get("customizations", {}) if data else {}

                # Filter checks
                text = event.raw_text or ""
                allowed, reason = should_process_message(text, cust)
                if not allowed:
                    self.update_stats(skipped=1)
                    self.log("INFO", f"Filtered message: {reason}")
                    return

                # If album message
                if event.grouped_id:
                    if self.album_manager:
                        await self.album_manager.handle_album_message(event.message)
                    return

                # Normal single message
                targets = self._get_targets_for_message(event.message)
                if not targets:
                    self.update_stats(skipped=1)
                    self.log("WARN", "No destination channels configured for this source.")
                    return

                new_text = transform_text(text, cust)
                remove_images = cust.get("remove_images", False)
                has_image = is_image_message(event.message)

                for dest in targets:
                    try:
                        if has_image and remove_images:
                            if new_text:
                                await self.client.send_message(dest, new_text)
                        elif event.media:
                            await self.client.send_file(dest, event.media, caption=new_text)
                        elif new_text:
                            await self.client.send_message(dest, new_text)
                        self.update_stats(mirrored=1)
                        self.log("SUCCESS", f"Mirrored message to {dest}")
                    except Exception as ex:
                        self.log("ERROR", f"Failed to mirror to destination: {ex}")

            except Exception as e:
                self.log("ERROR", f"Error processing incoming message: {e}")

    async def stop(self) -> bool:
        async with self._lock:
            if self.status == "stopped":
                return True
            try:
                if self.client and self.client.is_connected():
                    await self.client.disconnect()
            except Exception:
                pass
            self.status = "stopped"
            with db.get_connection() as conn:
                conn.execute("UPDATE user_bots SET status = 'stopped' WHERE user_id = ?", (self.user_id,))
                conn.commit()
            self.log("INFO", "Bot engine stopped.")
            return True

    async def restart(self) -> bool:
        await self.stop()
        await asyncio.sleep(0.5)
        return await self.start()

    def get_status(self) -> Dict[str, Any]:
        data = self.get_db_record() or {}
        cfg = data.get("config", {})
        channels = cfg.get("channels", {})
        return {
            "status": self.status,
            "error_message": self.error_message,
            "sources_count": len(channels.get("sources", [])),
            "destinations_count": len(channels.get("destinations", [])),
            "sources": channels.get("sources", []),
            "destinations": channels.get("destinations", []),
            "stats": data.get("stats", {})
        }

    async def fetch_dialogs(self) -> List[Dict[str, Any]]:
        """Discovers Telegram channels and chats the user has access to."""
        client_to_use = self.client
        temp_client = None

        if not client_to_use or not client_to_use.is_connected():
            data = self.get_db_record()
            session_str = data.get("session_string") if data else None
            bot_token = data.get("bot_token") if data else None
            api_id_val = data.get("api_id") if data else None
            api_hash_val = data.get("api_hash") if data else None

            if not (session_str or bot_token) or not (api_id_val and api_hash_val):
                from settings_manager import settings_manager
                tg_cfg = settings_manager.get("telegram", {})
                session_str = session_str or tg_cfg.get("session_string")
                api_id_val = api_id_val or tg_cfg.get("api_id")
                api_hash_val = api_hash_val or tg_cfg.get("api_hash")
                bot_token = bot_token or tg_cfg.get("bot_token")

            if (session_str or bot_token) and api_id_val and api_hash_val:
                try:
                    api_id = int(str(api_id_val).strip())
                    api_hash = str(api_hash_val).strip()
                    session = StringSession(session_str) if session_str else StringSession()
                    temp_client = TelegramClient(session, api_id, api_hash)
                    await asyncio.wait_for(temp_client.connect(), timeout=8.0)
                    if bot_token:
                        if not await temp_client.is_user_authorized():
                            await asyncio.wait_for(temp_client.sign_in(bot_token=bot_token), timeout=8.0)
                    else:
                        if not await temp_client.is_user_authorized():
                            self.log("WARN", "Telegram session is not authorized to fetch dialogs.")
                            return []
                    client_to_use = temp_client
                    if not self.client:
                        self.client = temp_client
                except Exception as e:
                    self.log("ERROR", f"Could not connect client to fetch dialogs: {e}")
                    return []
            else:
                return []

        if not client_to_use or not client_to_use.is_connected():
            return []

        dialogs = []
        try:
            async for d in client_to_use.iter_dialogs(limit=150):
                if d.is_channel or d.is_group:
                    ent = d.entity
                    username = getattr(ent, 'username', None)
                    is_creator = getattr(ent, 'creator', False)
                    admin_rights = getattr(ent, 'admin_rights', None)
                    is_admin = bool(is_creator or (admin_rights is not None))
                    can_post = bool(
                        is_creator
                        or (admin_rights and getattr(admin_rights, 'post_messages', False))
                        or (admin_rights and getattr(admin_rights, 'send_messages', False))
                        or (d.is_group and not getattr(ent, 'broadcast', False))
                    )
                    dialogs.append({
                        "id": d.id,
                        "title": d.title or "Untitled Channel",
                        "username": f"@{username}" if username else None,
                        "identifier": f"@{username}" if username else str(d.id),
                        "is_channel": bool(d.is_channel),
                        "is_group": bool(d.is_group),
                        "is_broadcast": bool(getattr(ent, 'broadcast', False)),
                        "is_creator": bool(is_creator),
                        "is_admin": is_admin,
                        "can_post": can_post,
                        "unread_count": d.unread_count or 0
                    })
        except Exception as e:
            self.log("ERROR", f"Error fetching dialogs: {e}")
        return dialogs

class TenantBotManager:
    def __init__(self):
        self.instances: Dict[int, TenantBotInstance] = {}

    def get_bot(self, user_id: int) -> TenantBotInstance:
        if user_id not in self.instances:
            self.instances[user_id] = TenantBotInstance(user_id)
        return self.instances[user_id]

    async def start_bot(self, user_id: int) -> Tuple[bool, str]:
        bot = self.get_bot(user_id)
        success = await bot.start()
        if success:
            return True, "Bot engine started successfully!"
        return False, bot.error_message or "Failed to start bot engine."

    async def stop_bot(self, user_id: int) -> bool:
        bot = self.get_bot(user_id)
        return await bot.stop()

    async def restart_bot(self, user_id: int) -> Tuple[bool, str]:
        bot = self.get_bot(user_id)
        success = await bot.restart()
        if success:
            return True, "Bot engine restarted successfully!"
        return False, bot.error_message or "Failed to restart bot engine."

    def get_status(self, user_id: int) -> Dict[str, Any]:
        bot = self.get_bot(user_id)
        return bot.get_status()

    async def fetch_dialogs(self, user_id: int) -> List[Dict[str, Any]]:
        bot = self.get_bot(user_id)
        return await bot.fetch_dialogs()

    async def fetch_dialogs_status(self, user_id: int) -> Dict[str, Any]:
        bot = self.get_bot(user_id)
        data = bot.get_db_record() or {}
        has_session = bool(data.get("session_string") or data.get("bot_token"))
        if not has_session:
            from settings_manager import settings_manager
            tg_cfg = settings_manager.get("telegram", {})
            has_session = bool(tg_cfg.get("session_string") or tg_cfg.get("bot_token"))

        if not has_session:
            return {
                "success": False,
                "connected": False,
                "error": "No Telegram account connected yet. Please connect your Telegram account in the Setup Wizard.",
                "dialogs": []
            }

        dialogs = await bot.fetch_dialogs()
        return {
            "success": True,
            "connected": True,
            "dialogs": dialogs,
            "count": len(dialogs)
        }

    def get_user_config(self, user_id: int) -> Dict[str, Any]:
        data = self.get_bot(user_id).get_db_record()
        return data.get("config", {}) if data else {}

    async def save_user_config(self, user_id: int, new_config: Dict[str, Any]) -> Tuple[bool, str]:
        # Enforce plan channel limit (number of source channels to copy from)
        user = auth_manager.get_user_by_id(user_id)
        if user and user.get("role") != "admin":
            sub = auth_manager.check_user_subscription(user_id)
            channel_limit = int(sub.get("channel_limit", 1))
            plan_name = sub.get("plan_name", "Current Plan")
            sources = new_config.get("channels", {}).get("sources", [])
            if len(sources) > channel_limit:
                return False, f"Plan limit reached: Your '{plan_name}' allows copying from up to {channel_limit} source channel(s). You have {len(sources)}. Please upgrade your plan to add more channels."

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with db.get_connection() as conn:
            conn.execute("UPDATE user_bots SET config_json = ?, updated_at = ? WHERE user_id = ?", (json.dumps(new_config), now, user_id))
            conn.commit()

        bot = self.get_bot(user_id)
        if bot.status == "running" and bot.client:
            asyncio.create_task(bot._resolve_channels())

        return True, "Settings updated and synced successfully!"

tenant_bot_manager = TenantBotManager()
