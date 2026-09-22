import sys
import os
import asyncio
from telethon import TelegramClient

# Add parent directory to path so we can import config
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
import config

async def list_user_channels():
    if not config.API_ID or not config.API_HASH or not config.PHONE_NUMBER:
        print("❌ Error: API_ID, API_HASH, or PHONE_NUMBER missing in .env")
        return

    client = TelegramClient(config.SESSION_NAME, config.API_ID, config.API_HASH)
    await client.start(phone=config.PHONE_NUMBER)

    print("\n" + "=" * 70)
    print("📋 YOUR ACCESSIBLE TELEGRAM CHATS / CHANNELS / GROUPS")
    print("=" * 70)
    print(f"{'TITLE':<30} | {'USERNAME':<20} | {'CHANNEL ID':<15}")
    print("-" * 70)

    async for dialog in client.iter_dialogs():
        if dialog.is_channel or dialog.is_group:
            title = dialog.title[:28] if dialog.title else "No Title"
            username = f"@{dialog.entity.username}" if hasattr(dialog.entity, 'username') and dialog.entity.username else "N/A (Private)"
            chat_id = dialog.id
            print(f"{title:<30} | {username:<20} | {chat_id:<15}")

    print("=" * 70)
    print("💡 Tip: You can use either the @username or the numeric CHANNEL ID in .env")
    await client.disconnect()

if __name__ == "__main__":
    asyncio.run(list_user_channels())
