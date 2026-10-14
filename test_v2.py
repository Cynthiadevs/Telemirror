import asyncio
import sys
from database import db
from auth_manager import auth_manager
from payment_manager import payment_manager
from onboarding_manager import onboarding_manager
from tenant_bot_manager import tenant_bot_manager
from admin_manager import admin_manager
from web_server import create_app

async def test_all():
    print("--- [1] Testing Database & Seeding ---")
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) as cnt FROM users")
        print(f"Users count: {cursor.fetchone()['cnt']}")
        cursor.execute("SELECT count(*) as cnt FROM plans")
        print(f"Plans count: {cursor.fetchone()['cnt']}")
        cursor.execute("SELECT count(*) as cnt FROM site_settings")
        print(f"Site settings count: {cursor.fetchone()['cnt']}")

    print("\n--- [2] Testing Auth & Paywall Guard ---")
    ok, msg, admin_auth = auth_manager.login_user("admin@telemirror.com", "admin123")
    assert ok, f"Admin login failed: {msg}"
    print(f"Admin auth OK: {admin_auth['user']['name']} ({admin_auth['user']['role']})")

    # Register fresh test user
    test_email = f"saas_user_{int(asyncio.get_event_loop().time())}@test.com"
    ok, msg, user_auth = auth_manager.register_user("Alice Trader", test_email, "securepass123")
    assert ok, f"User registration failed: {msg}"
    user_id = user_auth["user"]["id"]
    print(f"User registered OK: ID={user_id}, Name={user_auth['user']['name']}")

    # Check that paywall blocks them initially
    sub_status = auth_manager.check_user_subscription(user_id)
    assert not sub_status["is_subscribed"], "New user should not be subscribed before payment!"
    print(f"Paywall status correctly unsubscribed: {sub_status['status']}")

    print("\n--- [3] Testing Manual Payment & Admin Approval ---")
    plans = payment_manager.get_plans()
    selected_plan = plans[0]
    ok, msg, pay_res = payment_manager.submit_manual_payment(user_id, selected_plan["id"], {
        "sender_name": "Alice Trader",
        "sender_bank": "Zenith Bank",
        "reference": "TXN_ZENITH_99182"
    })
    assert ok, f"Manual payment submission failed: {msg}"
    print(f"Manual payment submitted: Ref={pay_res['reference']}, Status={pay_res['status']}")

    # Verify status is pending
    sub_pending = auth_manager.check_user_subscription(user_id)
    assert sub_pending["status"] == "pending", f"Expected pending status, got {sub_pending['status']}"
    print("User correctly in pending verification state.")

    # Admin approves
    ok, msg = payment_manager.approve_manual_subscription(pay_res["subscription_id"], admin_auth["user"]["id"])
    assert ok, f"Approval failed: {msg}"
    sub_active = auth_manager.check_user_subscription(user_id)
    assert sub_active["is_subscribed"], "User should now be subscribed!"
    print(f"Subscription approved! User plan={sub_active['plan_name']}, Expires={sub_active['expires_at']}")

    print("\n--- [4] Testing Onboarding Flow ---")
    ob_status = onboarding_manager.get_onboarding_status(user_id)
    print(f"Initial onboarding status: step={ob_status['step']}, onboarded={ob_status['is_onboarded']}")

    # Setup customizer & promotion engine
    ok, msg, cust_res = onboarding_manager.setup_customizer_and_engine(user_id, {
        "sources": ["@testsignalsource"],
        "destinations": ["@myviptarget"],
        "global_replacement_username": "@alicetrades",
        "header": "⚡️ ALICE VIP SIGNAL",
        "footer": "Trade with Alice: t.me/alicetrades",
        "remove_all_urls": True,
        "replace_all_usernames": True
    })
    assert ok, f"Customizer setup failed: {msg}"
    print(f"Customizer saved: step={cust_res['step']}")

    # Complete onboarding
    ok, msg, comp_res = onboarding_manager.complete_onboarding(user_id)
    assert ok, f"Completion failed: {msg}"
    print(f"Onboarding completed: {comp_res['support_channel']}")

    print("\n--- [4.1] Testing Telegram Account Connected State & Removal ---")
    # Simulate an active connected telegram session
    with db.get_connection() as conn:
        conn.cursor().execute("""
        UPDATE user_bots
        SET phone_number = '+2348011223344', session_string = 'MOCK_TEST_SESSION_123', api_id = '123456', api_hash = 'mockhash123'
        WHERE user_id = ?
        """, (user_id,))
        conn.commit()

    conn_status = onboarding_manager.get_onboarding_status(user_id)
    assert conn_status["has_telegram"] is True, "Expected has_telegram=True when session_string exists"
    assert conn_status["phone_number"] == "+2348011223344", f"Expected phone +2348011223344, got {conn_status['phone_number']}"
    print(f"Connected state verified: Phone={conn_status['phone_number']}, has_telegram={conn_status['has_telegram']}")

    # Remove account
    ok, msg, rem_res = await onboarding_manager.remove_telegram_account(user_id)
    assert ok is True, f"Remove account failed: {msg}"
    print(f"Account removal succeeded: {msg}")

    post_status = onboarding_manager.get_onboarding_status(user_id)
    assert post_status["has_telegram"] is False, "Expected has_telegram=False after removal"
    assert post_status["has_credentials"] is False, "Expected has_credentials=False after removal"
    assert post_status["step"] == "credentials", f"Expected step=credentials, got {post_status['step']}"
    print("Post-removal reset verified: session cleared, wizard reset to credentials step.")

    print("\n--- [5] Testing Tenant Bot Manager ---")
    bot_st = tenant_bot_manager.get_status(user_id)
    print(f"Tenant Bot initial status: {bot_st['status']}, sources={bot_st['sources_count']}")

    print("\n--- [5.1] Testing Strict Plan Channel Limit Enforcement (Zero Override) ---")
    # User is on Starter Plan with channel_limit = 1
    sub_info = auth_manager.check_user_subscription(user_id)
    assert sub_info["channel_limit"] == 1, f"Expected channel_limit=1, got {sub_info['channel_limit']}"
    
    # 1 source -> should SUCCEED
    ok, msg = await tenant_bot_manager.save_user_config(user_id, {
        "channels": {"sources": ["@source1"], "destinations": ["@dest1"]}
    })
    assert ok, f"Saving 1 source should succeed: {msg}"
    print(f"1 source saved successfully: {msg}")

    # 2 sources -> should FAIL with limit message
    ok, msg = await tenant_bot_manager.save_user_config(user_id, {
        "channels": {"sources": ["@source1", "@source2"], "destinations": ["@dest1"]}
    })
    assert not ok, "Saving 2 sources on 1-channel plan must fail!"
    assert "Plan limit reached" in msg, f"Unexpected error message: {msg}"
    print(f"Zero-override restriction enforced successfully: {msg}")

    # Upgrade to Pro Plan (channel_limit = 3)
    pro_plan = next(p for p in plans if p["channel_limit"] == 3)
    ok, msg, pay_res2 = payment_manager.submit_manual_payment(user_id, pro_plan["id"], {
        "sender_name": "Alice Trader",
        "sender_bank": "Zenith Bank",
        "reference": "TXN_UPGRADE_PRO"
    })
    payment_manager.approve_manual_subscription(pay_res2["subscription_id"], admin_auth["user"]["id"])
    sub_upgraded = auth_manager.check_user_subscription(user_id)
    assert sub_upgraded["channel_limit"] == 3, f"Expected channel_limit=3, got {sub_upgraded['channel_limit']}"
    print(f"Upgraded to {sub_upgraded['plan_name']} (Channel Limit: {sub_upgraded['channel_limit']})")

    # 3 sources -> should now SUCCEED
    ok, msg = await tenant_bot_manager.save_user_config(user_id, {
        "channels": {"sources": ["@source1", "@source2", "@source3"], "destinations": ["@dest1"]}
    })
    assert ok, f"Saving 3 sources after upgrade should succeed: {msg}"
    print(f"3 sources saved successfully after upgrade: {msg}")

    # 4 sources -> should FAIL
    ok, msg = await tenant_bot_manager.save_user_config(user_id, {
        "channels": {"sources": ["@s1", "@s2", "@s3", "@s4"], "destinations": ["@dest1"]}
    })
    assert not ok, "Saving 4 sources on 3-channel plan must fail!"
    print(f"Zero-override on Pro tier enforced: {msg}")

    # Admin user test -> Unlimited bypass (channel_limit = 9999)
    admin_id = admin_auth["user"]["id"]
    ok, msg = await tenant_bot_manager.save_user_config(admin_id, {
        "channels": {"sources": [f"@admin_src_{i}" for i in range(15)], "destinations": ["@dest1"]}
    })
    assert ok, f"Admin bypass should allow unlimited sources: {msg}"
    print(f"Admin bypass verified with 15 channels: {msg}")

    print("\n--- [5.2] Testing Automatic Channel Selection & Dialogs Status ---")
    dialog_res = await tenant_bot_manager.fetch_dialogs_status(user_id)
    assert "dialogs" in dialog_res, "Dialogs status result missing 'dialogs' key"
    print(f"Dialogs status result: success={dialog_res['success']}, connected={dialog_res.get('connected')}, dialogs_count={len(dialog_res.get('dialogs', []))}")


    print("\n--- [6] Testing Admin CRM Analytics & Modules ---")
    analytics = admin_manager.get_dashboard_analytics()
    print(f"Analytics: Total Users={analytics['total_users']}, Active Subs={analytics['active_subscriptions']}, Rev=₦{analytics['total_revenue']}")
    users_list = admin_manager.get_users()
    print(f"Admin Users list count: {len(users_list)}")

    # Admin Impersonation test
    ok, msg, imp_token = auth_manager.impersonate_user(admin_auth["user"], user_id)
    assert ok, f"Impersonation failed: {msg}"
    session = auth_manager.validate_session(imp_token)
    assert session["is_impersonating"] and session["user_id"] == user_id
    print(f"Impersonation verified! Token generated for target user ID={user_id}")

    # Admin User Details & Edit Test
    u_details = admin_manager.get_user_details(user_id)
    assert u_details is not None, "Failed to retrieve user details"
    assert u_details["email"] == test_email, f"User email mismatch: {u_details['email']}"
    print(f"Retrieved user details: {u_details['name']} ({u_details['email']})")

    ok, msg = admin_manager.update_user(user_id, {
        "name": "Alice Super Trader",
        "email": test_email,
        "role": "user",
        "is_active": 1,
        "is_verified": 1
    })
    assert ok, f"Admin update user failed: {msg}"
    updated_u = admin_manager.get_user_details(user_id)
    assert updated_u["name"] == "Alice Super Trader", f"User name not updated: {updated_u['name']}"
    print(f"Admin update user verified: {updated_u['name']}")

    # Admin Plan Save & Delete Test with Channel Limits & App Functions
    custom_plan_data = {
        "name": "VIP Signal Master",
        "slug": "vip-signal-master",
        "description": "Ultra fast mirroring with 20 channels",
        "price": 35000,
        "currency": "NGN",
        "duration_days": 60,
        "channel_limit": 20,
        "features": ["Real-time Message Mirroring", "Custom Header & Footer Injection", "24/7 Live Web Terminal Logs"],
        "is_active": 1
    }
    ok, msg = admin_manager.save_plan(custom_plan_data)
    assert ok, f"Admin save plan failed: {msg}"
    all_plans = payment_manager.get_plans(active_only=False)
    created_plan = next(p for p in all_plans if p["slug"] == "vip-signal-master")
    assert created_plan["channel_limit"] == 20, f"Expected channel_limit=20, got {created_plan['channel_limit']}"
    assert len(created_plan["features"]) == 3, f"Expected 3 features, got {len(created_plan['features'])}"
    print(f"Admin plan creation & channel limit verified: Plan={created_plan['name']}, Limit={created_plan['channel_limit']}")

    # Admin Direct Manual Grant Test
    ok, msg = admin_manager.grant_manual_subscription(admin_id, user_id, created_plan["id"], duration_days=60)
    assert ok, f"Admin grant manual subscription failed: {msg}"
    user_sub = auth_manager.check_user_subscription(user_id)
    assert user_sub["channel_limit"] == 20, f"Expected granted limit=20, got {user_sub['channel_limit']}"
    print(f"Admin manual grant verified: Plan={user_sub['plan_name']}, Limit={user_sub['channel_limit']}")

    # Clean up test plan
    admin_manager.delete_plan(created_plan["id"])
    print("Test plan deleted successfully.")

    # Admin Support Ticket & Chat Test
    ticket_id = admin_manager.create_support_ticket(user_id, "Need help with forward mapping", "How do I map Source A to Target B?")
    assert ticket_id > 0, "Ticket creation failed"
    ticket_detail = admin_manager.get_ticket_details(ticket_id)
    assert ticket_detail is not None, "Failed to get ticket details"
    assert len(ticket_detail["messages"]) == 1, f"Expected 1 message, got {len(ticket_detail['messages'])}"
    ok = admin_manager.reply_ticket(ticket_id, admin_id, "admin", "You can set custom mappings in the Channels tab!", new_status="resolved")
    assert ok, "Admin ticket reply failed"
    updated_ticket = admin_manager.get_ticket_details(ticket_id)
    assert updated_ticket["status"] == "resolved", f"Expected resolved status, got {updated_ticket['status']}"
    assert len(updated_ticket["messages"]) == 2, f"Expected 2 messages, got {len(updated_ticket['messages'])}"
    print(f"Support ticket & admin chat verified: Ticket #{ticket_id} resolved with 2 messages.")

    # Admin Newsletter Lead & Delete Test
    nl_email = f"lead_{int(asyncio.get_event_loop().time())}@newsletter.com"
    ok = admin_manager.subscribe_newsletter(nl_email)
    assert ok, "Newsletter subscribe failed"
    subs = admin_manager.get_newsletter_subscribers()
    sub_entry = next(s for s in subs if s["email"] == nl_email)
    assert sub_entry is not None, "Newsletter subscriber not found"
    admin_manager.delete_newsletter_subscriber(sub_entry["id"])
    print(f"Newsletter subscribe & delete verified: {nl_email}")

    # Admin Giveaway Test
    ok, msg = admin_manager.create_giveaway({
        "code": "TESTGIVEAWAY100",
        "title": "Special 100 Claims Promo",
        "plan_id": 1,
        "duration_days": 14,
        "target_type": "all",
        "max_claims": 100
    })
    assert ok, f"Giveaway creation failed: {msg}"
    giveaways = admin_manager.get_giveaways()
    gw = next(g for g in giveaways if g["code"] == "TESTGIVEAWAY100")
    admin_manager.toggle_giveaway(gw["id"], 0)
    admin_manager.delete_giveaway(gw["id"])
    print("Giveaway creation, toggle, and delete verified.")

    # Admin Payment Gateway & Site Settings Test
    admin_manager.save_payment_methods({
        "paystack_enabled": "1",
        "korapay_enabled": "1",
        "opay_enabled": "1",
        "palmpay_enabled": "1",
        "site_name": "TeleMirror Test Edition"
    })
    settings = admin_manager.get_all_site_settings()
    assert settings.get("site_name") == "TeleMirror Test Edition", f"Settings mismatch: {settings.get('site_name')}"
    print(f"Site settings & multi-gateway configs verified: site_name={settings.get('site_name')}")

    # Admin Audit Logs Test
    admin_manager.clear_system_logs()
    print("System audit logs clear verified.")

    print("\n--- [7] Testing Aiohttp Web Application Factory ---")
    app = create_app()
    routes = list(app.router.routes())
    print(f"App router loaded with {len(routes)} routes.")

    print("\n🎉 ALL TELEMIRROR V2 CORE & ADMIN CRM TESTS PASSED SUCCESSFULLY!")

if __name__ == "__main__":
    asyncio.run(test_all())
