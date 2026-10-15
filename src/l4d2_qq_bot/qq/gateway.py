"""One WS reader plus an independent heartbeat task and resumable reconnects."""

import asyncio
import json
import logging
import random
import time
from collections.abc import Callable
from contextlib import suppress
from typing import Any

import aiohttp

from .client import QQAPIError, QQClient, QQTransportError
from .protocol import GatewayState, HeartbeatTimeout, OpCode

logger = logging.getLogger(__name__)


class GatewayFatalError(RuntimeError):
    pass


class GatewayReconnect(ConnectionError):
    pass


class QQGateway:
    def __init__(
        self,
        client: QQClient,
        on_event: Callable[[dict[str, Any]], bool],
        *,
        auth_deadline_seconds: float = 30,
        max_backoff_seconds: float = 60,
    ):
        self.client = client
        self.on_event = on_event
        self.state = GatewayState()
        self.ready = asyncio.Event()
        self.auth_deadline_seconds = auth_deadline_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self._closed = False
        self._socket: aiohttp.ClientWebSocketResponse | None = None
        self._send_lock = asyncio.Lock()
        self._ready_this_connection = False

    async def close(self) -> None:
        self._closed = True
        if self._socket is not None:
            await self._socket.close()

    async def _send(self, ws: aiohttp.ClientWebSocketResponse, payload: dict[str, Any]) -> None:
        async with self._send_lock:
            await ws.send_json(payload)

    async def _heartbeat(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        try:
            while not ws.closed:
                await asyncio.sleep(self.state.heartbeat_interval)
                await self._send(ws, self.state.heartbeat())
        except (HeartbeatTimeout, aiohttp.ClientError, ConnectionError):
            logger.warning("QQ WS 心跳未确认或发送失败，准备重连")
            await ws.close()

    @staticmethod
    def _payload(message: aiohttp.WSMessage) -> dict[str, Any]:
        if message.type != aiohttp.WSMsgType.TEXT:
            raise GatewayReconnect("QQ WS 已关闭或收到非 JSON 消息")
        try:
            payload = json.loads(message.data)
        except (TypeError, ValueError):
            raise GatewayReconnect("QQ WS 收到无效 JSON") from None
        if not isinstance(payload, dict) or type(payload.get("op")) is not int:
            raise GatewayReconnect("QQ WS 收到无效载荷")
        return payload

    async def _connection(self, url: str) -> None:
        self.ready.clear()
        self._ready_this_connection = False
        async with self.client.session.ws_connect(
            url,
            timeout=aiohttp.ClientWSTimeout(ws_close=5),
            autoping=True,
            max_msg_size=1024 * 1024,
        ) as ws:
            self._socket = ws
            first = self._payload(await ws.receive(timeout=self.auth_deadline_seconds))
            if self.state.accept(first) != "hello":
                raise GatewayReconnect("QQ WS 未返回 Hello")
            token = await self.client.access_token()
            await self._send(ws, self.state.authentication(token))
            heartbeat = asyncio.create_task(self._heartbeat(ws), name="qq-heartbeat")
            deadline = time.monotonic() + self.auth_deadline_seconds
            try:
                while not ws.closed and not self._closed:
                    remaining = (
                        None
                        if self._ready_this_connection
                        else max(0.01, deadline - time.monotonic())
                    )
                    message = await ws.receive(timeout=remaining)
                    if message.type in {
                        aiohttp.WSMsgType.CLOSED,
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.ERROR,
                    }:
                        break
                    payload = self._payload(message)
                    previous_sequence = self.state.sequence
                    action = self.state.accept(payload)
                    if action == "ready" or payload.get("t") == "RESUMED":
                        self._ready_this_connection = True
                        self.ready.set()
                        logger.info("QQ WS 已登录，等待群 @ 消息")
                    elif action == "reconnect":
                        raise GatewayReconnect("QQ WS 要求重连")
                    elif action == "identify":
                        # Reset + reconnect; do not hammer Identify on the same socket.
                        raise GatewayReconnect("QQ WS 会话已失效")
                    elif action == "heartbeat_requested":
                        # A server-requested heartbeat is not an ACK for the periodic heartbeat.
                        await self._send(ws, {"op": OpCode.HEARTBEAT, "d": self.state.sequence})
                    elif action == "dispatch" and not self.on_event(payload):
                        self.state.sequence = previous_sequence
                        raise GatewayReconnect("业务队列已满，稍后恢复未接收事件")
                if ws.close_code in {4004, 4013, 4014}:
                    raise GatewayFatalError(
                        f"QQ WS 拒绝鉴权或事件权限（关闭码 {ws.close_code}），请检查应用配置"
                    )
            finally:
                heartbeat.cancel()
                with suppress(asyncio.CancelledError):
                    await heartbeat
                self._socket = None

    async def run(self) -> None:
        delay = 1.0
        while not self._closed:
            try:
                url = await self.client.gateway_url()
                await self._connection(url)
            except QQAPIError as exc:
                if exc.status in {401, 403} or str(exc.code) in {"100007", "100016", "10004"}:
                    raise GatewayFatalError(str(exc)) from None
                logger.warning("QQ 网关请求失败（HTTP %s，业务码 %s）", exc.status, exc.code)
            except (
                aiohttp.ClientError,
                QQTransportError,
                ConnectionError,
                TimeoutError,
                ValueError,
            ):
                logger.warning("QQ WS 连接中断或未就绪，准备重连")
            if self._closed:
                break
            if self._ready_this_connection:
                delay = 1.0
            await asyncio.sleep(min(delay + random.uniform(0, delay / 4), self.max_backoff_seconds))
            delay = min(delay * 2, self.max_backoff_seconds)
