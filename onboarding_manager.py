import json
import logging
import asyncio
from datetime import datetime
from typing import Dict, Any, Tuple, Optional

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    SessionPasswordNeededError,
    PhoneCodeInvalidError,
    PhoneCodeExpiredError,
    PasswordHashInvalidError,
    FloodWaitError,
    ApiIdInvalidError,
    PhoneNumberInvalidError
)

from database import db

logger = logging.getLogger("OnboardingManager")

# In-memory dictionary holding active Telethon clients during the OTP sign-in phase
# Format: user_id -> {"client": TelegramClient, "phone": str, "phone_code_hash": str, "api_id": int, "api_hash": str, "created_at": datetime}
ACTIVE_ONBOARDING_SESSIONS: Dict[int, Dict[str, Any]] = {}

class OnboardingManager:
    def get_user_bot_data(self, user_id: int) -> Optional[Dict[str, Any]]:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM user_bots WHERE user_id = ?", (user_id,))
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

    def get_onboarding_status(self, user_id: int) -> Dict[str, Any]:
        data = self.get_user_bot_data(user_id)
        if not data:
            return {
                "is_onboarded": False,
                "step": "credentials",
                "has_credentials": False,
                "has_telegram": False,
                "phone_number": "",
                "api_id": "",
                "bot_token": False,
                "status": "stopped",
                "config": {},
                "telegram_account": {}
            }

        has_session = bool(data.get("session_string"))
        has_creds = bool((data.get("api_id") and data.get("api_hash") and data.get("session_string")) or data.get("bot_token"))
        cfg = data.get("config", {})
        tg_info = cfg.get("telegram_account", {})
        phone = data.get("phone_number") or (tg_info.get("phone") if tg_info else "") or ""

        if (has_session or has_creds) and not tg_info:
            tg_info = {
                "phone": phone,
                "username": "",
                "first_name": ""
            }

        return {
            "is_onboarded": bool(data.get("is_onboarded", 0)),
            "step": data.get("onboarding_step", "credentials"),
            "has_credentials": has_creds,
            "has_telegram": has_session or has_creds,
            "phone_number": phone,
            "api_id": data.get("api_id") or "",
            "bot_token": bool(data.get("bot_token")),
            "status": data.get("status", "stopped"),
            "config": cfg,
            "telegram_account": tg_info
        }

    # =========================================================================
    # Step 1: Initialize Telegram Client & Send OTP Code
    # =========================================================================
    async def request_telegram_code(self, user_id: int, api_id_str: str, api_hash: str, phone_number: str) -> Tuple[bool, str, Dict[str, Any]]:
        """Connects to Telegram API and requests an SMS / Telegram app login OTP code."""
        try:
            api_id = int(str(api_id_str).strip())
        except (ValueError, TypeError):
            return False, "API ID must be a valid numeric integer.", {}

        api_hash = str(api_hash).strip()
        phone_number = str(phone_number).strip().replace(" ", "").replace("-", "")

        if not api_hash or len(api_hash) < 10:
            return False, "API Hash is invalid.", {}
        if not phone_number.startswith("+"):
            phone_number = "+" + phone_number

        # Disconnect any old pending session for this user
        if user_id in ACTIVE_ONBOARDING_SESSIONS:
            try:
                old_client = ACTIVE_ONBOARDING_SESSIONS[user_id].get("client")
                if old_client and old_client.is_connected():
                    await old_client.disconnect()
            except Exception:
                pass
            del ACTIVE_ONBOARDING_SESSIONS[user_id]

        try:
            # Create fresh Telethon client
            session = StringSession()
            client = TelegramClient(session, api_id, api_hash)
            await client.connect()

            send_code_result = await client.send_code_request(phone_number)
            phone_code_hash = send_code_result.phone_code_hash

            # Store in active memory
            ACTIVE_ONBOARDING_SESSIONS[user_id] = {
                "client": client,
                "phone": phone_number,
                "phone_code_hash": phone_code_hash,
                "api_id": api_id,
                "api_hash": api_hash,
                "created_at": datetime.utcnow()
            }

            # Update step in DB
            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                UPDATE user_bots
                SET api_id = ?, api_hash = ?, phone_number = ?, onboarding_step = 'otp', updated_at = ?
                WHERE user_id = ?
                """, (str(api_id), api_hash, phone_number, now, user_id))
                conn.commit()

            logger.info(f"📲 Telegram OTP sent successfully to {phone_number} for user {user_id}")
            return True, f"Login code successfully sent to {phone_number} via Telegram!", {
                "step": "otp",
                "phone_number": phone_number,
                "phone_code_hash": phone_code_hash
            }

        except ApiIdInvalidError:
            return False, "The API ID or API Hash provided is invalid. Please check your credentials at my.telegram.org.", {}
        except PhoneNumberInvalidError:
            return False, "The phone number is invalid. Ensure it includes the international country code (e.g. +234...).", {}
        except FloodWaitError as e:
            return False, f"Telegram FloodWait: Please wait {e.seconds} seconds before trying again.", {}
        except Exception as e:
            logger.error(f"Error sending Telegram code: {e}")
            return False, f"Could not connect to Telegram: {str(e)}", {}

    # =========================================================================
    # Step 2: Verify Code and optional 2FA Password
    # =========================================================================
    async def verify_telegram_code(self, user_id: int, code: str, password_2fa: Optional[str] = None) -> Tuple[bool, str, Dict[str, Any]]:
        """Verifies the Telegram code or 2FA password and generates the persistent session string."""
        if user_id not in ACTIVE_ONBOARDING_SESSIONS:
            return False, "Login session expired. Please re-enter your credentials and request a new code.", {"step": "credentials"}

        session_info = ACTIVE_ONBOARDING_SESSIONS[user_id]
        client: TelegramClient = session_info["client"]
        phone: str = session_info["phone"]
        phone_code_hash: str = session_info["phone_code_hash"]

        code = str(code).strip()

        try:
            if not client.is_connected():
                await client.connect()

            # If 2FA password provided directly
            if password_2fa:
                await client.sign_in(password=password_2fa.strip())
            else:
                try:
                    await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
                except SessionPasswordNeededError:
                    return False, "2-Step Verification (2FA) is enabled on this Telegram account. Please provide your 2FA password.", {
                        "step": "2fa",
                        "needs_2fa": True
                    }

            # Account is now authenticated!
            session_string = client.session.save()
            me = await client.get_me()
            tg_username = f"@{me.username}" if (me and me.username) else ((me.first_name if me else "") or "Telegram User")
            first_name = me.first_name if me else ""
            tg_id = me.id if me else None

            await client.disconnect()
            del ACTIVE_ONBOARDING_SESSIONS[user_id]

            # Save session string and telegram_account into user_bots
            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT config_json FROM user_bots WHERE user_id = ?", (user_id,))
                row = cursor.fetchone()
                cfg = json.loads(row["config_json"]) if (row and row["config_json"]) else {}
                cfg["telegram_account"] = {
                    "username": tg_username,
                    "first_name": first_name,
                    "phone": phone,
                    "telegram_id": tg_id
                }
                cursor.execute("""
                UPDATE user_bots
                SET session_string = ?, onboarding_step = 'customizer', config_json = ?, updated_at = ?
                WHERE user_id = ?
                """, (session_string, json.dumps(cfg), now, user_id))
                conn.commit()

            logger.info(f"✅ Telegram account verified for user {user_id} ({tg_username})!")
            return True, f"Telegram account connected successfully as {tg_username}!", {
                "step": "customizer",
                "tg_username": tg_username
            }

        except PhoneCodeInvalidError:
            return False, "The Telegram verification code you entered is incorrect. Please try again.", {"step": "otp"}
        except PhoneCodeExpiredError:
            return False, "The Telegram code has expired. Please request a new code.", {"step": "credentials"}
        except PasswordHashInvalidError:
            return False, "Incorrect 2FA password. Please check your password and try again.", {"step": "2fa", "needs_2fa": True}
        except Exception as e:
            logger.error(f"Error signing in to Telegram: {e}")
            return False, f"Failed to verify Telegram account: {str(e)}", {"step": "otp"}

    # =========================================================================
    # Step 2.5: Remove / Disconnect Telegram Account
    # =========================================================================
    async def remove_telegram_account(self, user_id: int) -> Tuple[bool, str, Dict[str, Any]]:
        """Removes the connected Telegram account, stops any active bot instance, and resets credentials."""
        try:
            # 1. Disconnect any active in-memory onboarding session
            if user_id in ACTIVE_ONBOARDING_SESSIONS:
                try:
                    c = ACTIVE_ONBOARDING_SESSIONS[user_id].get("client")
                    if c and c.is_connected():
                        await c.disconnect()
                except Exception:
                    pass
                del ACTIVE_ONBOARDING_SESSIONS[user_id]

            # 2. Stop running tenant bot instance if active
            try:
                from tenant_bot_manager import tenant_bot_manager
                await tenant_bot_manager.stop_bot(user_id)
                if user_id in tenant_bot_manager.instances:
                    inst = tenant_bot_manager.instances[user_id]
                    try:
                        if inst.client and inst.client.is_connected():
                            await inst.client.disconnect()
                    except Exception:
                        pass
                    inst.client = None
                    inst.status = "stopped"
            except Exception as e:
                logger.warning(f"Notice stopping tenant bot during account removal: {e}")

            # 3. Clean user_bots record in database
            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            with db.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT config_json FROM user_bots WHERE user_id = ?", (user_id,))
                row = cursor.fetchone()
                cfg = json.loads(row["config_json"]) if (row and row["config_json"]) else {}
                cfg.pop("telegram_account", None)

                cursor.execute("""
                UPDATE user_bots
                SET session_string = NULL,
                    phone_number = NULL,
                    api_id = NULL,
                    api_hash = NULL,
                    bot_token = NULL,
                    is_onboarded = 0,
                    onboarding_step = 'credentials',
                    status = 'stopped',
                    error_message = NULL,
                    config_json = ?,
                    updated_at = ?
                WHERE user_id = ?
                """, (json.dumps(cfg), now, user_id))
                conn.commit()

            logger.info(f"🗑️ Telegram account removed and reset for user {user_id}")
            return True, "Telegram account removed successfully. You can now connect a new Telegram account.", {
                "step": "credentials",
                "has_credentials": False,
                "has_telegram": False
            }
        except Exception as e:
            logger.error(f"Error removing telegram account for user {user_id}: {e}")
            return False, f"Failed to remove Telegram account: {str(e)}", {}

    # =========================================================================
    # Step 3: Configure Message Customizer & Promotion Engine
    # =========================================================================
    def setup_customizer_and_engine(self, user_id: int, setup_data: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        """Saves message customization rules, channel mappings, and promotional engine setup."""
        data = self.get_user_bot_data(user_id)
        if not data:
            return False, "User bot profile not found.", {}

        cfg = data.get("config", {})

        # Extract channels
        sources = setup_data.get("sources", [])
        destinations = setup_data.get("destinations", [])
        if isinstance(sources, str):
            sources = [s.strip() for s in sources.split(",") if s.strip()]
        if isinstance(destinations, str):
            destinations = [d.strip() for d in destinations.split(",") if d.strip()]

        # Enforce plan channel limit
        from auth_manager import auth_manager
        user = auth_manager.get_user_by_id(user_id)
        if user and user.get("role") != "admin":
            sub = auth_manager.check_user_subscription(user_id)
            channel_limit = int(sub.get("channel_limit", 1))
            plan_name = sub.get("plan_name", "Current Plan")
            if len(sources) > channel_limit:
                return False, f"Plan limit reached: Your '{plan_name}' allows copying from up to {channel_limit} source channel(s). You entered {len(sources)}. Please upgrade your plan to add more channels.", {}

        cfg["channels"] = {
            "sources": sources,
            "destinations": destinations,
            "mappings": setup_data.get("mappings", {})
        }

        # Extract customizations & Promotion engine
        cfg["customizations"] = {
            "replace_all_usernames": bool(setup_data.get("replace_all_usernames", False)),
            "global_replacement_username": setup_data.get("global_replacement_username", "").strip(),
            "username_rules": setup_data.get("username_rules", []),
            "remove_all_urls": bool(setup_data.get("remove_all_urls", False)),
            "replace_all_urls": bool(setup_data.get("replace_all_urls", False)),
            "global_replacement_url": setup_data.get("global_replacement_url", "").strip(),
            "url_rules": setup_data.get("url_rules", []),
            "word_rules": setup_data.get("word_rules", []),
            "remove_images": bool(setup_data.get("remove_images", False)),
            "header": setup_data.get("header", "").strip(),
            "footer": setup_data.get("footer", "").strip(),
            "blacklist_keywords": setup_data.get("blacklist_keywords", []),
            "whitelist_keywords": setup_data.get("whitelist_keywords", [])
        }

        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE user_bots
            SET config_json = ?, onboarding_step = 'tutorial', updated_at = ?
            WHERE user_id = ?
            """, (json.dumps(cfg), now, user_id))
            conn.commit()

        logger.info(f"⚙️ Message customizer & promotion engine configured for user {user_id}")
        return True, "Message rules and channels saved successfully!", {"step": "tutorial"}

    # =========================================================================
    # Step 4: Finalize Onboarding & Provide Tips & Tutorial
    # =========================================================================
    def complete_onboarding(self, user_id: int) -> Tuple[bool, str, Dict[str, Any]]:
        """Completes the onboarding workflow, unlocking the full user dashboard."""
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            UPDATE user_bots
            SET is_onboarded = 1, onboarding_step = 'completed', updated_at = ?
            WHERE user_id = ?
            """, (now, user_id))

            cursor.execute("SELECT value FROM site_settings WHERE key = 'telegram_support_channel'")
            row = cursor.fetchone()
            support_link = row["value"] if row else "https://t.me/telemirror_support"
            conn.commit()

        logger.info(f"🚀 User {user_id} has completed onboarding!")
        return True, "Onboarding complete! Welcome to TeleMirror.", {
            "is_onboarded": True,
            "support_channel": support_link,
            "redirect": "/dashboard"
        }

onboarding_manager = OnboardingManager()
