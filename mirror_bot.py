import os
import sys
import asyncio
import logging
from typing import Dict, List, Any, Union

from telethon import TelegramClient, events, errors
from telethon.sessions import StringSession
from telethon.tl.custom import Message
from telethon.tl.types import (
    MessageMediaPoll,
    MessageMediaWebPage,
    MessageMediaDice,
    MessageMediaContact,
    MessageMediaGeo
)

import config
from cleaner import should_process_message, transform_text, is_image_message
from album_manager import AlbumManager

# ==============================================================================
# Logging Configuration
# ==============================================================================
log_level = getattr(logging, config.LOG_LEVEL, logging.INFO)
logging.basicConfig(
    level=log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(config.LOG_FILE, encoding="utf-8")
    ]
)
logger = logging.getLogger("MirrorBot")

# ==============================================================================
# Global State & Client Setup
# ==============================================================================
routing_map: Dict[Union[str, int], List[Union[str, int]]] = {}
resolved_sources: Dict[int, Any] = {}
resolved_destinations: Dict[int, Any] = {}
# Mapping: (source_chat_id, source_msg_id) -> List[(dest_chat_id, dest_msg_id)]
message_history_map: Dict[tuple, List[tuple]] = {}

client: TelegramClient = None

# ==============================================================================
# Helper Functions
# ==============================================================================
async def safe_send_action(action_func, *args, **kwargs):
    """
    Executes a Telegram send action with automatic FloodWait recovery.
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            res = await action_func(*args, **kwargs)
            if config.ACTION_DELAY > 0:
                await asyncio.sleep(config.ACTION_DELAY)
            return res
        except errors.FloodWaitError as e:
            logger.warning(f"⚠️ FloodWaitError: Telegram requires waiting for {e.seconds}s. Sleeping...")
            await asyncio.sleep(e.seconds + 1)
        except errors.ChatWriteForbiddenError:
            logger.error(f"❌ Error: Cannot post to destination. Verify user account is an admin with post permissions!")
            break
        except Exception as e:
            logger.error(f"❌ Send failed (attempt {attempt + 1}/{max_retries}): {e}", exc_info=True)
            await asyncio.sleep(2.0)
    return None

def extract_channel_id(entity) -> int:
    """Safely get numeric channel ID for comparison."""
    if hasattr(entity, 'id'):
        return entity.id
    return 0

# ==============================================================================
# Message Processing
# ==============================================================================
async def copy_single_message(message: Message, dest_entity):
    """Copies or forwards a single message to a destination entity."""
    # Whitelist / Blacklist check
    allowed, reason = should_process_message(message.raw_text or message.message)
    if not allowed:
        logger.info(f"⏭️ Skipping message #{message.id} ({reason})")
        return None

    dest_name = getattr(dest_entity, 'title', str(dest_entity))
    logger.info(f"📤 Processing message #{message.id} -> '{dest_name}'")

    is_image = is_image_message(message)

    if config.COPY_MODE == "forward":
        if config.REMOVE_IMAGES and is_image:
            caption_or_text = transform_text(message.raw_text or message.message)
            if not caption_or_text:
                logger.info(f"⏭️ Skipping message #{message.id} (Image stripped and no text caption)")
                return None
            logger.info(f"🖼️ Removed image on message #{message.id} -> posting text caption only")
            sent = await safe_send_action(
                client.send_message,
                entity=dest_entity,
                message=caption_or_text,
                parse_mode='html',
                link_preview=not config.REMOVE_LINKS
            )
            return sent
        else:
            sent = await safe_send_action(
                client.forward_messages,
                entity=dest_entity,
                messages=message
            )
            return sent

    # Clean Copy mode (reposts without forwarded tag)
    caption_or_text = transform_text(message.raw_text or message.message)

    if config.REMOVE_IMAGES and is_image:
        if not caption_or_text:
            logger.info(f"⏭️ Skipping message #{message.id} (Image stripped and no text caption)")
            return None
        logger.info(f"🖼️ Removed image on message #{message.id} -> posting text caption only")
        sent = await safe_send_action(
            client.send_message,
            entity=dest_entity,
            message=caption_or_text,
            parse_mode='html',
            link_preview=not config.REMOVE_LINKS
        )
        return sent

    # 1. Message with standard media (photo, video, document, audio, voice, sticker)
    if message.media and not isinstance(message.media, (MessageMediaWebPage, MessageMediaPoll, MessageMediaDice, MessageMediaContact, MessageMediaGeo)):
        sent = await safe_send_action(
            client.send_file,
            entity=dest_entity,
            file=message.media,
            caption=caption_or_text,
            parse_mode='html'
        )
        return sent

    # 2. Message is a poll
    elif isinstance(message.media, MessageMediaPoll):
        poll = message.media.poll
        answers = [ans.text for ans in poll.answers]
        sent = await safe_send_action(
            client.send_message,
            entity=dest_entity,
            file=message.media
        )
        return sent

    # 3. Plain text message or WebPage preview
    else:
        if not caption_or_text:
            return None
        sent = await safe_send_action(
            client.send_message,
            entity=dest_entity,
            message=caption_or_text,
            parse_mode='html',
            link_preview=not config.REMOVE_LINKS
        )
        return sent

async def handle_album_batch(grouped_id: int, messages: List[Message]):
    """Handles an entire album group of media messages."""
    if not messages:
        return

    first_msg = messages[0]
    chat_id = first_msg.chat_id or (first_msg.peer_id.channel_id if hasattr(first_msg.peer_id, 'channel_id') else 0)

    # Determine destinations for this source chat
    destinations = get_destinations_for_chat(chat_id)
    if not destinations:
        return

    # Check blacklist/whitelist using combined text
    combined_text = " ".join([m.raw_text or m.message or "" for m in messages if m.raw_text or m.message])
    allowed, reason = should_process_message(combined_text)
    if not allowed:
        logger.info(f"⏭️ Skipping album group {grouped_id} ({reason})")
        return

    # Find the main caption across messages
    main_caption = ""
    for m in messages:
        if m.raw_text or m.message:
            main_caption = m.raw_text or m.message
            break

    transformed_caption = transform_text(main_caption)
    
    if config.REMOVE_IMAGES:
        non_image_messages = [m for m in messages if not is_image_message(m)]
        if not non_image_messages:
            if not transformed_caption:
                logger.info(f"⏭️ Skipping album group {grouped_id} (All media were images and REMOVE_IMAGES is enabled, no text caption)")
                return
            for dest in destinations:
                dest_name = getattr(dest, 'title', str(dest))
                logger.info(f"🖼️ Removed all images from album #{grouped_id} -> posting text caption only to '{dest_name}'")
                await safe_send_action(
                    client.send_message,
                    entity=dest,
                    message=transformed_caption,
                    parse_mode='html',
                    link_preview=not config.REMOVE_LINKS
                )
            return
        else:
            media_files = [m.media for m in non_image_messages if m.media]
    else:
        media_files = [m.media for m in messages if m.media]

    for dest in destinations:
        dest_name = getattr(dest, 'title', str(dest))
        logger.info(f"📸 Sending album ({len(media_files)} items) from group {grouped_id} -> '{dest_name}'")
        
        if config.COPY_MODE == "forward":
            await safe_send_action(
                client.forward_messages,
                entity=dest,
                messages=[m.id for m in messages],
                from_peer=chat_id
            )
        else:
            await safe_send_action(
                client.send_file,
                entity=dest,
                file=media_files,
                caption=transformed_caption,
                parse_mode='html'
            )

album_manager = AlbumManager(callback=handle_album_batch)

def get_destinations_for_chat(chat_id: int) -> List[Any]:
    """Finds all resolved destination entities for a given source chat ID."""
    dests = []
    # Try direct numeric match
    if chat_id in routing_map:
        for d in routing_map[chat_id]:
            if d in resolved_destinations:
                dests.append(resolved_destinations[d])
    
    # Try match with -100 prefix if applicable
    prefixed_id = int(f"-100{chat_id}") if not str(chat_id).startswith("-100") else chat_id
    if prefixed_id in routing_map:
        for d in routing_map[prefixed_id]:
            if d in resolved_destinations:
                dests.append(resolved_destinations[d])

    # Also search by username if mapped
    for src_key, dst_list in routing_map.items():
        if isinstance(src_key, str) and src_key in resolved_sources:
            if resolved_sources[src_key].id == chat_id or f"-100{resolved_sources[src_key].id}" == str(chat_id):
                for d in dst_list:
                    if d in resolved_destinations:
                        dests.append(resolved_destinations[d])

    return dests

# ==============================================
# Telethon Event Handlers
# ==============================================
async def on_new_message(event: events.NewMessage.Event):
    """Triggered on every new message in any chat."""
    message = event.message
    chat_id = event.chat_id

    # Check if this chat is one of our monitored sources
    destinations = get_destinations_for_chat(chat_id)
    if not destinations:
        return

    # Handle albums (media groups)
    if message.grouped_id:
        await album_manager.add_message(message)
        return

    # Handle single messages
    for dest in destinations:
        sent = await copy_single_message(message, dest)
        if sent and config.MIRROR_EDITS:
            key = (chat_id, message.id)
            if key not in message_history_map:
                message_history_map[key] = []
            sent_id = sent.id if hasattr(sent, 'id') else (sent[0].id if isinstance(sent, list) and sent else None)
            if sent_id:
                dest_id = getattr(dest, 'id', None)
                if dest_id:
                    message_history_map[key].append((dest_id, sent_id))

async def on_message_edited(event: events.MessageEdited.Event):
    """Handles message edit events to sync updates."""
    if not config.MIRROR_EDITS:
        return

    message = event.message
    chat_id = event.chat_id
    key = (chat_id, message.id)

    if key in message_history_map:
        new_text = transform_text(message.raw_text or message.message)
        for dest_id, dest_msg_id in message_history_map[key]:
            try:
                logger.info(f"✏️ Syncing edit for message #{message.id} -> dest #{dest_msg_id}")
                await client.edit_message(
                    entity=dest_id,
                    message=dest_msg_id,
                    text=new_text,
                    parse_mode='html'
                )
            except Exception as e:
                logger.warning(f"Could not edit message #{dest_msg_id}: {e}")

# ==============================================================================
# Initialization and Startup
# ==============================================================================
async def initialize():
    """Initializes channels and verifies access permissions."""
    global client, routing_map
    routing_map = config.get_routing_map()

    logger.info("=" * 60)
    logger.info("🚀 Starting Telegram Channel Mirror Bot")
    logger.info("=" * 60)

    # 1. Connect and authenticate
    if config.BOT_TOKEN:
        session = StringSession()
        logger.info("🤖 Using Bot Token for authentication.")
    elif config.SESSION_STRING:
        session = StringSession(config.SESSION_STRING)
        logger.info("🔑 Using StringSession for authentication.")
    else:
        session = config.SESSION_NAME

    client = TelegramClient(
        session,
        config.API_ID,
        config.API_HASH
    )
    client.add_event_handler(on_new_message, events.NewMessage)
    client.add_event_handler(on_message_edited, events.MessageEdited)

    if config.BOT_TOKEN:
        await client.start(bot_token=config.BOT_TOKEN)
    elif config.SESSION_STRING:
        await client.connect()
        if not await client.is_user_authorized():
            logger.error("❌ Provided SESSION_STRING is not authorized or has expired.")
            sys.exit(1)
    else:
        await client.start(phone=config.PHONE_NUMBER)

    me = await client.get_me()
    logger.info(f"✅ Logged in as: {me.first_name} (@{me.username or 'NoUsername'}) [ID: {me.id}]")

    # 2. Resolve Source Entities
    logger.info("\n🔍 Resolving Source Channels:")
    for src in routing_map.keys():
        try:
            entity = await client.get_entity(src)
            resolved_sources[src] = entity
            channel_id = getattr(entity, 'id', 'N/A')
            title = getattr(entity, 'title', str(src))
            logger.info(f"   [+] Monitoring: '{title}' (ID: {channel_id}, Handle: {src})")
        except Exception as e:
            logger.error(f"   [-] Could not resolve source '{src}': {e}")

    # 3. Resolve Destination Entities
    logger.info("\n🎯 Resolving Destination Channels:")
    all_dests = {dst for dst_list in routing_map.values() for dst in dst_list}
    for dst in all_dests:
        try:
            entity = await client.get_entity(dst)
            resolved_destinations[dst] = entity
            channel_id = getattr(entity, 'id', 'N/A')
            title = getattr(entity, 'title', str(dst))
            logger.info(f"   [+] Posting to: '{title}' (ID: {channel_id}, Handle: {dst})")
        except Exception as e:
            logger.error(f"   [-] Could not resolve destination '{dst}': {e}")
            logger.error(f"       Tip: Ensure your user account joined '{dst}' and is an admin with Post Messages permission.")

    logger.info("\n" + "=" * 60)
    logger.info(f"📡 Bot is live and listening for new messages in {len(resolved_sources)} sources...")
    logger.info("   Press Ctrl+C to stop.")
    logger.info("=" * 60 + "\n")

async def main():
    errors_list = config.validate_config()
    if errors_list:
        logger.error("Configuration Errors Found:")
        for err in errors_list:
            logger.error(f"  • {err}")
        logger.info("\nPlease update the `.env` file with your settings before running.")
        sys.exit(1)

    await initialize()
    await client.run_until_disconnected()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("\n👋 Mirror Bot stopped cleanly.")
