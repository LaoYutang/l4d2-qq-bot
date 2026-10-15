"""Bounded external HTTP; no QQ headers, redirects, response-body logs or retries."""

import asyncio
import json
from urllib.parse import urlsplit

import aiohttp


class ExternalAPIError(RuntimeError):
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"外部接口不可用（{reason}）")


def public_link(value: object) -> str:
    if not isinstance(value, str) or len(value) > 2048 or any(ord(ch) < 32 for ch in value):
        return ""
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            return ""
        _ = parsed.port
    except ValueError:
        return ""
    return value


class ExternalHTTP:
    def __init__(self, session: aiohttp.ClientSession, *, timeout: float = 5, concurrency: int = 4):
        self.session = session
        self.timeout = timeout
        self._semaphore = asyncio.Semaphore(concurrency)

    async def read(
        self,
        method: str,
        url: str,
        *,
        max_bytes: int = 1024 * 1024,
        timeout_seconds: float | None = None,
        **kwargs,
    ) -> bytes:
        async with self._semaphore:
            try:
                async with self.session.request(
                    method,
                    url,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=timeout_seconds or self.timeout),
                    **kwargs,
                ) as response:
                    if response.status != 200:
                        raise ExternalAPIError(f"HTTP {response.status}")
                    chunks = []
                    size = 0
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > max_bytes:
                            raise ExternalAPIError("响应过大")
                        chunks.append(chunk)
                    return b"".join(chunks)
            except (aiohttp.ClientError, TimeoutError, OSError):
                raise ExternalAPIError("网络失败或超时") from None

    async def json(self, method: str, url: str, **kwargs):
        data = await self.read(method, url, **kwargs)
        try:
            return json.loads(data)
        except (ValueError, UnicodeDecodeError, RecursionError):
            raise ExternalAPIError("响应格式错误") from None
