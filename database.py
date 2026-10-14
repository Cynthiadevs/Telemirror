import sqlite3
import os
import json
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

logger = logging.getLogger("Database")

DB_PATH = os.getenv("DB_PATH") or os.path.join(os.getenv("DATA_DIR", os.path.dirname(__file__)), "telemirror.db")

class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        os.makedirs(os.path.dirname(os.path.abspath(self.db_path)), exist_ok=True)
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self):
        """Creates all necessary tables for TeleMirror if they do not exist."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            
            # 1. Users Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'user', -- 'user' or 'admin'
                is_active INTEGER DEFAULT 1,
                is_verified INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """)

            # 2. Subscription Plans Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                slug TEXT UNIQUE NOT NULL,
                description TEXT,
                price REAL NOT NULL,
                currency TEXT DEFAULT 'NGN',
                duration_days INTEGER NOT NULL,
                channel_limit INTEGER DEFAULT 10,
                features_json TEXT DEFAULT '[]',
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            );
            """)

            # 3. Subscriptions Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                plan_id INTEGER REFERENCES plans(id) ON DELETE SET NULL,
                status TEXT DEFAULT 'pending', -- 'pending', 'active', 'expired', 'declined', 'cancelled'
                payment_method TEXT NOT NULL, -- 'paystack', 'korapay', 'opay', 'palmpay', 'manual', 'giveaway'
                start_date TEXT,
                end_date TEXT,
                transaction_id INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """)

            # 4. Transactions / Payments Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                plan_id INTEGER REFERENCES plans(id) ON DELETE SET NULL,
                amount REAL NOT NULL,
                currency TEXT DEFAULT 'NGN',
                payment_method TEXT NOT NULL,
                reference TEXT UNIQUE NOT NULL,
                proof_details TEXT, -- JSON for manual payment details (bank, sender, notes, proof_image)
                status TEXT DEFAULT 'pending', -- 'pending', 'success', 'failed', 'declined'
                gateway_response TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """)

            # 5. User Bots & Telegram Configurations
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_bots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                phone_number TEXT,
                api_id TEXT,
                api_hash TEXT,
                bot_token TEXT,
                session_string TEXT,
                is_onboarded INTEGER DEFAULT 0,
                onboarding_step TEXT DEFAULT 'credentials', -- 'credentials', 'otp', 'rules', 'completed'
                status TEXT DEFAULT 'stopped', -- 'stopped', 'running', 'error', 'connecting'
                error_message TEXT,
                config_json TEXT NOT NULL DEFAULT '{}',
                stats_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """)

            # 6. Support Tickets Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS support_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                subject TEXT NOT NULL,
                priority TEXT DEFAULT 'medium', -- 'low', 'medium', 'high', 'urgent'
                status TEXT DEFAULT 'open', -- 'open', 'in_progress', 'resolved', 'closed'
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """)

            # 7. Support Ticket Messages Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS ticket_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket_id INTEGER NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
                sender_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                sender_role TEXT NOT NULL, -- 'user', 'admin'
                message TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """)

            # 8. Newsletter Subscribers
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS newsletter_subscribers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                is_active INTEGER DEFAULT 1,
                created_at TEXT NOT NULL
            );
            """)

            # 9. Message Center (Platform Branded Emails/Broadcasts)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS message_center_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                admin_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                recipient_type TEXT NOT NULL, -- 'single', 'multiple', 'all'
                recipient_emails TEXT NOT NULL, -- comma separated or JSON
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                status TEXT DEFAULT 'sent',
                created_at TEXT NOT NULL
            );
            """)

            # 10. Giveaways / Promo Free-Use Table
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS giveaways (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                target_type TEXT NOT NULL, -- 'all', 'new_users', 'selected_users'
                target_user_ids TEXT, -- JSON array of user IDs if target_type == 'selected_users'
                duration_days INTEGER NOT NULL DEFAULT 7,
                plan_id INTEGER REFERENCES plans(id) ON DELETE SET NULL,
                max_claims INTEGER DEFAULT 0, -- 0 for unlimited
                claims_count INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                expires_at TEXT,
                created_at TEXT NOT NULL
            );
            """)

            # 11. Giveaway Claims (Tracks which user redeemed what)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS giveaway_claims (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                giveaway_id INTEGER NOT NULL REFERENCES giveaways(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                claimed_at TEXT NOT NULL,
                UNIQUE(giveaway_id, user_id)
            );
            """)

            # 12. Site Settings (Key-Value system store)
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS site_settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """)

            # 13. System Audit / Activity Logs
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                action TEXT NOT NULL,
                details TEXT,
                ip_address TEXT,
                created_at TEXT NOT NULL
            );
            """)

            conn.commit()
            logger.info("✅ Database schema initialized successfully.")

            # Seed default admin & plans & site settings if not present
            self._seed_defaults(cursor, conn)

    def _seed_defaults(self, cursor: sqlite3.Cursor, conn: sqlite3.Connection):
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        # 1. Seed Site Settings
        default_settings = {
            "site_name": "TeleMirror",
            "site_logo": "",
            "seo_title": "TeleMirror - Next-Gen Telegram Channel Mirror & Signal Automation",
            "seo_description": "Powerful cloud Telegram channel mirroring platform with real-time text transformation, ad replacement, and multi-channel forwarding.",
            "seo_keywords": "telegram mirror bot, signal cloner, channel forwarder, telethon, forex signals",
            "telegram_support_channel": "https://t.me/telemirror_support",
            "support_telegram_username": "@telemirror_admin",
            "maintenance_mode": "0",
            # Payment Gateway Settings
            "paystack_enabled": "1",
            "paystack_public_key": "pk_test_sample_paystack_key",
            "paystack_secret_key": "sk_test_sample_paystack_key",
            "korapay_enabled": "1",
            "korapay_public_key": "pk_test_sample_korapay_key",
            "korapay_secret_key": "sk_test_sample_korapay_key",
            "opay_enabled": "1",
            "opay_merchant_id": "OPAY_MERCHANT_SAMPLE",
            "opay_secret_key": "OPAY_KEY_SAMPLE",
            "palmpay_enabled": "1",
            "palmpay_app_id": "PALMPAY_SAMPLE_ID",
            "palmpay_secret_key": "PALMPAY_SAMPLE_KEY",
            # Manual Payment Settings
            "manual_payment_enabled": "1",
            "manual_bank_name": "Zenith Bank / OPay / PalmPay / Crypto",
            "manual_account_number": "1234567890",
            "manual_account_name": "TeleMirror Official Enterprise",
            "manual_instructions": "Please transfer the exact plan amount to the account details above. After payment, enter your sender name and transaction reference below to submit for instant review.",
            # SMTP Settings
            "smtp_enabled": "0",
            "smtp_host": "smtp.gmail.com",
            "smtp_port": "587",
            "smtp_user": "",
            "smtp_pass": "",
            "smtp_from": "no-reply@telemirror.com",
            "security_session_hours": "72"
        }

        for k, v in default_settings.items():
            cursor.execute("INSERT OR IGNORE INTO site_settings (key, value, updated_at) VALUES (?, ?, ?)", (k, v, now))

        # 2. Seed Default Subscription Plans
        cursor.execute("SELECT COUNT(*) as cnt FROM plans")
        if cursor.fetchone()["cnt"] == 0:
            default_plans = [
                (
                    "Starter Plan",
                    "starter",
                    "Perfect for individual traders and small communities.",
                    10000.0,
                    "NGN",
                    30,
                    1,
                    json.dumps(["1 Source Channel to copy from", "Clean Text & URL Filtering", "Basic Ad Replacement", "Live Logs Terminal"]),
                    1,
                    now
                ),
                (
                    "Pro Trader",
                    "pro",
                    "Best for professional signal forwarders and high-traffic channels.",
                    25000.0,
                    "NGN",
                    30,
                    3,
                    json.dumps(["3 Source Channels to copy from", "Zero Delay Instant Forwarding", "Full Ad & Username Cloner", "Media Cleaner & Watermarking", "Priority Support"]),
                    1,
                    now
                ),
                (
                    "Enterprise Unlimited",
                    "enterprise",
                    "For large networks, signal syndicates, and agency multi-broadcasting.",
                    60000.0,
                    "NGN",
                    90,
                    10,
                    json.dumps(["10 Source Channels to copy from", "Dedicated Worker Engine", "Custom Regex & Whitelist/Blacklist", "VIP Telegram Support", "90 Days Validity"]),
                    1,
                    now
                )
            ]
            cursor.executemany("""
            INSERT INTO plans (name, slug, description, price, currency, duration_days, channel_limit, features_json, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, default_plans)

        conn.commit()

db = Database()
