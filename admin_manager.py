import json
import logging
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple

from database import db
from auth_manager import auth_manager
from payment_manager import payment_manager

logger = logging.getLogger("AdminManager")

class AdminManager:
    # =========================================================================
    # 1. CRM Analytics & Overview
    # =========================================================================
    def get_dashboard_analytics(self) -> Dict[str, Any]:
        with db.get_connection() as conn:
            cursor = conn.cursor()

            # Users count
            cursor.execute("SELECT COUNT(*) as total, SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) as active FROM users")
            u_row = cursor.fetchone()
            total_users = u_row["total"] or 0
            active_users = u_row["active"] or 0

            # Subscriptions
            cursor.execute("""
            SELECT 
                COUNT(*) as total,
                SUM(CASE WHEN status = 'active' THEN 1 ELSE 0 END) as active,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending
            FROM subscriptions
            """)
            s_row = cursor.fetchone()
            total_subs = s_row["total"] or 0
            active_subs = s_row["active"] or 0
            pending_subs = s_row["pending"] or 0

            # Revenue / Income
            cursor.execute("SELECT SUM(amount) as total_rev FROM transactions WHERE status = 'success'")
            rev_row = cursor.fetchone()
            total_revenue = rev_row["total_rev"] or 0.0

            # Active Bots
            cursor.execute("SELECT COUNT(*) as cnt FROM user_bots WHERE status = 'running'")
            running_bots = cursor.fetchone()["cnt"] or 0

            # Support tickets
            cursor.execute("SELECT COUNT(*) as cnt FROM support_tickets WHERE status = 'open'")
            open_tickets = cursor.fetchone()["cnt"] or 0

            # Recent transactions
            cursor.execute("""
            SELECT t.*, u.name as user_name, u.email as user_email, p.name as plan_name
            FROM transactions t
            LEFT JOIN users u ON t.user_id = u.id
            LEFT JOIN plans p ON t.plan_id = p.id
            ORDER BY t.id DESC LIMIT 5
            """)
            recent_txs = [dict(r) for r in cursor.fetchall()]

            # Recent pending subscriptions awaiting approval
            cursor.execute("""
            SELECT s.*, u.name as user_name, u.email as user_email, p.name as plan_name, t.proof_details, t.reference
            FROM subscriptions s
            JOIN users u ON s.user_id = u.id
            LEFT JOIN plans p ON s.plan_id = p.id
            LEFT JOIN transactions t ON s.transaction_id = t.id
            WHERE s.status = 'pending'
            ORDER BY s.id DESC LIMIT 5
            """)
            pending_approvals = []
            for r in cursor.fetchall():
                item = dict(r)
                try:
                    item["proof"] = json.loads(item.get("proof_details") or "{}")
                except Exception:
                    item["proof"] = {}
                pending_approvals.append(item)

            return {
                "total_users": total_users,
                "active_users": active_users,
                "total_subscriptions": total_subs,
                "active_subscriptions": active_subs,
                "pending_approvals": pending_subs,
                "total_revenue": total_revenue,
                "running_bots": running_bots,
                "open_tickets": open_tickets,
                "recent_transactions": recent_txs,
                "pending_approval_list": pending_approvals
            }

    # =========================================================================
    # 2. Manage Users
    # =========================================================================
    def get_users(self, search: str = "", limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            query = """
            SELECT u.id, u.name, u.email, u.role, u.is_active, u.is_verified, u.created_at,
                   b.status as bot_status, b.is_onboarded,
                   s.status as sub_status, p.name as plan_name
            FROM users u
            LEFT JOIN user_bots b ON u.id = b.user_id
            LEFT JOIN subscriptions s ON s.id = (
                SELECT id FROM subscriptions WHERE user_id = u.id ORDER BY id DESC LIMIT 1
            )
            LEFT JOIN plans p ON s.plan_id = p.id
            """
            params = []
            if search:
                query += " WHERE u.name LIKE ? OR u.email LIKE ?"
                params.extend([f"%{search}%", f"%{search}%"])
            query += " ORDER BY u.id DESC LIMIT ? OFFSET ?"
            params.extend([limit, offset])

            cursor.execute(query, params)
            return [dict(r) for r in cursor.fetchall()]

    def update_user_status(self, user_id: int, is_active: Optional[int] = None, is_verified: Optional[int] = None, role: Optional[str] = None) -> bool:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with db.get_connection() as conn:
            cursor = conn.cursor()
            if is_active is not None:
                cursor.execute("UPDATE users SET is_active = ?, updated_at = ? WHERE id = ?", (is_active, now, user_id))
            if is_verified is not None:
                cursor.execute("UPDATE users SET is_verified = ?, updated_at = ? WHERE id = ?", (is_verified, now, user_id))
            if role is not None:
                cursor.execute("UPDATE users SET role = ?, updated_at = ? WHERE id = ?", (role, now, user_id))
            conn.commit()
            return True

    def delete_user(self, user_id: int) -> bool:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM users WHERE id = ?", (user_id,))
            conn.commit()
            return True

    def get_user_details(self, user_id: int) -> Optional[Dict[str, Any]]:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT u.id, u.name, u.email, u.role, u.is_active, u.is_verified, u.created_at,
                   b.status as bot_status, b.phone_number, b.is_onboarded,
                   s.id as subscription_id, s.status as sub_status, s.start_date, s.end_date,
                   p.id as plan_id, p.name as plan_name, p.channel_limit
            FROM users u
            LEFT JOIN user_bots b ON u.id = b.user_id
            LEFT JOIN subscriptions s ON s.id = (
                SELECT id FROM subscriptions WHERE user_id = u.id ORDER BY id DESC LIMIT 1
            )
            LEFT JOIN plans p ON s.plan_id = p.id
            WHERE u.id = ?
            """, (user_id,))
            row = cursor.fetchone()
            if not row:
                return None
            return dict(row)

    def update_user(self, user_id: int, data: Dict[str, Any]) -> Tuple[bool, str]:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            user = cursor.fetchone()
            if not user:
                return False, "User not found."

            name = data.get("name", "").strip() or user["name"]
            email = data.get("email", "").strip().lower() or user["email"]
            role = data.get("role") or user["role"]
            is_active = int(data.get("is_active", user["is_active"]))
            is_verified = int(data.get("is_verified", user["is_verified"]))

            cursor.execute("""
            UPDATE users
            SET name = ?, email = ?, role = ?, is_active = ?, is_verified = ?, updated_at = ?
            WHERE id = ?
            """, (name, email, role, is_active, is_verified, now, user_id))

            new_pwd = data.get("password", "").strip()
            if new_pwd and len(new_pwd) >= 6:
                hashed = auth_manager.hash_password(new_pwd)
                cursor.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?", (hashed, now, user_id))

            # Assign or switch plan if requested
            plan_id = data.get("plan_id")
            if plan_id is not None and str(plan_id).isdigit() and int(plan_id) > 0:
                plan_id = int(plan_id)
                cursor.execute("SELECT * FROM plans WHERE id = ?", (plan_id,))
                plan = cursor.fetchone()
                if plan:
                    from datetime import timedelta
                    start_date = datetime.utcnow()
                    duration = plan["duration_days"] or 30
                    end_date = start_date + timedelta(days=duration)
                    start_str = start_date.strftime("%Y-%m-%d %H:%M:%S")
                    end_str = end_date.strftime("%Y-%m-%d %H:%M:%S")

                    cursor.execute("UPDATE subscriptions SET status = 'cancelled', updated_at = ? WHERE user_id = ? AND status = 'active'", (now, user_id))
                    cursor.execute("""
                    INSERT INTO subscriptions (user_id, plan_id, status, payment_method, start_date, end_date, created_at, updated_at)
                    VALUES (?, ?, 'active', 'manual', ?, ?, ?, ?)
                    """, (user_id, plan_id, start_str, end_str, now, now))

            conn.commit()
            return True, f"User '{name}' updated successfully."

    # =========================================================================
    # 3. Manage Subscription Plans
    # =========================================================================
    def save_plan(self, plan_data: Dict[str, Any]) -> Tuple[bool, str]:
        plan_id = plan_data.get("id")
        name = plan_data.get("name", "").strip()
        slug = plan_data.get("slug", "").strip() or name.lower().replace(" ", "-")
        desc = plan_data.get("description", "")
        price = float(plan_data.get("price", 0))
        currency = plan_data.get("currency", "NGN")
        duration = int(plan_data.get("duration_days", 30))
        channels = int(plan_data.get("channel_limit", 10))
        features = plan_data.get("features", [])
        if isinstance(features, str):
            features = [f.strip() for f in features.split(",") if f.strip()]
        features_json = json.dumps(features)
        is_active = 1 if plan_data.get("is_active", True) else 0
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        with db.get_connection() as conn:
            cursor = conn.cursor()
            if plan_id:
                cursor.execute("""
                UPDATE plans
                SET name = ?, slug = ?, description = ?, price = ?, currency = ?, duration_days = ?,
                    channel_limit = ?, features_json = ?, is_active = ?
                WHERE id = ?
                """, (name, slug, desc, price, currency, duration, channels, features_json, is_active, plan_id))
            else:
                cursor.execute("""
                INSERT INTO plans (name, slug, description, price, currency, duration_days, channel_limit, features_json, is_active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (name, slug, desc, price, currency, duration, channels, features_json, is_active, now))
            conn.commit()
            return True, "Plan saved successfully."

    def delete_plan(self, plan_id: int) -> bool:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
            conn.commit()
            return True

    # =========================================================================
    # 4. Payment Methods & Site Settings
    # =========================================================================
    def save_payment_methods(self, settings_data: Dict[str, str]) -> bool:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with db.get_connection() as conn:
            cursor = conn.cursor()
            for key, val in settings_data.items():
                cursor.execute("""
                INSERT INTO site_settings (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
                """, (key, str(val), now))
            conn.commit()
            return True

    # =========================================================================
    # 5. Subscription History & Approvals
    # =========================================================================
    def get_subscriptions(self, status: str = "", limit: int = 50) -> List[Dict[str, Any]]:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            query = """
            SELECT s.*, u.name as user_name, u.email as user_email, p.name as plan_name,
                   t.amount, t.currency, t.reference, t.proof_details
            FROM subscriptions s
            JOIN users u ON s.user_id = u.id
            LEFT JOIN plans p ON s.plan_id = p.id
            LEFT JOIN transactions t ON s.transaction_id = t.id
            """
            params = []
            if status and status != "all":
                query += " WHERE s.status = ?"
                params.append(status)
            query += " ORDER BY s.id DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            subs = []
            for r in cursor.fetchall():
                item = dict(r)
                try:
                    item["proof"] = json.loads(item.get("proof_details") or "{}")
                except Exception:
                    item["proof"] = {}
                subs.append(item)
            return subs

    def grant_manual_subscription(self, admin_id: int, user_id: int, plan_id: int, duration_days: int = 30) -> Tuple[bool, str]:
        from datetime import timedelta
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM plans WHERE id = ?", (plan_id,))
            plan = cursor.fetchone()
            if not plan:
                return False, "Plan not found."

            cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
            user = cursor.fetchone()
            if not user:
                return False, "User not found."

            start_date = datetime.utcnow()
            end_date = start_date + timedelta(days=duration_days)
            start_str = start_date.strftime("%Y-%m-%d %H:%M:%S")
            end_str = end_date.strftime("%Y-%m-%d %H:%M:%S")

            # Cancel old active subscriptions
            cursor.execute("UPDATE subscriptions SET status = 'cancelled', updated_at = ? WHERE user_id = ? AND status = 'active'", (now, user_id))

            # Create subscription
            cursor.execute("""
            INSERT INTO subscriptions (user_id, plan_id, status, payment_method, start_date, end_date, created_at, updated_at)
            VALUES (?, ?, 'active', 'manual', ?, ?, ?, ?)
            """, (user_id, plan_id, start_str, end_str, now, now))
            sub_id = cursor.lastrowid

            # Create transaction record for audit
            ref = f"ADMIN_GRANT_{user_id}_{int(datetime.utcnow().timestamp())}"
            cursor.execute("""
            INSERT INTO transactions (user_id, plan_id, amount, currency, payment_method, reference, status, gateway_response, created_at, updated_at)
            VALUES (?, ?, 0.0, ?, 'manual', ?, 'success', 'Direct admin grant', ?, ?)
            """, (user_id, plan_id, plan["currency"] or "NGN", ref, now, now))
            tx_id = cursor.lastrowid

            cursor.execute("UPDATE subscriptions SET transaction_id = ? WHERE id = ?", (tx_id, sub_id))
            conn.commit()

            logger.info(f"🎁 Admin #{admin_id} granted plan '{plan['name']}' to User #{user_id}")
            return True, f"Successfully granted {plan['name']} ({duration_days} days) to {user['name']}!"

    # =========================================================================
    # 6. Transaction History
    # =========================================================================
    def get_transactions(self, limit: int = 100) -> List[Dict[str, Any]]:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT t.*, u.name as user_name, u.email as user_email, p.name as plan_name
            FROM transactions t
            LEFT JOIN users u ON t.user_id = u.id
            LEFT JOIN plans p ON t.plan_id = p.id
            ORDER BY t.id DESC LIMIT ?
            """, (limit,))
            txs = []
            for r in cursor.fetchall():
                item = dict(r)
                try:
                    item["proof"] = json.loads(item.get("proof_details") or "{}")
                except Exception:
                    item["proof"] = {}
                txs.append(item)
            return txs

    # =========================================================================
    # 7. Newsletter Marketing
    # =========================================================================
    def get_newsletter_subscribers(self) -> List[Dict[str, Any]]:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM newsletter_subscribers ORDER BY id DESC")
            return [dict(r) for r in cursor.fetchall()]

    def subscribe_newsletter(self, email: str, user_id: Optional[int] = None) -> bool:
        email = email.strip().lower()
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR IGNORE INTO newsletter_subscribers (email, user_id, is_active, created_at)
            VALUES (?, ?, 1, ?)
            """, (email, user_id, now))
            conn.commit()
            return True

    # =========================================================================
    # 8. Message Center
    # =========================================================================
    def send_broadcast_message(self, admin_id: int, recipient_type: str, recipient_ids: List[int], subject: str, body: str) -> Tuple[bool, str]:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            emails = []
            if recipient_type == "all":
                cursor.execute("SELECT email FROM users WHERE is_active = 1")
                emails = [r["email"] for r in cursor.fetchall()]
            elif recipient_type == "single" and recipient_ids:
                cursor.execute("SELECT email FROM users WHERE id = ?", (recipient_ids[0],))
                row = cursor.fetchone()
                if row:
                    emails = [row["email"]]
            elif recipient_type == "multiple" and recipient_ids:
                placeholders = ",".join("?" * len(recipient_ids))
                cursor.execute(f"SELECT email FROM users WHERE id IN ({placeholders})", recipient_ids)
                emails = [r["email"] for r in cursor.fetchall()]

            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("""
            INSERT INTO message_center_logs (admin_id, recipient_type, recipient_emails, subject, body, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'sent', ?)
            """, (admin_id, recipient_type, ",".join(emails), subject, body, now))
            conn.commit()

            logger.info(f"📨 Message dispatched to {len(emails)} recipients by Admin #{admin_id}")
            return True, f"Branded message dispatched successfully to {len(emails)} recipient(s)!"

    def get_message_logs(self) -> List[Dict[str, Any]]:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM message_center_logs ORDER BY id DESC LIMIT 50")
            return [dict(r) for r in cursor.fetchall()]

    # =========================================================================
    # 9. Support Tickets & Real-Time Chat
    # =========================================================================
    def get_support_tickets(self, status: str = "", user_id: Optional[int] = None) -> List[Dict[str, Any]]:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            query = """
            SELECT st.*, u.name as user_name, u.email as user_email,
                   (SELECT COUNT(*) FROM ticket_messages WHERE ticket_id = st.id) as message_count
            FROM support_tickets st
            JOIN users u ON st.user_id = u.id
            """
            params = []
            conds = []
            if user_id:
                conds.append("st.user_id = ?")
                params.append(user_id)
            if status and status != "all":
                conds.append("st.status = ?")
                params.append(status)
            if conds:
                query += " WHERE " + " AND ".join(conds)
            query += " ORDER BY st.updated_at DESC"

            cursor.execute(query, params)
            return [dict(r) for r in cursor.fetchall()]

    def create_support_ticket(self, user_id: int, subject: str, message: str, priority: str = "medium") -> int:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO support_tickets (user_id, subject, priority, status, created_at, updated_at)
            VALUES (?, ?, ?, 'open', ?, ?)
            """, (user_id, subject, priority, now, now))
            ticket_id = cursor.lastrowid

            cursor.execute("""
            INSERT INTO ticket_messages (ticket_id, sender_id, sender_role, message, created_at)
            VALUES (?, ?, 'user', ?, ?)
            """, (ticket_id, user_id, message, now))
            conn.commit()
            return ticket_id

    def get_ticket_details(self, ticket_id: int) -> Optional[Dict[str, Any]]:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT st.*, u.name as user_name, u.email as user_email
            FROM support_tickets st
            JOIN users u ON st.user_id = u.id
            WHERE st.id = ?
            """, (ticket_id,))
            row = cursor.fetchone()
            if not row:
                return None
            ticket = dict(row)

            cursor.execute("""
            SELECT tm.*, u.name as sender_name
            FROM ticket_messages tm
            JOIN users u ON tm.sender_id = u.id
            WHERE tm.ticket_id = ?
            ORDER BY tm.id ASC
            """, (ticket_id,))
            ticket["messages"] = [dict(m) for m in cursor.fetchall()]
            return ticket

    def reply_ticket(self, ticket_id: int, sender_id: int, sender_role: str, message: str, new_status: Optional[str] = None) -> bool:
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO ticket_messages (ticket_id, sender_id, sender_role, message, created_at)
            VALUES (?, ?, ?, ?, ?)
            """, (ticket_id, sender_id, sender_role, message, now))

            if new_status:
                cursor.execute("UPDATE support_tickets SET status = ?, updated_at = ? WHERE id = ?", (new_status, now, ticket_id))
            else:
                cursor.execute("UPDATE support_tickets SET updated_at = ? WHERE id = ?", (now, ticket_id))
            conn.commit()
            return True

    # =========================================================================
    # 10. Giveaways
    # =========================================================================
    def get_giveaways(self) -> List[Dict[str, Any]]:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT g.*, p.name as plan_name
            FROM giveaways g
            LEFT JOIN plans p ON g.plan_id = p.id
            ORDER BY g.id DESC
            """)
            return [dict(r) for r in cursor.fetchall()]

    def create_giveaway(self, data: Dict[str, Any]) -> Tuple[bool, str]:
        code = data.get("code", "").strip().upper()
        title = data.get("title", "").strip()
        target_type = data.get("target_type", "all")
        target_users = json.dumps(data.get("target_user_ids", []))
        duration = int(data.get("duration_days", 7))
        plan_id = int(data.get("plan_id", 1))
        max_claims = int(data.get("max_claims", 0))
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO giveaways (code, title, target_type, target_user_ids, duration_days, plan_id, max_claims, is_active, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
            """, (code, title, target_type, target_users, duration, plan_id, max_claims, now))
            conn.commit()
            return True, f"Giveaway '{code}' created successfully!"

    def delete_giveaway(self, giveaway_id: int) -> bool:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM giveaways WHERE id = ?", (giveaway_id,))
            conn.commit()
            return True

    def toggle_giveaway(self, giveaway_id: int, is_active: int) -> bool:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE giveaways SET is_active = ? WHERE id = ?", (is_active, giveaway_id))
            conn.commit()
            return True

    def delete_newsletter_subscriber(self, sub_id: int) -> bool:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM newsletter_subscribers WHERE id = ?", (sub_id,))
            conn.commit()
            return True

    def clear_system_logs(self) -> bool:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM audit_logs")
            conn.commit()
            return True

    # =========================================================================
    # 11. Site Settings & Error Logs
    # =========================================================================
    def get_all_site_settings(self) -> Dict[str, str]:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM site_settings")
            return {r["key"]: r["value"] for r in cursor.fetchall()}

    def get_system_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT a.*, u.name as user_name, u.email as user_email
            FROM audit_logs a
            LEFT JOIN users u ON a.user_id = u.id
            ORDER BY a.id DESC LIMIT ?
            """, (limit,))
            return [dict(r) for r in cursor.fetchall()]

admin_manager = AdminManager()
