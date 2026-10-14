import asyncio
import logging
from typing import Dict, List, Callable, Any, Optional
from telethon.tl.custom import Message

class AlbumManager:
    """
    Buffers and handles grouped messages (media albums in Telegram)
    so they are delivered together in a single album rather than separate posts.
    Supports both callback-based handling and direct client forwarding.
    """
    def __init__(
        self,
        callback: Optional[Callable[[int, List[Message]], Any]] = None,
        wait_time: float = 1.5,
        client: Optional[Any] = None,
        transform_fn: Optional[Callable] = None,
        should_process_fn: Optional[Callable] = None,
        get_targets_fn: Optional[Callable] = None,
        custom_settings: Optional[Dict[str, Any]] = None,
        copy_mode: str = "clean_copy",
        logger: Optional[Any] = None,
        **kwargs
    ):
        self.callback = callback
        self.wait_time = wait_time
        self.client = client
        self.transform_fn = transform_fn
        self.should_process_fn = should_process_fn
        self.get_targets_fn = get_targets_fn
        self.custom_settings = custom_settings or {}
        self.copy_mode = copy_mode
        self.logger = logger or logging.getLogger("AlbumManager")
        self.album_buffers: Dict[int, List[Message]] = {}
        self.album_tasks: Dict[int, asyncio.Task] = {}

    async def handle_album_message(self, message: Message):
        """Alias for add_message to support both calling patterns."""
        await self.add_message(message)

    async def add_message(self, message: Message):
        grouped_id = message.grouped_id
        if not grouped_id:
            return

        if grouped_id not in self.album_buffers:
            self.album_buffers[grouped_id] = []
            # Start timer task for this album group
            self.album_tasks[grouped_id] = asyncio.create_task(self._wait_and_flush(grouped_id))

        self.album_buffers[grouped_id].append(message)

    async def _wait_and_flush(self, grouped_id: int):
        await asyncio.sleep(self.wait_time)
        messages = self.album_buffers.pop(grouped_id, [])
        self.album_tasks.pop(grouped_id, None)

        if not messages:
            return

        # Sort messages in their original order by message id
        messages.sort(key=lambda m: m.id)

        # 1. Use callback if provided
        if self.callback:
            try:
                await self.callback(grouped_id, messages)
            except Exception as e:
                self.logger.error(f"Error handling album group {grouped_id} via callback: {e}", exc_info=True)
            return

        # 2. Use direct client forwarding if client is provided
        if self.client:
            try:
                first_msg = messages[0]
                targets = self.get_targets_fn(first_msg) if self.get_targets_fn else []
                if not targets:
                    return

                combined_text = " ".join([m.raw_text or m.message or "" for m in messages if m.raw_text or m.message])
                if self.should_process_fn:
                    allowed, reason = self.should_process_fn(combined_text, self.custom_settings)
                    if not allowed:
                        self.logger.info(f"Skipping album #{grouped_id} ({reason})")
                        return

                main_caption = ""
                for m in messages:
                    if m.raw_text or m.message:
                        main_caption = m.raw_text or m.message
                        break

                caption = self.transform_fn(main_caption, self.custom_settings) if self.transform_fn else main_caption
                media_files = [m.media for m in messages if m.media]

                for dest in targets:
                    try:
                        if media_files:
                            await self.client.send_file(dest, media_files, caption=caption)
                        elif caption:
                            await self.client.send_message(dest, caption)
                    except Exception as err:
                        self.logger.error(f"Error mirroring album to {dest}: {err}")
            except Exception as e:
                self.logger.error(f"Error processing album #{grouped_id}: {e}", exc_info=True)
