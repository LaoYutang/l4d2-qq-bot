"""Bounded OpenID discovery; only IDs are persisted to a local ignored file."""

import asyncio
import json
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path

from .protocol import GroupMessage


class DiscoveryRecorder:
    def __init__(self, path: Path):
        self.path = path
        self.groups: OrderedDict[str, dict[str, str]] = OrderedDict()
        self._changed = asyncio.Event()

    def record(self, message: GroupMessage) -> bool:
        if message.group_openid in self.groups:
            return False
        self.groups[message.group_openid] = {
            "group_openid": message.group_openid,
            "member_openid": message.member_openid,
            "seen_at": datetime.now(UTC).isoformat(),
        }
        while len(self.groups) > 128:
            self.groups.popitem(last=False)
        self._changed.set()
        return True

    def _write(self, snapshot: list[dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"groups": snapshot}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.path)

    async def flush(self) -> None:
        if self._changed.is_set():
            self._changed.clear()
            await asyncio.to_thread(self._write, list(self.groups.values()))

    async def run(self) -> None:
        while True:
            await self._changed.wait()
            await self.flush()
