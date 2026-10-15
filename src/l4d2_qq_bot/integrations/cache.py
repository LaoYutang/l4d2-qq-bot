"""Small TTL cache with shared in-flight reads and explicit shutdown."""

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable


class AsyncTTLCache[T]:
    def __init__(self, ttl: float, max_entries: int = 256):
        self.ttl, self.max_entries = ttl, max_entries
        self._entries: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._pending: dict[str, asyncio.Task[T]] = {}

    async def get(self, key: str, load: Callable[[], Awaitable[T]]) -> T:
        cached = self._entries.get(key)
        if cached and cached[0] > time.monotonic():
            self._entries.move_to_end(key)
            return cached[1]
        self._entries.pop(key, None)
        pending = self._pending.get(key)
        if pending is None:
            if len(self._pending) >= self.max_entries:
                # Do not create an unbounded set of background requests.
                return await load()
            pending = asyncio.create_task(self._load(key, load))
            pending.add_done_callback(self._observe)
            self._pending[key] = pending
        return await asyncio.shield(pending)

    @staticmethod
    def _observe(task: asyncio.Task) -> None:
        if not task.cancelled():
            task.exception()

    async def _load(self, key: str, load: Callable[[], Awaitable[T]]) -> T:
        try:
            result = await load()
            self._entries[key] = time.monotonic() + self.ttl, result
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)
            return result
        finally:
            self._pending.pop(key, None)

    async def close(self) -> None:
        pending = list(self._pending.values())
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        self._pending.clear()
        self._entries.clear()
