"""
TeleMirror - cPanel / CloudLinux Passenger WSGI Entrypoint
If running via cPanel "Setup Python App" with Phusion Passenger:
This script boots the TeleMirror asynchronous web server and background engine.
"""

import os
import sys
import threading
import time

# Add current project directory to sys.path
CWD = os.path.dirname(os.path.abspath(__file__))
if CWD not in sys.path:
    sys.path.insert(0, CWD)

# Start background server daemon if not already running
def run_app_daemon():
    import asyncio
    from web_server import start_web_server
    try:
        asyncio.run(start_web_server())
    except Exception as e:
        print(f"TeleMirror server daemon error: {e}", file=sys.stderr)

daemon_thread = threading.Thread(target=run_app_daemon, daemon=True)
daemon_thread.start()

# Provide standard WSGI callable for Passenger health check / entry
def application(environ, start_response):
    status = '200 OK'
    headers = [
        ('Content-Type', 'text/html; charset=utf-8'),
        ('Refresh', '2; url=/')
    ]
    start_response(status, headers)
    html = """<!DOCTYPE html>
<html>
<head><title>TeleMirror Running</title></head>
<body style="font-family:sans-serif;text-align:center;padding:50px;background:#030712;color:#22d3ee;">
    <h2>🚀 TeleMirror Engine is Active!</h2>
    <p>Redirecting to dashboard...</p>
    <p><a href="/" style="color:#a855f7;">Click here if not redirected automatically</a></p>
</body>
</html>"""
    return [html.encode('utf-8')]
