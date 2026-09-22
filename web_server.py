import os
import json
import asyncio
import logging
from aiohttp import web
import aiohttp

import sys
from settings_manager import settings_manager
from bot_engine import bot_engine, log_broadcaster
from cleaner import transform_text, should_process_message

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("WebServer")

# ==============================================================================
# API Handlers
# ==============================================================================
async def handle_index(request):
    html_file = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if os.path.exists(html_file):
        with open(html_file, "r", encoding="utf-8") as f:
            return web.Response(text=f.read(), content_type="text/html")
    return web.Response(text="<h1>Admin Dashboard template not found.</h1>", content_type="text/html")

async def handle_get_status(request):
    return web.json_response(bot_engine.get_status())

async def handle_bot_start(request):
    success = await bot_engine.start()
    return web.json_response({
        "success": success,
        "status": bot_engine.status,
        "error": bot_engine.error_message
    })

async def handle_bot_stop(request):
    success = await bot_engine.stop()
    return web.json_response({
        "success": success,
        "status": bot_engine.status
    })

async def handle_bot_restart(request):
    success = await bot_engine.restart()
    return web.json_response({
        "success": success,
        "status": bot_engine.status,
        "error": bot_engine.error_message
    })

async def handle_get_settings(request):
    return web.json_response(settings_manager.get_all())

async def handle_save_settings(request):
    try:
        data = await request.json()
        settings_manager.update_all(data)
        logger.info("⚙️ Settings updated via Web Admin.")
        
        # If bot is running, re-resolve channels to apply immediately
        if bot_engine.status == "running" and bot_engine.client:
            asyncio.create_task(bot_engine._resolve_channels())
            
        return web.json_response({"success": True, "message": "Settings saved successfully."})
    except Exception as e:
        logger.error(f"Error saving settings: {e}")
        return web.json_response({"success": False, "error": str(e)}, status=400)

async def handle_get_dialogs(request):
    if bot_engine.status != "running":
        return web.json_response({
            "success": False,
            "error": "Bot engine must be running to discover Telegram channels."
        }, status=400)
    
    dialogs = await bot_engine.fetch_user_dialogs()
    return web.json_response({"success": True, "dialogs": dialogs})

async def handle_preview_transform(request):
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

# ==============================================================================
# WebSocket for Live Logs
# ==============================================================================
async def handle_ws_logs(request):
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    queue = asyncio.Queue(maxsize=100)
    log_broadcaster.subscribe(queue)

    # Send historical logs first
    for log in list(log_broadcaster.logs):
        await ws.send_json(log)

    try:
        while not ws.closed:
            try:
                log_entry = await asyncio.wait_for(queue.get(), timeout=2.0)
                await ws.send_json(log_entry)
            except asyncio.TimeoutError:
                # Keepalive ping
                await ws.ping()
    except Exception:
        pass
    finally:
        log_broadcaster.unsubscribe(queue)
        await ws.close()

    return ws

# ==============================================================================
# Health & Cloud Helper Endpoints
# ==============================================================================
async def handle_healthz(request):
    """Health check endpoint for Render and Uptime monitors."""
    return web.json_response({
        "status": "healthy",
        "service": "TeleMirror Pro",
        "bot_engine": bot_engine.status
    })

async def handle_export_session_string(request):
    """Returns the StringSession for deployment on Render/Cloud."""
    session_str = bot_engine.export_session_string()
    if session_str:
        return web.json_response({
            "success": True,
            "session_string": session_str,
            "message": "Copy this SESSION_STRING into your Render Environment Variables."
        })
    return web.json_response({
        "success": False,
        "error": "No authenticated session found to export. Please log in first."
    }, status=404)

# ==============================================================================
# Application Factory
# ==============================================================================
def create_app() -> web.Application:
    app = web.Application()
    
    # Public & Health Check Routes
    app.router.add_get("/", handle_index)
    app.router.add_get("/healthz", handle_healthz)
    app.router.add_get("/api/health", handle_healthz)
    
    # Bot Engine & Control APIs
    app.router.add_get("/api/status", handle_get_status)
    app.router.add_post("/api/bot/start", handle_bot_start)
    app.router.add_post("/api/bot/stop", handle_bot_stop)
    app.router.add_post("/api/bot/restart", handle_bot_restart)
    app.router.add_get("/api/settings", handle_get_settings)
    app.router.add_post("/api/settings", handle_save_settings)
    app.router.add_get("/api/dialogs", handle_get_dialogs)
    app.router.add_post("/api/preview", handle_preview_transform)
    app.router.add_get("/api/session-string", handle_export_session_string)
    app.router.add_get("/ws/logs", handle_ws_logs)

    # Static assets
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    if os.path.exists(static_dir):
        app.router.add_static("/static/", path=static_dir, name="static")

    return app

async def start_web_server():
    web_cfg = settings_manager.get("web", {})
    host = os.getenv("WEB_HOST", web_cfg.get("host", "0.0.0.0"))
    port = int(os.getenv("PORT", web_cfg.get("port", 8080)))

    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"🌐 Web Admin Dashboard running at http://{host}:{port}")

    # Auto-start bot if configured
    if settings_manager.get("bot", {}).get("auto_start", True):
        asyncio.create_task(bot_engine.start())

    # Keep server running
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    try:
        asyncio.run(start_web_server())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Web Admin server stopped.")
