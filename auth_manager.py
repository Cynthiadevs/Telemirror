import hashlib
import hmac
import secrets
import json
import logging
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Tuple
from database import db

logger = logging.getLogger("AuthManager")

# In-memory session store: token -> {"user_id": int, "expires_at": datetime, "is_impersonating": bool, "admin_id": Optional[int]}
SESSIONS: Dict[str, Dict[str, Any]] = {}

def hash_password(password: str) -> str:
    """Hashes a password using PBKDF2-HMAC-SHA256 with a unique salt."""
    salt = secrets.token_hex(16)
    key = hashlib.pbkdf2_hmac(
        'sha256',
        password.encode('utf-8'),
        salt.encode('utf-8'),
        200000
    )
    return f"{salt}${key.hex()}"

def verify_password(stored_password: str, provided_password: str) -> bool:
    """Verifies a stored salt$hash password against user input."""
    try:
        salt, key_hex = stored_password.split('$', 1)
        expected_key = bytes.fromhex(key_hex)
        key = hashlib.pbkdf2_hmac(
            'sha256',
            provided_password.encode('utf-8'),
            salt.encode('utf-8'),
            200000
        )
        return hmac.compare_digest(key, expected_key)
    except Exception as e:
        logger.error(f"Error verifying password: {e}")
        return False

class AuthManager:
    def __init__(self):
        self._ensure_admin_account()

    def _ensure_admin_account(self):
        """Ensures at least one super admin exists in the database."""
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1")
            admin = cursor.fetchone()
            if not admin:
                now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                admin_pw_hash = hash_password("admin123")
                cursor.execute("""
                INSERT INTO users (name, email, password_hash, role, is_active, is_verified, created_at, updated_at)
                VALUES (?, ?, ?, 'admin', 1, 1, ?, ?)
                """, ("Master Admin", "admin@telemirror.com", admin_pw_hash, now, now))
                admin_id = cursor.lastrowid
                
                # Create bot record for admin
                default_config = {
                    "channels": {"sources": [], "destinations": [], "mappings": {}},
                    "customizations": {
                        "replace_all_usernames": True,
                        "global_replacement_username": "@telemirror_admin",
                        "remove_all_urls": False,
                        "header": "",
                        "footer": "🚀 Mirrored with TeleMirror Pro",
                        "blacklist_keywords": [],
                        "whitelist_keywords": []
                    }
                }
                cursor.execute("""
                INSERT INTO user_bots (user_id, config_json, stats_json, created_at, updated_at)
                VALUES (?, ?, '{}', ?, ?)
                """, (admin_id, json.dumps(default_config), now, now))
                conn.commit()
                logger.info("👑 Default Super Admin created: admin@telemirror.com / admin123")

    def register_user(self, name: str, email: str, password: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Registers a new user and creates their isolated bot record."""
        name = name.strip()
        email = email.strip().lower()
        if not name or len(name) < 2:
            return False, "Full name must be at least 2 characters.", None
        if not email or "@" not in email or "." not in email:
            return False, "Please enter a valid email address.", None
        if not password or len(password) < 6:
            return False, "Password must be at least 6 characters.", None

        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM users WHERE email = ?", (email,))
            if cursor.fetchone():
                return False, "An account with this email address already exists.", None

            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            pw_hash = hash_password(password)

            cursor.execute("""
            INSERT INTO users (name, email, password_hash, role, is_active, is_verified, created_at, updated_at)
            VALUES (?, ?, ?, 'user', 1, 0, ?, ?)
            """, (name, email, pw_hash, now, now))
            user_id = cursor.lastrowid

            # Initialize empty isolated bot config for this user
            default_config = {
                "channels": {"sources": [], "destinations": [], "mappings": {}},
                "customizations": {
                    "replace_all_usernames": False,
                    "global_replacement_username": "",
                    "remove_all_urls": False,
                    "header": "",
                    "footer": "",
                    "blacklist_keywords": [],
                    "whitelist_keywords": []
                }
            }
            cursor.execute("""
            INSERT INTO user_bots (user_id, config_json, stats_json, created_at, updated_at)
            VALUES (?, ?, '{}', ?, ?)
            """, (user_id, json.dumps(default_config), now, now))
            conn.commit()

            user_data = self.get_user_by_id(user_id)
            token = self.create_session(user_id)
            return True, "Account created successfully!", {"user": user_data, "token": token}

    def login_user(self, email: str, password: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Authenticates user with email and password."""
        email = email.strip().lower()
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
            user = cursor.fetchone()
            if not user:
                return False, "Invalid email or password.", None

            if not verify_password(user["password_hash"], password):
                return False, "Invalid email or password.", None

            if not user["is_active"]:
                return False, "Your account has been deactivated. Please contact support.", None

            user_data = dict(user)
            del user_data["password_hash"]
            token = self.create_session(user["id"])
            return True, "Login successful!", {"user": user_data, "token": token}

    def create_session(self, user_id: int, is_impersonating: bool = False, admin_id: Optional[int] = None) -> str:
        """Generates a secure random session token valid for 72 hours."""
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(hours=72)
        SESSIONS[token] = {
            "user_id": user_id,
            "expires_at": expires_at,
            "is_impersonating": is_impersonating,
            "admin_id": admin_id
        }
        return token

    def validate_session(self, token: str) -> Optional[Dict[str, Any]]:
        """Checks if a session token is valid and active."""
        if not token or token not in SESSIONS:
            return None
        session = SESSIONS[token]
        if datetime.utcnow() > session["expires_at"]:
            del SESSIONS[token]
            return None
        return session

    def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Fetches user record by ID (excluding password hash)."""
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id, name, email, role, is_active, is_verified, created_at, updated_at FROM users WHERE id = ?", (user_id,))
            user = cursor.fetchone()
            return dict(user) if user else None

    def get_user_from_request(self, request) -> Optional[Dict[str, Any]]:
        """Extracts and validates user from Authorization header or Cookie."""
        token = None
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = request.cookies.get("telemirror_token")

        if not token:
            return None

        session = self.validate_session(token)
        if not session:
            return None

        user = self.get_user_by_id(session["user_id"])
        if user:
            user["is_impersonating"] = session.get("is_impersonating", False)
            user["admin_id"] = session.get("admin_id")
            user["session_token"] = token
        return user

    def impersonate_user(self, admin_user: Dict[str, Any], target_user_id: int) -> Tuple[bool, str, Optional[str]]:
        """Allows an admin to generate a session logging in as any user."""
        if admin_user.get("role") != "admin":
            return False, "Unauthorized. Only admins can impersonate users.", None

        target_user = self.get_user_by_id(target_user_id)
        if not target_user:
            return False, "Target user not found.", None

        token = self.create_session(target_user_id, is_impersonating=True, admin_id=admin_user["id"])
        return True, f"Now impersonating {target_user['name']}.", token

    def check_user_subscription(self, user_id: int) -> Dict[str, Any]:
        """Checks if a user has an active, valid subscription."""
        user = self.get_user_by_id(user_id)
        if not user:
            return {"is_subscribed": False, "status": "no_account"}

        # Admins always have unlimited bypass access
        if user.get("role") == "admin":
            return {
                "is_subscribed": True,
                "status": "active",
                "plan_id": 0,
                "plan_name": "Admin Lifetime Access",
                "channel_limit": 9999,
                "is_admin": True,
                "expires_at": "Never"
            }

        with db.get_connection() as conn:
            cursor = conn.cursor()
            # Fetch most recent subscription
            cursor.execute("""
            SELECT s.*, p.name as plan_name, p.channel_limit, p.features_json
            FROM subscriptions s
            LEFT JOIN plans p ON s.plan_id = p.id
            WHERE s.user_id = ?
            ORDER BY s.id DESC
            LIMIT 1
            """, (user_id,))
            sub = cursor.fetchone()

            if not sub:
                return {"is_subscribed": False, "status": "none", "message": "No subscription found."}

            status = sub["status"]
            if status == "pending":
                return {
                    "is_subscribed": False,
                    "status": "pending",
                    "plan_id": sub["plan_id"],
                    "plan_name": sub["plan_name"],
                    "channel_limit": sub["channel_limit"] or 1,
                    "payment_method": sub["payment_method"],
                    "message": "Payment pending admin verification."
                }

            if status == "active":
                # Check expiration date if set
                if sub["end_date"]:
                    try:
                        exp_dt = datetime.strptime(sub["end_date"], "%Y-%m-%d %H:%M:%S")
                        if datetime.utcnow() > exp_dt:
                            # Update to expired
                            cursor.execute("UPDATE subscriptions SET status = 'expired' WHERE id = ?", (sub["id"],))
                            conn.commit()
                            return {"is_subscribed": False, "status": "expired", "message": "Your subscription has expired."}
                    except Exception:
                        pass

                return {
                    "is_subscribed": True,
                    "status": "active",
                    "plan_id": sub["plan_id"],
                    "plan_name": sub["plan_name"],
                    "channel_limit": sub["channel_limit"] or 1,
                    "expires_at": sub["end_date"],
                    "end_date": sub["end_date"],
                    "payment_method": sub["payment_method"]
                }

            return {"is_subscribed": False, "status": status, "message": f"Subscription {status}."}

auth_manager = AuthManager()
