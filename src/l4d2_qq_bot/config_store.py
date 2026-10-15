"""Revision checked, atomic configuration persistence (upstream config_manager protocol)."""

import asyncio
import hashlib
import json
import os
import tempfile
from collections.abc import Callable
from pathlib import Path

from .config import AppConfig


class ConfigConflictError(ValueError):
    pass


class ConfigStore:
    def __init__(self, path: Path, on_change: Callable[[AppConfig], None] | None = None):
        self.path = path
        self.on_change = on_change
        self._lock = asyncio.Lock()
        self._applied_revision: str | None = None

    def _read(self) -> tuple[AppConfig, str]:
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(1024 * 1024 + 1)
        except FileNotFoundError:
            return AppConfig(), "missing"
        if len(raw) > 1024 * 1024:
            raise ValueError("配置文件超过 1 MiB")
        config = AppConfig.model_validate(json.loads(raw.decode("utf-8-sig")))
        return config, hashlib.sha256(raw).hexdigest()

    def _write(self, raw: bytes) -> str:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".config-", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return hashlib.sha256(raw).hexdigest()

    def _apply(self, config: AppConfig, revision: str) -> None:
        if revision != self._applied_revision:
            if self.on_change:
                self.on_change(config)
            self._applied_revision = revision

    async def read(self) -> tuple[AppConfig, str]:
        async with self._lock:
            config, revision = await asyncio.to_thread(self._read)
            self._apply(config, revision)
            return config, revision

    async def save(self, data: object, expected_revision: str) -> tuple[AppConfig, str]:
        config = AppConfig.model_validate(data)
        raw = (config.model_dump_json(indent=2) + "\n").encode("utf-8")
        if len(raw) > 1024 * 1024:
            raise ValueError("配置文件超过 1 MiB")
        async with self._lock:
            _, revision = await asyncio.to_thread(self._read)
            if expected_revision != revision:
                raise ConfigConflictError("config.json 已被其他操作修改，请重新载入后再保存")
            revision = await asyncio.to_thread(self._write, raw)
            self._apply(config, revision)
            return config, revision
