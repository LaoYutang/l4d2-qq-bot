"""Group isolation, bounded workers and the live query-to-card reply flow."""

import asyncio
import logging
import time
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import quote

from .cards.markdown import markdown_card
from .cards.renderer import CardRenderer
from .cards.views import clean_text, overview_card, server_card
from .commands import Command, CommandKind, match_server, parse_command
from .config import AppConfig, GroupConfig, ServerConfig, Settings
from .domain import Card, CardSection
from .qq.client import QQAPIError, QQClient, QQTransportError
from .qq.discovery import DiscoveryRecorder
from .qq.protocol import GroupMessage
from .qq.replies import ReplySession, connection_keyboard
from .services.query import QueryService, TargetNotAllowed

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RoutedCommand:
    message: GroupMessage
    group: GroupConfig
    command: Command
    server: ServerConfig | None = None


def route_event(payload: dict[str, Any], config: AppConfig) -> RoutedCommand | None:
    message = GroupMessage.from_payload(payload)
    if message is None:
        return None
    group = next(
        (item for item in config.groups if item.group_openid == message.group_openid), None
    )
    if group is None:
        return None
    command = parse_command(message.content)
    if command is None:
        return None
    server = None
    if command.kind == CommandKind.QUERY:
        server = match_server(group.servers, command.argument)
        if server is None:
            return None
    return RoutedCommand(message, group, command, server)


def connect_url(server: ServerConfig, base: str) -> str:
    if server.connect_url:
        return server.connect_url
    return f"{base.rstrip('/')}/{quote(server.address, safe=':[]')}" if base else ""


@dataclass(frozen=True, slots=True)
class QueuedMessage:
    message: GroupMessage
    received_at: float


class BotApplication:
    def __init__(
        self,
        client: QQClient,
        config: AppConfig,
        settings: Settings,
        renderer: CardRenderer,
        discovery: DiscoveryRecorder,
        *,
        demo_data: bool = False,
    ):
        self.client, self.config, self.settings = client, config, settings
        self.renderer, self.discovery = renderer, discovery
        self.demo_data = demo_data
        self.query_service = QueryService(config.query.timeout_seconds, config.query.concurrency)
        self.queue: asyncio.Queue[QueuedMessage] = asyncio.Queue(settings.event_queue_size)
        self._seen: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._workers: list[asyncio.Task] = []
        # Fonts are cached by Pillow. Keep the renderer in one thread at a time.
        self._render_lock = asyncio.Lock()

    def offer_event(self, payload: dict[str, Any]) -> bool:
        message = GroupMessage.from_payload(payload)
        if message is None:
            return True
        if self.discovery.record(message):
            logger.info("发现群消息，OpenID 已保存到本地 discovery 文件")
        if not any(group.group_openid == message.group_openid for group in self.config.groups):
            return True
        now = time.monotonic()
        while self._seen and next(iter(self._seen.values())) <= now:
            self._seen.popitem(last=False)
        key = message.group_openid, message.message_id
        if key in self._seen:
            return True
        try:
            self.queue.put_nowait(QueuedMessage(message, now))
        except asyncio.QueueFull:
            return False
        self._seen[key] = now + 600
        while len(self._seen) > 4096:
            self._seen.popitem(last=False)
        return True

    def start(self) -> None:
        self._workers = [
            asyncio.create_task(self._worker(), name=f"qq-command-{index}")
            for index in range(self.settings.worker_count)
        ]

    async def close(self) -> None:
        for worker in self._workers:
            worker.cancel()
        for worker in self._workers:
            with suppress(asyncio.CancelledError):
                await worker
        self._workers.clear()

    async def _worker(self) -> None:
        while True:
            queued = await self.queue.get()
            reply = ReplySession(self.client, queued.message)
            try:
                remaining = self.settings.command_timeout_seconds - (
                    time.monotonic() - queued.received_at
                )
                if remaining <= 0:
                    logger.warning("丢弃已超出处理时限的群命令")
                    continue
                async with asyncio.timeout(remaining):
                    await self.handle_message(queued.message, reply)
                if reply.sent_count:
                    logger.info("群命令已回复 %s 条消息", reply.sent_count)
            except (QQAPIError, QQTransportError) as exc:
                logger.warning("群回复失败：%s", exc)
            except TimeoutError:
                logger.warning("群查询处理超时")
            except (ValueError, TargetNotAllowed):
                logger.warning("群命令参数或输出预算无效")
            except Exception as exc:
                # Do not log a traceback that could contain request objects or private payloads.
                logger.error("群命令发生未预期错误：%s", type(exc).__name__)
            finally:
                self.queue.task_done()

    async def _card(
        self, reply: ReplySession, card: Card, *, server: ServerConfig | None = None
    ) -> None:
        if self.settings.reply_format == "markdown":
            keyboard = None
            command = f"connect {server.address}" if server else ""
            if server and self.settings.connect_buttons:
                url = connect_url(server, self.config.connect_base_url)
                label = (
                    f"连接{clean_text(server.name, 8)}"
                    if url
                    else f"{clean_text(server.name, 6)}连接指令"
                )
                keyboard = connection_keyboard(label, url, command=command)
            await reply.markdown(markdown_card(card, connection_command=command), keyboard=keyboard)
            return
        async with self._render_lock:
            png = await asyncio.to_thread(self.renderer.render, card)
        try:
            await reply.image(png)
        except QQAPIError:
            if reply.sequence == 0:
                await reply.text("图片上传失败，请检查机器人富媒体接口权限后重试。")
            raise

    async def _query_server(self, server: ServerConfig, *, players: bool = True):
        if self.demo_data:
            from .demo import sample_statuses

            return replace(sample_statuses()[0], alias=server.name, address=server.address)
        return await self.query_service.query(
            server.address, alias=server.name, allow_private=True, include_players=players
        )

    async def handle_message(self, message: GroupMessage, reply: ReplySession) -> None:
        group = next(
            (item for item in self.config.groups if item.group_openid == message.group_openid), None
        )
        if group is None:
            return
        try:
            command = parse_command(message.content)
        except ValueError as exc:
            await reply.text(str(exc))
            return
        if command is None:
            return
        if command.kind == CommandKind.HELP:
            await self._card(
                reply,
                Card(
                    title="L4D2 查询帮助",
                    subtitle="模拟查询数据" if self.demo_data else "QQ 官方机器人 · 服务器查询",
                    sections=(
                        CardSection(
                            "可用指令",
                            (
                                "查询1服 / 查询 合作服",
                                "综合查询",
                                "服务器列表 / 服务器列表 2",
                                "connect IP:端口",
                                "帮助",
                            ),
                        ),
                    ),
                    footer="管理、语音与工坊功能仍在后续开发阶段",
                ),
            )
            return
        if command.kind == CommandKind.OVERVIEW:
            if not group.servers:
                await reply.text("本群尚未配置服务器。")
                return
            statuses = list(
                await asyncio.gather(
                    *(self._query_server(server, players=False) for server in group.servers)
                )
            )
            for offset in range(0, len(statuses), 8):
                card = overview_card(statuses[offset : offset + 8], demo=self.demo_data)
                if len(statuses) > 8:
                    card = replace(
                        card,
                        title=f"服务器概览 · 第 {offset // 8 + 1} 页",
                        footer="人数为本页统计 · L4D2 QQ BOT",
                    )
                await self._card(reply, card)
            return
        if command.kind == CommandKind.ADDRESSES:
            pages = max(1, (len(group.servers) + 5) // 6)
            if command.page > pages:
                await reply.text(f"页码超出范围，当前共 {pages} 页。")
                return
            servers = group.servers[(command.page - 1) * 6 : command.page * 6]
            if not servers:
                await reply.text("本群尚未配置服务器。")
                return
            await self._card(
                reply,
                Card(
                    title=f"服务器列表 · {command.page}/{pages}",
                    sections=tuple(
                        CardSection(server.name, (f"connect {server.address}",))
                        for server in servers
                    ),
                    footer=f"发送“服务器列表 {command.page + 1}”查看下一页"
                    if command.page < pages
                    else "在游戏控制台输入 connect 指令连接",
                ),
            )
            if self.settings.reply_format == "markdown":
                return
            lines = []
            for server in servers:
                lines.extend([f"{server.name}：connect {server.address}"])
                url = connect_url(server, self.config.connect_base_url)
                if url:
                    lines.append(url)
            await reply.text("\n".join(lines))
            return
        server = None
        if command.kind == CommandKind.QUERY:
            server = match_server(group.servers, command.argument)
            if server is None:
                return
            status = await self._query_server(server)
        else:
            server = next(
                (item for item in group.servers if item.address == command.argument), None
            )
            if server is not None:
                status = await self._query_server(server)
            elif self.demo_data:
                from .demo import sample_statuses

                status = replace(
                    sample_statuses()[0], alias=command.argument, address=command.argument
                )
            else:
                try:
                    status = await self.query_service.query(
                        command.argument, allow_private=self.config.query.allow_private_connect
                    )
                except TargetNotAllowed:
                    await reply.text("临时查询仅允许公网地址；私网服务器需由维护者加入本群配置。")
                    return
        target = server or ServerConfig(name=status.alias, address=status.address)
        await self._card(
            reply,
            server_card(status, safe_mode=self.config.safe_mode, demo=self.demo_data),
            server=target,
        )
        if self.settings.reply_format == "markdown":
            return
        url = connect_url(target, self.config.connect_base_url)
        text = f"connect {status.address}" + (f"\n{url}" if url else "")
        await reply.connection(
            f"连接{target.name}"[:20], text, url, buttons=self.settings.connect_buttons
        )
