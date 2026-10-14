import sys
import socket
import asyncio
import logging
from settings_manager import settings_manager

# Configure root logger with standard formatting to console
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("Launcher")

def get_local_ip() -> str:
    """Helper to detect local LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

def print_banner(host: str, port: int):
    local_ip = get_local_ip()
    border = "=" * 62
    print(f"\n{border}")
    print("   🚀 TeleMirror • Multi-Tenant SaaS & Telegram Engine")
    print(border)
    print(f"   🌐 Local Access:    http://localhost:{port}")
    if local_ip != "127.0.0.1":
        print(f"   📱 LAN / Mobile:    http://{local_ip}:{port}")
    print(f"   📡 Host Binding:    http://{host}:{port}")
    print("-" * 62)
    print("   💡 Open the link above in any browser to manage the bot.")
    print("   🛑 Press Ctrl+C in this terminal to stop the server.")
    print(f"{border}\n", flush=True)

if __name__ == "__main__":
    from web_server import start_web_server

    import os
    web_cfg = settings_manager.get("web", {})
    host = os.getenv("WEB_HOST", web_cfg.get("host", "0.0.0.0"))
    port = int(os.getenv("PORT", web_cfg.get("port", 8080)))

    print_banner(host, port)

    try:
        asyncio.run(start_web_server())
    except (KeyboardInterrupt, SystemExit):
        print("\n👋 TeleMirror stopped cleanly.")
