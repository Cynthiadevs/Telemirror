import os
import json
import asyncio
import logging
import sys
from datetime import datetime
from aiohttp import web

from database import db
from auth_manager import auth_manager
from payment_manager import payment_manager
from onboarding_manager import onboarding_manager
from tenant_bot_manager import tenant_bot_manager
from admin_manager import admin_manager
from cleaner import transform_text, should_process_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("WebServer")

# Helper decorators / middleware for authentication
def get_user_or_none(request) -> dict:
    return auth_manager.get_user_from_request(request)

def require_auth(handler):
    async def middleware(request):
        user = get_user_or_none(request)
        if not user:
            return web.json_response({"success": False, "error": "Authentication required. Please log in.", "redirect": "/login"}, status=401)
        request["user"] = user
        return await handler(request)
    return middleware

def require_admin(handler):
    async def middleware(request):
        user = get_user_or_none(request)
        if not user:
            return web.json_response({"success": False, "error": "Authentication required.", "redirect": "/login"}, status=401)
        if user.get("role") != "admin":
            return web.json_response({"success": False, "error": "Forbidden: Admin access required."}, status=403)
        request["user"] = user
        return await handler(request)
    return middleware

def require_subscription(handler):
    async def middleware(request):
        user = get_user_or_none(request)
        if not user:
            return web.json_response({"success": False, "error": "Authentication required.", "redirect": "/login"}, status=401)
        sub = auth_manager.check_user_subscription(user["id"])
        if not sub.get("is_subscribed"):
            return web.json_response({
                "success": False,
                "error": "Active subscription required.",
                "subscription_status": sub.get("status"),
                "redirect": "/subscription"
            }, status=403)
        request["user"] = user
        request["subscription"] = sub
        return await handler(request)
    return middleware

# ==============================================================================
# Public / Frontend Template Route
# ==============================================================================
def render_template(filename: str) -> web.Response:
    html_file = os.path.join(os.path.dirname(__file__), "templates", filename)
    if os.path.exists(html_file):
        with open(html_file, "r", encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="text/html")
    # Fallback to home.html if specific template not found
    fallback = os.path.join(os.path.dirname(__file__), "templates", "home.html")
    if os.path.exists(fallback):
        with open(fallback, "r", encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="text/html")
    return web.Response(text=f"<h1>Template {filename} not found.</h1>", content_type="text/html", status=404)

async def handle_index(request):
    return render_template("home.html")

async def handle_page_home(request):
    return render_template("home.html")

async def handle_page_pricing(request):
    return render_template("pricing.html")

async def handle_page_dashboard(request):
    return render_template("dashboard.html")

async def handle_page_channels(request):
    return render_template("channels.html")

async def handle_page_rules(request):
    return render_template("rules.html")

async def handle_page_logs(request):
    return render_template("logs.html")

async def handle_page_onboarding(request):
    return render_template("onboarding.html")

async def handle_page_support(request):
    return render_template("support.html")

async def handle_page_admin(request):
    return render_template("admin.html")

async def handle_healthz(request):
    return web.json_response({
        "status": "healthy",
        "service": "TeleMirror Multi-Tenant",
        "timestamp": datetime.utcnow().isoformat()
    })

# ==============================================================================
# Authentication APIs
# ==============================================================================
async def handle_auth_register(request):
    try:
        data = await request.json()
        ok, msg, res = auth_manager.register_user(
            name=data.get("name", ""),
            email=data.get("email", ""),
            password=data.get("password", "")
        )
        if not ok:
            return web.json_response({"success": False, "error": msg}, status=400)

        response = web.json_response({"success": True, "message": msg, "user": res["user"], "token": res["token"]})
        response.set_cookie("telemirror_token", res["token"], max_age=86400 * 3, httponly=False)
        return response
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

async def handle_auth_login(request):
    try:
        data = await request.json()
        ok, msg, res = auth_manager.login_user(
            email=data.get("email", ""),
            password=data.get("password", "")
        )
        if not ok:
            return web.json_response({"success": False, "error": msg}, status=401)

        response = web.json_response({"success": True, "message": msg, "user": res["user"], "token": res["token"]})
        response.set_cookie("telemirror_token", res["token"], max_age=86400 * 3, httponly=False)
        return response
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

async def handle_auth_me(request):
    user = get_user_or_none(request)
    if not user:
        return web.json_response({"authenticated": False})

    sub = auth_manager.check_user_subscription(user["id"])
    onb = onboarding_manager.get_onboarding_status(user["id"])
    return web.json_response({
        "authenticated": True,
        "user": user,
        "subscription": sub,
        "onboarding": onb
    })

async def handle_auth_logout(request):
    response = web.json_response({"success": True, "message": "Logged out successfully."})
    response.del_cookie("telemirror_token")
    return response

async def handle_auth_impersonate(request):
    user = get_user_or_none(request)
    if not user or user.get("role") != "admin":
        return web.json_response({"success": False, "error": "Admin required."}, status=403)
    try:
        data = await request.json()
        target_id = int(data.get("user_id"))
        ok, msg, token = auth_manager.impersonate_user(user, target_id)
        if not ok:
            return web.json_response({"success": False, "error": msg}, status=400)
        response = web.json_response({"success": True, "message": msg, "token": token})
        response.set_cookie("telemirror_token", token, max_age=86400 * 3, httponly=False)
        return response
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

async def handle_auth_exit_impersonation(request):
    user = get_user_or_none(request)
    if not user or not user.get("is_impersonating"):
        return web.json_response({"success": False, "error": "Not currently impersonating."}, status=400)
    admin_id = user.get("admin_id")
    token = auth_manager.create_session(admin_id)
    response = web.json_response({"success": True, "message": "Returned to admin account.", "token": token})
    response.set_cookie("telemirror_token", token, max_age=86400 * 3, httponly=False)
    return response

# ==============================================================================
# Subscription & Payment APIs
# ==============================================================================
async def handle_get_plans(request):
    plans = payment_manager.get_plans(active_only=True)
    return web.json_response({"success": True, "plans": plans})

async def handle_get_payment_methods(request):
    methods = payment_manager.get_payment_methods()
    return web.json_response({"success": True, "methods": methods})

@require_auth
async def handle_payment_initialize(request):
    user = request["user"]
    try:
        data = await request.json()
        plan_id = int(data.get("plan_id"))
        gateway = str(data.get("gateway", "paystack")).lower()
        callback_url = f"{request.scheme}://{request.host}/api/payment/verify"

        ok, msg, res = payment_manager.initialize_automatic_payment(
            user_id=user["id"],
            user_email=user["email"],
            plan_id=plan_id,
            gateway=gateway,
            callback_url=callback_url
        )
        if not ok:
            return web.json_response({"success": False, "error": msg}, status=400)
        return web.json_response({"success": True, "message": msg, "data": res})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

async def handle_payment_verify(request):
    ref = request.query.get("reference") or request.query.get("trxref")
    if not ref:
        return web.Response(text="Missing transaction reference.", status=400)

    ok, msg, res = payment_manager.verify_and_activate_payment(ref)
    if ok:
        # Redirect to onboarding
        raise web.HTTPFound("/?verified=1#onboarding")
    return web.Response(text=f"Payment verification failed: {msg}", status=400)

@require_auth
async def handle_payment_manual_submit(request):
    user = request["user"]
    try:
        data = await request.json()
        plan_id = int(data.get("plan_id"))
        proof_data = data.get("proof_data", {})
        ok, msg, res = payment_manager.submit_manual_payment(user["id"], plan_id, proof_data)
        if not ok:
            return web.json_response({"success": False, "error": msg}, status=400)
        return web.json_response({"success": True, "message": msg, "data": res})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_auth
async def handle_giveaway_claim(request):
    user = request["user"]
    try:
        data = await request.json()
        code = data.get("code", "")
        ok, msg, res = payment_manager.claim_giveaway(user["id"], code)
        if not ok:
            return web.json_response({"success": False, "error": msg}, status=400)
        return web.json_response({"success": True, "message": msg, "data": res})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

# ==============================================================================
# Telegram Onboarding APIs
# ==============================================================================
@require_subscription
async def handle_onboarding_status(request):
    user = request["user"]
    st = onboarding_manager.get_onboarding_status(user["id"])
    return web.json_response({"success": True, "status": st})

@require_subscription
async def handle_onboarding_request_code(request):
    user = request["user"]
    try:
        data = await request.json()
        ok, msg, res = await onboarding_manager.request_telegram_code(
            user_id=user["id"],
            api_id_str=data.get("api_id", ""),
            api_hash=data.get("api_hash", ""),
            phone_number=data.get("phone_number", "")
        )
        if not ok:
            return web.json_response({"success": False, "error": msg}, status=400)
        return web.json_response({"success": True, "message": msg, "data": res})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_subscription
async def handle_onboarding_verify_code(request):
    user = request["user"]
    try:
        data = await request.json()
        ok, msg, res = await onboarding_manager.verify_telegram_code(
            user_id=user["id"],
            code=data.get("code", ""),
            password_2fa=data.get("password_2fa")
        )
        if not ok:
            return web.json_response({"success": False, "error": msg, "data": res}, status=400)
        return web.json_response({"success": True, "message": msg, "data": res})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_subscription
async def handle_onboarding_customizer(request):
    user = request["user"]
    try:
        data = await request.json()
        ok, msg, res = onboarding_manager.setup_customizer_and_engine(user["id"], data)
        if not ok:
            return web.json_response({"success": False, "error": msg}, status=400)
        return web.json_response({"success": True, "message": msg, "data": res})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_subscription
async def handle_onboarding_complete(request):
    user = request["user"]
    ok, msg, res = onboarding_manager.complete_onboarding(user["id"])
    return web.json_response({"success": True, "message": msg, "data": res})

@require_subscription
async def handle_onboarding_remove_account(request):
    user = request["user"]
    ok, msg, res = await onboarding_manager.remove_telegram_account(user["id"])
    if not ok:
        return web.json_response({"success": False, "error": msg}, status=400)
    return web.json_response({"success": True, "message": msg, "data": res})

# ==============================================================================
# Multi-Tenant User Bot APIs (Scoped to Authenticated User)
# ==============================================================================
@require_subscription
async def handle_user_bot_status(request):
    user = request["user"]
    return web.json_response(tenant_bot_manager.get_status(user["id"]))

@require_subscription
async def handle_user_bot_start(request):
    user = request["user"]
    ok, msg = await tenant_bot_manager.start_bot(user["id"])
    return web.json_response({
        "success": ok,
        "message": msg,
        "status_data": tenant_bot_manager.get_status(user["id"])
    })

@require_subscription
async def handle_user_bot_stop(request):
    user = request["user"]
    ok = await tenant_bot_manager.stop_bot(user["id"])
    return web.json_response({
        "success": ok,
        "status_data": tenant_bot_manager.get_status(user["id"])
    })

@require_subscription
async def handle_user_bot_restart(request):
    user = request["user"]
    ok, msg = await tenant_bot_manager.restart_bot(user["id"])
    return web.json_response({
        "success": ok,
        "message": msg,
        "status_data": tenant_bot_manager.get_status(user["id"])
    })

@require_subscription
async def handle_user_get_config(request):
    user = request["user"]
    cfg = tenant_bot_manager.get_user_config(user["id"])
    return web.json_response(cfg)

@require_subscription
async def handle_user_save_config(request):
    user = request["user"]
    try:
        data = await request.json()
        ok, msg = await tenant_bot_manager.save_user_config(user["id"], data)
        return web.json_response({"success": ok, "message": msg})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_subscription
async def handle_user_fetch_dialogs(request):
    user = request["user"]
    res = await tenant_bot_manager.fetch_dialogs_status(user["id"])
    return web.json_response(res)

@require_subscription
async def handle_user_channels_toggle(request):
    try:
        user = request["user"]
        data = await request.json()
        action = data.get("action", "add")  # "add" or "remove"
        ch_type = data.get("type", "source")  # "source" or "destination"
        ident = str(data.get("identifier", "")).strip()

        if not ident:
            return web.json_response({"success": False, "error": "Channel identifier is required."}, status=400)

        current_cfg = tenant_bot_manager.get_user_config(user["id"])
        channels = current_cfg.get("channels", {})
        sources = list(channels.get("sources", []))
        destinations = list(channels.get("destinations", []))
        mappings = channels.get("mappings", {})

        # Enforce plan limits
        sub = auth_manager.check_user_subscription(user["id"])
        is_admin = user.get("role") == "admin"
        limit = 9999 if is_admin else int(sub.get("channel_limit", 1))
        plan_name = sub.get("plan_name", "Current Plan")

        if ch_type == "source":
            if action == "add":
                if ident in sources:
                    return web.json_response({
                        "success": True,
                        "message": f"'{ident}' is already in Source channels.",
                        "sources": sources,
                        "destinations": destinations,
                        "sources_count": len(sources),
                        "limit": limit
                    })
                if len(sources) >= limit:
                    return web.json_response({
                        "success": False,
                        "limit_reached": True,
                        "error": f"Plan limit reached: Your '{plan_name}' allows copying from up to {limit} source channel(s). Please upgrade to copy from more channels.",
                        "current_count": len(sources),
                        "limit": limit,
                        "plan_name": plan_name
                    }, status=400)
                sources.append(ident)
            elif action == "remove":
                if ident in sources:
                    sources.remove(ident)
        elif ch_type == "destination":
            if action == "add":
                if ident not in destinations:
                    destinations.append(ident)
            elif action == "remove":
                if ident in destinations:
                    destinations.remove(ident)

        new_cfg = dict(current_cfg)
        new_cfg["channels"] = {
            "sources": sources,
            "destinations": destinations,
            "mappings": mappings
        }
        ok, msg = await tenant_bot_manager.save_user_config(user["id"], new_cfg)
        if not ok:
            return web.json_response({"success": False, "error": msg}, status=400)

        return web.json_response({
            "success": True,
            "action": action,
            "type": ch_type,
            "identifier": ident,
            "sources": sources,
            "destinations": destinations,
            "sources_count": len(sources),
            "destinations_count": len(destinations),
            "limit": limit,
            "message": f"{'Added' if action == 'add' else 'Removed'} {ident} {'to' if action == 'add' else 'from'} {ch_type.capitalize()} channels."
        })
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=500)

@require_subscription
async def handle_user_session_string(request):
    user = request["user"]
    data = onboarding_manager.get_user_bot_data(user["id"])
    session_str = data.get("session_string") if data else None
    if session_str:
        return web.json_response({"success": True, "session_string": session_str})
    return web.json_response({"success": False, "error": "No session string found."}, status=404)

async def handle_user_preview_transform(request):
    try:
        data = await request.json()
        text = data.get("text", "")
        custom_settings = data.get("customizations", None)
        allowed, reason = should_process_message(text, custom_settings)
        result_text = transform_text(text, custom_settings)
        return web.json_response({
            "allowed": allowed,
            "filter_reason": reason,
            "original": text,
            "transformed": result_text
        })
    except Exception as e:
        return web.json_response({"error": str(e)}, status=400)

async def handle_ws_user_logs(request):
    user = get_user_or_none(request)
    if not user:
        return web.Response(text="Unauthorized", status=401)

    ws = web.WebSocketResponse()
    await ws.prepare(request)

    bot = tenant_bot_manager.get_bot(user["id"])
    queue = asyncio.Queue(maxsize=150)
    bot.log_subscribers.add(queue)

    for entry in list(bot.logs):
        await ws.send_json(entry)

    try:
        while not ws.closed:
            try:
                entry = await asyncio.wait_for(queue.get(), timeout=2.0)
                await ws.send_json(entry)
            except asyncio.TimeoutError:
                await ws.ping()
    except Exception:
        pass
    finally:
        bot.log_subscribers.discard(queue)
        await ws.close()

    return ws

# ==============================================================================
# Support Tickets & Newsletter
# ==============================================================================
@require_auth
async def handle_support_get_tickets(request):
    user = request["user"]
    tickets = admin_manager.get_support_tickets(user_id=user["id"])
    return web.json_response({"success": True, "tickets": tickets})

@require_auth
async def handle_support_create_ticket(request):
    user = request["user"]
    try:
        data = await request.json()
        subject = data.get("subject", "").strip()
        message = data.get("message", "").strip()
        priority = data.get("priority", "medium")
        if not subject or not message:
            return web.json_response({"success": False, "error": "Subject and message required."}, status=400)

        tid = admin_manager.create_support_ticket(user["id"], subject, message, priority)
        return web.json_response({"success": True, "ticket_id": tid, "message": "Support ticket created."})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_auth
async def handle_support_get_ticket(request):
    tid = int(request.match_info["id"])
    ticket = admin_manager.get_ticket_details(tid)
    if not ticket:
        return web.json_response({"success": False, "error": "Ticket not found."}, status=404)
    return web.json_response({"success": True, "ticket": ticket})

@require_auth
async def handle_support_reply_ticket(request):
    user = request["user"]
    tid = int(request.match_info["id"])
    try:
        data = await request.json()
        msg = data.get("message", "").strip()
        role = "admin" if user.get("role") == "admin" else "user"
        admin_manager.reply_ticket(tid, user["id"], role, msg)
        return web.json_response({"success": True, "message": "Reply sent."})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

async def handle_newsletter_subscribe(request):
    try:
        data = await request.json()
        email = data.get("email", "").strip()
        if not email or "@" not in email:
            return web.json_response({"success": False, "error": "Invalid email address."}, status=400)
        admin_manager.subscribe_newsletter(email)
        return web.json_response({"success": True, "message": "Subscribed to newsletter updates successfully!"})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

# ==============================================================================
# Admin CRM APIs (All 11 Modules)
# ==============================================================================
@require_admin
async def handle_admin_analytics(request):
    return web.json_response(admin_manager.get_dashboard_analytics())

@require_admin
async def handle_admin_get_users(request):
    search = request.query.get("search", "")
    users = admin_manager.get_users(search=search)
    return web.json_response({"success": True, "users": users})

@require_admin
async def handle_admin_get_user(request):
    try:
        uid = int(request.match_info["id"])
        u = admin_manager.get_user_details(uid)
        if not u:
            return web.json_response({"success": False, "error": "User not found."}, status=404)
        return web.json_response({"success": True, "user": u})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_admin
async def handle_admin_update_user(request):
    try:
        data = await request.json()
        uid = int(data.get("user_id"))
        ok, msg = admin_manager.update_user(uid, data)
        return web.json_response({"success": ok, "message": msg})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_admin
async def handle_admin_delete_user(request):
    try:
        data = await request.json()
        uid = int(data.get("user_id"))
        admin_manager.delete_user(uid)
        return web.json_response({"success": True, "message": "User deleted."})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_admin
async def handle_admin_get_plans(request):
    plans = payment_manager.get_plans(active_only=False)
    return web.json_response({"success": True, "plans": plans})

@require_admin
async def handle_admin_save_plan(request):
    try:
        data = await request.json()
        ok, msg = admin_manager.save_plan(data)
        return web.json_response({"success": ok, "message": msg})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_admin
async def handle_admin_delete_plan(request):
    try:
        data = await request.json()
        pid = int(data.get("plan_id"))
        admin_manager.delete_plan(pid)
        return web.json_response({"success": True, "message": "Plan deleted."})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_admin
async def handle_admin_get_payment_methods(request):
    settings = admin_manager.get_all_site_settings()
    return web.json_response({"success": True, "settings": settings})

@require_admin
async def handle_admin_save_payment_methods(request):
    try:
        data = await request.json()
        admin_manager.save_payment_methods(data)
        return web.json_response({"success": True, "message": "Payment method settings saved."})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_admin
async def handle_admin_get_subscriptions(request):
    status = request.query.get("status", "all")
    subs = admin_manager.get_subscriptions(status=status)
    return web.json_response({"success": True, "subscriptions": subs})

@require_admin
async def handle_admin_approve_subscription(request):
    admin_user = request["user"]
    try:
        data = await request.json()
        sub_id = int(data.get("subscription_id"))
        ok, msg = payment_manager.approve_manual_subscription(sub_id, admin_user["id"])
        return web.json_response({"success": ok, "message": msg})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_admin
async def handle_admin_decline_subscription(request):
    admin_user = request["user"]
    try:
        data = await request.json()
        sub_id = int(data.get("subscription_id"))
        reason = data.get("reason", "")
        ok, msg = payment_manager.decline_manual_subscription(sub_id, admin_user["id"], reason)
        return web.json_response({"success": ok, "message": msg})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_admin
async def handle_admin_get_transactions(request):
    txs = admin_manager.get_transactions()
    return web.json_response({"success": True, "transactions": txs})

@require_admin
async def handle_admin_grant_subscription(request):
    admin_user = request["user"]
    try:
        data = await request.json()
        uid = int(data.get("user_id"))
        pid = int(data.get("plan_id"))
        days = int(data.get("duration_days", 30))
        ok, msg = admin_manager.grant_manual_subscription(admin_user["id"], uid, pid, days)
        return web.json_response({"success": ok, "message": msg})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_admin
async def handle_admin_get_newsletters(request):
    subscribers = admin_manager.get_newsletter_subscribers()
    return web.json_response({"success": True, "subscribers": subscribers})

@require_admin
async def handle_admin_add_newsletter(request):
    try:
        data = await request.json()
        email = data.get("email", "").strip().lower()
        if not email or "@" not in email:
            return web.json_response({"success": False, "error": "Valid email address is required."}, status=400)
        admin_manager.subscribe_newsletter(email)
        return web.json_response({"success": True, "message": f"Subscriber '{email}' added successfully."})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_admin
async def handle_admin_delete_newsletter(request):
    try:
        data = await request.json()
        sid = int(data.get("subscriber_id"))
        admin_manager.delete_newsletter_subscriber(sid)
        return web.json_response({"success": True, "message": "Subscriber removed."})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_admin
async def handle_admin_message_center_send(request):
    admin_user = request["user"]
    try:
        data = await request.json()
        ok, msg = admin_manager.send_broadcast_message(
            admin_id=admin_user["id"],
            recipient_type=data.get("recipient_type", "all"),
            recipient_ids=data.get("recipient_ids", []),
            subject=data.get("subject", ""),
            body=data.get("body", "")
        )
        return web.json_response({"success": ok, "message": msg})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_admin
async def handle_admin_message_center_logs(request):
    logs = admin_manager.get_message_logs()
    return web.json_response({"success": True, "logs": logs})

@require_admin
async def handle_admin_get_tickets(request):
    status = request.query.get("status", "all")
    tickets = admin_manager.get_support_tickets(status=status)
    return web.json_response({"success": True, "tickets": tickets})

@require_admin
async def handle_admin_get_ticket_details(request):
    try:
        tid = int(request.match_info["id"])
        ticket = admin_manager.get_ticket_details(tid)
        if not ticket:
            return web.json_response({"success": False, "error": "Ticket not found."}, status=404)
        return web.json_response({"success": True, "ticket": ticket})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_admin
async def handle_admin_reply_ticket(request):
    admin_user = request["user"]
    try:
        data = await request.json()
        tid = int(data.get("ticket_id"))
        msg = data.get("message", "")
        status = data.get("status")
        admin_manager.reply_ticket(tid, admin_user["id"], "admin", msg, new_status=status)
        return web.json_response({"success": True, "message": "Reply dispatched."})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_admin
async def handle_admin_get_giveaways(request):
    giveaways = admin_manager.get_giveaways()
    return web.json_response({"success": True, "giveaways": giveaways})

@require_admin
async def handle_admin_create_giveaway(request):
    try:
        data = await request.json()
        ok, msg = admin_manager.create_giveaway(data)
        return web.json_response({"success": ok, "message": msg})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_admin
async def handle_admin_toggle_giveaway(request):
    try:
        data = await request.json()
        gid = int(data.get("giveaway_id"))
        active = int(data.get("is_active", 1))
        admin_manager.toggle_giveaway(gid, active)
        return web.json_response({"success": True, "message": f"Giveaway {'activated' if active else 'paused'}."})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_admin
async def handle_admin_delete_giveaway(request):
    try:
        data = await request.json()
        gid = int(data.get("giveaway_id"))
        admin_manager.delete_giveaway(gid)
        return web.json_response({"success": True, "message": "Giveaway deleted."})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_admin
async def handle_admin_get_settings(request):
    settings = admin_manager.get_all_site_settings()
    return web.json_response({"success": True, "settings": settings})

@require_admin
async def handle_admin_save_settings(request):
    try:
        data = await request.json()
        admin_manager.save_payment_methods(data)
        return web.json_response({"success": True, "message": "Site settings updated."})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

@require_admin
async def handle_admin_get_logs(request):
    logs = admin_manager.get_system_logs()
    return web.json_response({"success": True, "logs": logs})

@require_admin
async def handle_admin_clear_logs(request):
    try:
        admin_manager.clear_system_logs()
        return web.json_response({"success": True, "message": "Audit logs cleared."})
    except Exception as e:
        return web.json_response({"success": False, "error": str(e)}, status=400)

# ==============================================================================
# Application Factory
# ==============================================================================
def create_app() -> web.Application:
    app = web.Application()

    # Public Page Routes (Dedicated Separate Pages)
    app.router.add_get("/", handle_page_home)
    app.router.add_get("/home", handle_page_home)
    app.router.add_get("/pricing", handle_page_pricing)
    app.router.add_get("/plans", handle_page_pricing)
    app.router.add_get("/dashboard", handle_page_dashboard)
    app.router.add_get("/channels", handle_page_channels)
    app.router.add_get("/rules", handle_page_rules)
    app.router.add_get("/customizer", handle_page_rules)
    app.router.add_get("/logs", handle_page_logs)
    app.router.add_get("/onboarding", handle_page_onboarding)
    app.router.add_get("/support", handle_page_support)
    app.router.add_get("/admin", handle_page_admin)

    # Health Checks
    app.router.add_get("/healthz", handle_healthz)
    app.router.add_get("/api/health", handle_healthz)

    # Auth Routes
    app.router.add_post("/api/auth/register", handle_auth_register)
    app.router.add_post("/api/auth/login", handle_auth_login)
    app.router.add_get("/api/auth/me", handle_auth_me)
    app.router.add_post("/api/auth/logout", handle_auth_logout)
    app.router.add_post("/api/auth/impersonate", handle_auth_impersonate)
    app.router.add_post("/api/auth/exit-impersonation", handle_auth_exit_impersonation)

    # Billing & Plans
    app.router.add_get("/api/plans", handle_get_plans)
    app.router.add_get("/api/payment/methods", handle_get_payment_methods)
    app.router.add_post("/api/payment/initialize", handle_payment_initialize)
    app.router.add_get("/api/payment/verify", handle_payment_verify)
    app.router.add_get("/api/payment/test-verify", handle_payment_verify)
    app.router.add_post("/api/payment/manual-submit", handle_payment_manual_submit)
    app.router.add_post("/api/payment/giveaway/claim", handle_giveaway_claim)

    # Onboarding
    app.router.add_get("/api/onboarding/status", handle_onboarding_status)
    app.router.add_post("/api/onboarding/request-code", handle_onboarding_request_code)
    app.router.add_post("/api/onboarding/verify-code", handle_onboarding_verify_code)
    app.router.add_post("/api/onboarding/customizer", handle_onboarding_customizer)
    app.router.add_post("/api/onboarding/complete", handle_onboarding_complete)
    app.router.add_post("/api/onboarding/remove-account", handle_onboarding_remove_account)
    app.router.add_post("/api/user/telegram/disconnect", handle_onboarding_remove_account)

    # User Bot Operations
    app.router.add_get("/api/user/status", handle_user_bot_status)
    app.router.add_post("/api/user/bot/start", handle_user_bot_start)
    app.router.add_post("/api/user/bot/stop", handle_user_bot_stop)
    app.router.add_post("/api/user/bot/restart", handle_user_bot_restart)
    app.router.add_get("/api/user/config", handle_user_get_config)
    app.router.add_post("/api/user/config", handle_user_save_config)
    app.router.add_get("/api/user/dialogs", handle_user_fetch_dialogs)
    app.router.add_post("/api/user/channels/toggle", handle_user_channels_toggle)
    app.router.add_get("/api/user/session-string", handle_user_session_string)
    app.router.add_post("/api/preview", handle_user_preview_transform)
    app.router.add_get("/ws/logs", handle_ws_user_logs)
    app.router.add_get("/ws/user/logs", handle_ws_user_logs)

    # Support & Newsletter
    app.router.add_get("/api/support/tickets", handle_support_get_tickets)
    app.router.add_post("/api/support/tickets", handle_support_create_ticket)
    app.router.add_get("/api/support/tickets/{id}", handle_support_get_ticket)
    app.router.add_post("/api/support/tickets/{id}/reply", handle_support_reply_ticket)
    app.router.add_post("/api/newsletter/subscribe", handle_newsletter_subscribe)

    # Admin CRM Routes
    app.router.add_get("/api/admin/analytics", handle_admin_analytics)
    app.router.add_get("/api/admin/users", handle_admin_get_users)
    app.router.add_get("/api/admin/users/{id}", handle_admin_get_user)
    app.router.add_post("/api/admin/users/update", handle_admin_update_user)
    app.router.add_post("/api/admin/users/status", handle_admin_update_user)
    app.router.add_post("/api/admin/users/delete", handle_admin_delete_user)
    app.router.add_get("/api/admin/plans", handle_admin_get_plans)
    app.router.add_post("/api/admin/plans/save", handle_admin_save_plan)
    app.router.add_post("/api/admin/plans/delete", handle_admin_delete_plan)
    app.router.add_get("/api/admin/payment-methods", handle_admin_get_payment_methods)
    app.router.add_post("/api/admin/payment-methods/save", handle_admin_save_payment_methods)
    app.router.add_get("/api/admin/subscriptions", handle_admin_get_subscriptions)
    app.router.add_post("/api/admin/subscriptions/grant", handle_admin_grant_subscription)
    app.router.add_post("/api/admin/subscriptions/approve", handle_admin_approve_subscription)
    app.router.add_post("/api/admin/subscriptions/decline", handle_admin_decline_subscription)
    app.router.add_get("/api/admin/transactions", handle_admin_get_transactions)
    app.router.add_get("/api/admin/newsletter", handle_admin_get_newsletters)
    app.router.add_post("/api/admin/newsletter/add", handle_admin_add_newsletter)
    app.router.add_post("/api/admin/newsletter/delete", handle_admin_delete_newsletter)
    app.router.add_post("/api/admin/message-center/send", handle_admin_message_center_send)
    app.router.add_get("/api/admin/message-center/logs", handle_admin_message_center_logs)
    app.router.add_get("/api/admin/tickets", handle_admin_get_tickets)
    app.router.add_get("/api/admin/tickets/{id}", handle_admin_get_ticket_details)
    app.router.add_post("/api/admin/tickets/reply", handle_admin_reply_ticket)
    app.router.add_get("/api/admin/giveaways", handle_admin_get_giveaways)
    app.router.add_post("/api/admin/giveaways/create", handle_admin_create_giveaway)
    app.router.add_post("/api/admin/giveaways/toggle", handle_admin_toggle_giveaway)
    app.router.add_post("/api/admin/giveaways/delete", handle_admin_delete_giveaway)
    app.router.add_get("/api/admin/settings", handle_admin_get_settings)
    app.router.add_post("/api/admin/settings/save", handle_admin_save_settings)
    app.router.add_get("/api/admin/logs", handle_admin_get_logs)
    app.router.add_post("/api/admin/logs/clear", handle_admin_clear_logs)

    # Static assets
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.exists(static_dir):
        app.router.add_static("/static/", path=static_dir, name="static")

    return app

async def start_web_server():
    host = os.getenv("WEB_HOST", "0.0.0.0")
    port = int(os.getenv("PORT", 10000))

    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"🌐 TeleMirror running at http://{host}:{port}")

    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(start_web_server())
    except (KeyboardInterrupt, SystemExit):
        logger.info("TeleMirror server stopped.")
