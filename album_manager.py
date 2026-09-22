import asyncio
import logging
from typing import Dict, List, Callable, Any
from telethon.tl.custom import Message

class AlbumManager:
    """
    Buffers and handles grouped messages (media albums in Telegram)
    so they are delivered together in a single album rather than separate posts.
    """
    def __init__(self, callback: Callable[[int, List[Message]], Any], wait_time: float = 1.5):
        self.callback = callback
        self.wait_time = wait_time
        self.album_buffers: Dict[int, List[Message]] = {}
        self.album_tasks: Dict[int, asyncio.Task] = {}

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

        if messages:
            # Sort messages in their original order by message id
            messages.sort(key=lambda m: m.id)
            try:
                await self.callback(grouped_id, messages)
            except Exception as e:
                logging.error(f"Error handling album group {grouped_id}: {e}", exc_info=True)
