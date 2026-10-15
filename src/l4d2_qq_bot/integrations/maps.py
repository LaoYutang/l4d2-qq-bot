"""Upstream-compatible GET {map_name_url}/{map_code}, cached for one hour."""

import json
import logging
import re
from urllib.parse import quote

from .cache import AsyncTTLCache
from .http import ExternalAPIError, ExternalHTTP

logger = logging.getLogger(__name__)


class MapNameClient:
    def __init__(self, http: ExternalHTTP, base_url: str = ""):
        self.http = http
        self.base_url = base_url.rstrip("/")
        self._cache: AsyncTTLCache[str] = AsyncTTLCache(3600, 512)

    async def name(self, code: str) -> str:
        if not self.base_url or not re.fullmatch(r"[\w.-]{1,128}", code) or code in {".", ".."}:
            return code
        try:
            return await self._cache.get(code, lambda: self._fetch(code))
        except ExternalAPIError as exc:
            logger.info("地图名称接口不可用，保留地图代码：%s", exc.reason)
            return code

    async def _fetch(self, code: str) -> str:
        raw = await self.http.read("GET", f"{self.base_url}/{quote(code, safe='')}", max_bytes=4096)
        try:
            result = raw.decode("utf-8-sig").strip()
        except UnicodeDecodeError:
            raise ExternalAPIError("地图名称编码错误") from None
        if not result or len(result) > 200 or result.startswith("<"):
            raise ExternalAPIError("地图名称格式错误")
        if result.startswith(("{", "[")):
            try:
                structured = json.loads(result)
            except ValueError:
                structured = None
            if isinstance(structured, (dict, list)):
                raise ExternalAPIError("地图名称接口应返回纯文本")
        return result

    async def close(self) -> None:
        await self._cache.close()
