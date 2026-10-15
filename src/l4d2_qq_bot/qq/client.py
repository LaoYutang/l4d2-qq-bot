"""QQ official REST API. Credentials and response bodies are never logged."""

import asyncio
import base64
import json
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import aiohttp


class QQAPIError(RuntimeError):
    def __init__(self, status: int = 0, code: int | str = 0, *, retry_after: float = 0):
        self.status = status
        self.code = code
        self.retry_after = retry_after
        super().__init__(f"QQ API 请求失败：HTTP {status}，业务码 {code}")


class QQTransportError(RuntimeError):
    def __init__(self):
        super().__init__("QQ 网络请求失败或超时")


@dataclass(frozen=True, slots=True)
class UploadedImage:
    file_info: str


class QQClient:
    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        app_id: str,
        client_secret: str,
        api_base: str = "https://api.bot.qq.com",
        token_url: str = "https://api.bot.qq.com/app/getAppAccessToken",
    ):
        self.session = session
        self.api_base = api_base.rstrip("/")
        self.token_url = token_url
        self._app_id = app_id
        self._client_secret = client_secret
        self._token = ""
        self._refresh_at = 0.0
        self._token_lock = asyncio.Lock()

    async def _json(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            async with self.session.request(
                method, url, headers=headers, json=payload, allow_redirects=False
            ) as response:
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > 1024 * 1024:
                        raise QQAPIError(response.status, "response_too_large")
                    chunks.append(chunk)
                try:
                    data = json.loads(b"".join(chunks))
                except (ValueError, UnicodeDecodeError):
                    raise QQAPIError(response.status, "invalid_json") from None
                if not isinstance(data, dict):
                    raise QQAPIError(response.status, "invalid_response")
                code = data.get("code", 0)
                if not 200 <= response.status < 300 or code not in (0, "0", None):
                    # Only a short numeric code leaves this boundary. Never echo server messages.
                    safe_code = code if str(code).isascii() and str(code).isdigit() else "unknown"
                    try:
                        retry_after = float(response.headers.get("Retry-After", "1"))
                    except ValueError:
                        retry_after = 1
                    raise QQAPIError(response.status, safe_code, retry_after=retry_after)
                return data
        except (aiohttp.ClientError, TimeoutError):
            raise QQTransportError() from None

    async def access_token(self) -> str:
        async with self._token_lock:
            if self._token and time.monotonic() < self._refresh_at:
                return self._token
            data = await self._json(
                "POST",
                self.token_url,
                payload={"appId": self._app_id, "clientSecret": self._client_secret},
            )
            token = data.get("access_token")
            try:
                lifetime = int(data["expires_in"])
            except (KeyError, TypeError, ValueError):
                raise QQAPIError(200, "invalid_token_response") from None
            if not isinstance(token, str) or not token or lifetime <= 0:
                raise QQAPIError(200, "invalid_token_response")
            self._token = token
            self._refresh_at = time.monotonic() + max(1, lifetime - min(55, lifetime / 10))
            return token

    async def invalidate_token(self, rejected_token: str) -> None:
        async with self._token_lock:
            if self._token == rejected_token:
                self._refresh_at = 0

    async def request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        for attempt in range(2):
            token = await self.access_token()
            try:
                return await self._json(
                    method,
                    self.api_base + path,
                    headers={"Authorization": f"QQBot {token}"},
                    payload=payload,
                )
            except QQAPIError as exc:
                if attempt == 0 and exc.status == 401:
                    await self.invalidate_token(token)
                    continue
                if attempt == 0 and exc.status == 429 and 0 <= exc.retry_after <= 10:
                    await asyncio.sleep(max(0.25, exc.retry_after))
                    continue
                raise
        raise QQAPIError(401, "authentication_failed")

    async def gateway_url(self) -> str:
        data = await self.request("GET", "/gateway")
        url = data.get("url")
        if not isinstance(url, str):
            raise QQAPIError(200, "invalid_gateway")
        parsed = urlsplit(url)
        # Plain WS is only accepted when both endpoints are a local test server.
        local_test = (
            urlsplit(self.api_base).scheme == "http"
            and urlsplit(self.api_base).hostname in {"127.0.0.1", "localhost", "::1"}
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and parsed.scheme == "ws"
        )
        if (
            (parsed.scheme != "wss" and not local_test)
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise QQAPIError(200, "invalid_gateway")
        return url

    @staticmethod
    def group_path(group_openid: str, resource: str) -> str:
        return f"/v2/groups/{quote(group_openid, safe='')}/{resource}"

    async def upload_png(self, group_openid: str, image: bytes) -> UploadedImage:
        if not image.startswith(b"\x89PNG\r\n\x1a\n") or len(image) > 3 * 1024 * 1024:
            raise ValueError("图片必须是 3 MiB 以下的 PNG")
        data = await self.request(
            "POST",
            self.group_path(group_openid, "files"),
            {
                "file_type": 1,
                "file_name": "l4d2-card.png",
                "file_data": base64.b64encode(image).decode("ascii"),
                "srv_send_msg": False,
            },
        )
        file_info = data.get("file_info")
        if not isinstance(file_info, str) or not file_info:
            raise QQAPIError(200, "missing_file_info")
        return UploadedImage(file_info)

    async def send_group(
        self,
        group_openid: str,
        message_id: str,
        sequence: int,
        content: dict[str, Any],
    ) -> None:
        await self.request(
            "POST",
            self.group_path(group_openid, "messages"),
            {**content, "msg_id": message_id, "msg_seq": sequence},
        )
