import sys
import os
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession, SQLiteSession

# Add parent directory to path so we can import config & settings_manager
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import config
from settings_manager import settings_manager

async def main():
    print("\n" + "=" * 70)
    print("🔑 TELEMIRROR PRO • TELEGRAM STRING SESSION EXPORTER")
    print("=" * 70)
    print("Telethon StringSession is required for cloud platforms like Render")
    print("to run headlessly without prompting for login codes.\n")

    tg_cfg = settings_manager.get("telegram", {})
    api_id_val = tg_cfg.get("api_id") or os.getenv("API_ID", "")
    api_hash = tg_cfg.get("api_hash") or os.getenv("API_HASH", "")
    phone = tg_cfg.get("phone_number") or os.getenv("PHONE_NUMBER", "")
    session_name = tg_cfg.get("session_name") or os.getenv("SESSION_NAME", "telegram_mirror_session")

    # 1. First, check if SQLite session file exists and convert it directly
    session_file = f"{session_name}.session"
    if not os.path.exists(session_file) and not session_file.endswith(".session"):
        session_file = f"{session_name}"

    string_session_val = None

    if os.path.exists(session_file):
        print(f"📁 Found existing local session file: '{session_file}'")
        try:
            clean_name = session_name[:-8] if session_name.endswith(".session") else session_name
            s_sql = SQLiteSession(clean_name)
            s_str = StringSession()
            s_str._server_address = s_sql.server_address
            s_str._port = s_sql.port
            s_str._auth_key = s_sql.auth_key
            s_str._takeout_id = s_sql.takeout_id
            encoded = s_str.save()
            if encoded:
                string_session_val = encoded
                print("✅ Successfully exported StringSession from local session file!")
        except Exception as e:
            print(f"⚠️ Could not directly convert SQLite session file: {e}")

    # 2. If conversion failed or file not found, login interactively to generate
    if not string_session_val:
        if not api_id_val or not api_hash:
            print("❌ Error: API_ID and API_HASH are required to authenticate.")
            print("Please configure them in settings.json or .env first.")
            return

        api_id = int(api_id_val)
        print("🔐 Starting interactive login to generate a new StringSession...")
        client = TelegramClient(StringSession(), api_id, api_hash)
        await client.start(phone=phone if phone else None)
        string_session_val = client.session.save()
        me = await client.get_me()
        print(f"✅ Logged in as: {me.first_name} (@{me.username or 'NoUsername'})")
        await client.disconnect()

    # 3. Print the result
    border = "-" * 70
    print(f"\n{border}")
    print("📋 YOUR TELETHON STRING SESSION (KEEP PRIVATE!):")
    print(border)
    print(f"\n{string_session_val}\n")
    print(border)
    print("🚀 HOW TO USE ON RENDER:")
    print("1. Go to your Render Dashboard (https://dashboard.render.com)")
    print("2. Open your TeleMirror Web Service -> Environment")
    print("3. Add Environment Variable:")
    print("   Key:   SESSION_STRING")
    print(f"   Value: {string_session_val}")
    print("4. Save Changes & Deploy. Your bot will connect instantly!")
    print(f"{border}\n")

    # Update in settings.json as well for local persistence
    tg_cfg["session_string"] = string_session_val
    settings_manager.update_section("telegram", tg_cfg)
    print("💾 Also saved SESSION_STRING to local settings.json.")

if __name__ == "__main__":
    asyncio.run(main())
