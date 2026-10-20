"""Group isolation, bounded workers and the live query-to-card reply flow."""

import asyncio
import logging
import time
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import quote

from .cards.markdown import markdown_card, markdown_text_pages
from .cards.renderer import CardRenderer
from .cards.views import clean_text, overview_card, server_card, voice_card
from .commands import Command, CommandKind, match_server, parse_command, server_query_command
from .config import AppConfig, GroupConfig, ServerConfig, Settings
from .domain import Card, CardLink, CardSection, VoiceStatus
from .integrations.heybox import HeyboxVoiceClient
from .integrations.maps import MapNameClient
from .qq.client import QQAPIError, QQClient, QQTransportError
from .qq.discovery import DiscoveryRecorder
from .qq.protocol import GroupMessage
from .qq.replies import (
    BUTTONS_PER_ROW,
    MAX_LINK_BUTTONS,
    ReplySession,
    connection_keyboard,
    link_keyboard,
    query_keyboard,
    split_text,
)
from .services.management import ManagementService
from .services.queries import QueryCoordinator
from .services.query import QueryService, TargetNotAllowed

logger = logging.getLogger(__name__)
QUERY_COOLDOWN_SECONDS = 2.0
QUERY_RECENT_LIMIT = 4096
OVERVIEW_QUERY_BUTTONS = 4 * BUTTONS_PER_ROW
QueryKey = tuple[str, str, str, str]


@dataclass(frozen=True, slots=True)
class RoutedCommand:
    message: GroupMessage
    group: GroupConfig | None
    command: Command
    server: ServerConfig | None = None


def route_event(payload: dict[str, Any], config: AppConfig) -> RoutedCommand | None:
    message = GroupMessage.from_payload(payload)
    if message is None:
        return None
    group = next(
        (item for item in config.groups if item.group_openid == message.group_openid), None
    )
    try:
        command = parse_command(message.content)
    except ValueError:
        if group is None:
            return None
        raise
    if command is None:
        return None
    if command.kind == CommandKind.GROUP_ID:
        return RoutedCommand(message, group, command)
    if group is None:
        return None
    server = None
    if command.kind == CommandKind.QUERY:
        server = match_server(group.servers, command.argument)
        if server is None:
            return None
    return RoutedCommand(message, group, command, server)


def connect_url(server: ServerConfig, base: str) -> str:
    return f"{base.rstrip('/')}/{quote(server.address, safe=':[]')}" if base else ""


@dataclass(frozen=True, slots=True)
class QueuedMessage:
    message: GroupMessage
    received_at: float


@dataclass
class ApplicationResources:
    config: AppConfig
    query: QueryService
    queries: QueryCoordinator
    maps: MapNameClient | None
    voice: HeyboxVoiceClient | None
    users: int = 0
    retired: bool = False

    async def close(self) -> None:
        for client in (self.maps, self.voice):
            if client is not None:
                await client.close()


class BotApplication:
    def __init__(
        self,
        client: QQClient,
        config: AppConfig,
        settings: Settings,
        renderer: CardRenderer | None,
        discovery: DiscoveryRecorder,
        *,
        query_service: QueryService | None = None,
        maps: MapNameClient | None = None,
        voice: HeyboxVoiceClient | None = None,
        management: ManagementService | None = None,
    ):
        self.client, self.config, self.settings = client, config, settings
        self.renderer, self.discovery = renderer, discovery
        self.query_service = query_service or QueryService(
            config.query.timeout_seconds, config.query.concurrency
        )
        self.maps, self.voice = maps, voice
        self.queries = QueryCoordinator(self.query_service, maps, voice)
        self._resources = ApplicationResources(
            config, self.query_service, self.queries, maps, voice
        )
        self.management = management
        self._cleanup: set[asyncio.Task] = set()
        self.queue: asyncio.Queue[QueuedMessage] = asyncio.Queue(settings.event_queue_size)
        self._seen: OrderedDict[tuple[str, str], float] = OrderedDict()
        self._query_active: set[QueryKey] = set()
        self._query_recent: OrderedDict[QueryKey, float] = OrderedDict()
        self._workers: list[asyncio.Task] = []
        # Fonts are cached by Pillow. Keep the renderer in one thread at a time.
        self._render_lock = asyncio.Lock()

    def offer_event(self, payload: dict[str, Any]) -> bool:
        message = GroupMessage.from_payload(payload)
        if message is None:
            return True
        if self.discovery.record(message):
            logger.info("发现群消息，OpenID 已保存到本地 discovery 文件")
        group = next(
            (item for item in self.config.groups if item.group_openid == message.group_openid), None
        )
        pending = bool(group and self.management and self.management.has_session(message))
        try:
            command = parse_command(message.content)
            if group is None and (command is None or command.kind != CommandKind.GROUP_ID):
                return True
            if command is None and not (self.management and self.management.has_session(message)):
                return True
            if (
                command is not None
                and group is not None
                and not pending
                and command.kind in {CommandKind.QUERY, CommandKind.VOICE}
                and match_server(group.servers, command.argument) is None
            ):
                return True
        except ValueError:
            if group is None:
                return True
            # Recognized commands with invalid arguments get usage help from a worker.
            pass
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
        if self.management:
            await self.management.close()
        await self._resources.close()
        await asyncio.gather(*self._cleanup, return_exceptions=True)

    def reconfigure(
        self,
        config: AppConfig,
        *,
        maps: MapNameClient,
        voice: HeyboxVoiceClient,
    ) -> None:
        previous = self._resources
        previous.retired = True
        self.config, self.maps, self.voice = config, maps, voice
        self.query_service = QueryService(config.query.timeout_seconds, config.query.concurrency)
        self.queries = QueryCoordinator(self.query_service, maps, voice)
        self._resources = ApplicationResources(
            config, self.query_service, self.queries, maps, voice
        )
        if self.management:
            self.management.cancel_all()
            self.management.http = maps.http
        if previous.users == 0:
            task = asyncio.create_task(previous.close())
            self._cleanup.add(task)
            task.add_done_callback(self._cleanup.discard)

    def _query_key(self, message: GroupMessage) -> QueryKey | None:
        group = next(
            (item for item in self.config.groups if item.group_openid == message.group_openid), None
        )
        if group is None or (self.management and self.management.has_session(message)):
            return None
        try:
            command = parse_command(message.content)
        except ValueError:
            return None
        if command is None:
            return None
        if command.kind == CommandKind.OVERVIEW:
            target = ""
        elif command.kind == CommandKind.QUERY:
            server = match_server(group.servers, command.argument)
            if server is None:
                return None
            target = server.address
        elif command.kind == CommandKind.CONNECT:
            target = command.argument
        else:
            return None
        return message.group_openid, message.member_openid, command.kind.value, target

    async def _worker(self) -> None:
        while True:
            queued = await self.queue.get()
            reply = ReplySession(self.client, queued.message)
            query_key = None
            try:
                remaining = self.settings.command_timeout_seconds - (
                    time.monotonic() - queued.received_at
                )
                if remaining <= 0:
                    logger.warning("丢弃已超出处理时限的群命令")
                    continue
                key = self._query_key(queued.message)
                if key is not None:
                    now = time.monotonic()
                    while self._query_recent and next(iter(self._query_recent.values())) <= now:
                        self._query_recent.popitem(last=False)
                    if key in self._query_active or key in self._query_recent:
                        continue
                    self._query_active.add(key)
                    query_key = key
                async with asyncio.timeout(remaining):
                    await self.handle_message(
                        queued.message,
                        reply,
                        deadline=queued.received_at + self.settings.command_timeout_seconds,
                    )
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
                if query_key is not None:
                    self._query_active.discard(query_key)
                    self._query_recent.pop(query_key, None)
                    self._query_recent[query_key] = time.monotonic() + QUERY_COOLDOWN_SECONDS
                    while len(self._query_recent) > QUERY_RECENT_LIMIT:
                        self._query_recent.popitem(last=False)
                self.queue.task_done()

    async def _card(
        self,
        reply: ReplySession,
        card: Card,
        *,
        server: ServerConfig | None = None,
        config: AppConfig | None = None,
        fallback_text: str = "",
        plain_text: bool = False,
        query_actions: tuple[tuple[str, str], ...] = (),
        query_last_row: bool = False,
    ) -> None:
        if plain_text:
            content = card.content or "\n".join(
                value for value in (card.title, card.subtitle) if value
            )
            if card.links:
                content += "\n" + "\n".join(f"{link.label}: {link.url}" for link in card.links)
            await reply.text(content)
            return
        if self.settings.reply_format == "markdown":
            keyboard = (
                link_keyboard(card.links) if card.links and self.settings.connect_buttons else None
            )
            command = (
                f"connect {server.address}"
                if server and self.settings.connect_buttons
                else fallback_text
            )
            if server and self.settings.connect_buttons:
                url = connect_url(server, (config or self.config).connect_base_url)
                label = "🎮 进入服务器" if url else "🎮 连接指令"
                game_keyboard = connection_keyboard(label, url, command=command)
                buttons = game_keyboard["content"]["rows"][0]["buttons"]
                if keyboard:
                    buttons.extend(
                        button for row in keyboard["content"]["rows"] for button in row["buttons"]
                    )
                if len(buttons) > MAX_LINK_BUTTONS:
                    raise ValueError(f"单条卡片最多支持 {MAX_LINK_BUTTONS} 个按钮")
                keyboard = {
                    "content": {
                        "rows": [
                            {"buttons": buttons[index : index + BUTTONS_PER_ROW]}
                            for index in range(0, len(buttons), BUTTONS_PER_ROW)
                        ]
                    }
                }
            if server and card.links:
                command = "\n".join(
                    filter(None, [command, *(f"{link.label}: {link.url}" for link in card.links)])
                )
            if query_actions and self.settings.connect_buttons:
                controls = query_keyboard(query_actions, separate_last=query_last_row)
                rows = keyboard["content"]["rows"] if keyboard else []
                rows = [*rows, *controls["content"]["rows"]]
                if len(rows) > 5 or sum(len(row["buttons"]) for row in rows) > MAX_LINK_BUTTONS:
                    raise ValueError("查询卡片按钮超过单条卡片上限")
                keyboard = {"content": {"rows": rows}}
                command = "\n".join(filter(None, [command, *(text for _, text in query_actions)]))
            pages = (
                markdown_text_pages(card.content)
                if card.content
                else [
                    (page, None)
                    for page in split_text(markdown_card(card, max_bytes=100000), max_bytes=3200)
                ]
            )
            if len(pages) > 5 - reply.sequence:
                await reply.text("查询结果过长，请使用页码缩小范围。")
                return
            for index, (page, plain) in enumerate(pages):
                await reply.markdown(
                    page,
                    keyboard=keyboard if index == len(pages) - 1 else None,
                    fallback_text=command if index == len(pages) - 1 else "",
                    plain_content=plain,
                )
            return
        if self.renderer is None:
            raise ValueError("图片模式需要配置图片渲染器")
        async with self._render_lock:
            png = await asyncio.to_thread(self.renderer.render, card)
        try:
            await reply.image(png)
        except QQAPIError:
            if reply.sequence == 0:
                await reply.text("图片上传失败，请检查机器人富媒体接口权限后重试。")
            raise
        if card.links:
            downloads = [line for line in card.content.splitlines() if line.startswith("下载: ")]
            if downloads:
                await reply.text("\n".join(downloads))

    async def _query_server(
        self,
        server: ServerConfig,
        group: GroupConfig,
        *,
        players: bool = True,
        resources: ApplicationResources | None = None,
    ):
        return await (resources or self._resources).queries.server(server, group, players=players)

    async def handle_message(
        self, message: GroupMessage, reply: ReplySession, *, deadline: float | None = None
    ) -> None:
        resources = self._resources
        resources.users += 1
        try:
            await self._handle_message(
                message,
                reply,
                resources,
                deadline or time.monotonic() + self.settings.command_timeout_seconds,
            )
        finally:
            resources.users -= 1
            if resources.retired and resources.users == 0:
                await resources.close()

    async def _handle_message(
        self,
        message: GroupMessage,
        reply: ReplySession,
        resources: ApplicationResources,
        deadline: float,
    ) -> None:
        config = resources.config
        group = next(
            (item for item in config.groups if item.group_openid == message.group_openid), None
        )
        pending = bool(group and self.management and self.management.has_session(message))
        management_kinds = {CommandKind.RCON, CommandKind.RESTART, CommandKind.FLASH}

        async def respond():
            await self.management.respond(
                group,
                message,
                reply,
                timeout_seconds=max(0, min(90, deadline - time.monotonic() - 5)),
            )

        try:
            command = parse_command(message.content)
        except ValueError as exc:
            if pending:
                await respond()
            elif group is not None:
                await reply.text(str(exc))
            return
        if pending and (command is None or command.kind not in management_kinds):
            await respond()
            return
        if command is not None and command.kind == CommandKind.GROUP_ID:
            await reply.text(message.group_openid)
            return
        if group is None:
            return
        if command is None:
            return
        if command.kind == CommandKind.USER_ID:
            await reply.text(message.member_openid)
            return
        if command.kind in management_kinds:
            if self.management:
                await self.management.start(
                    command,
                    group,
                    message,
                    reply,
                    timeout_seconds=max(0, min(90, deadline - time.monotonic() - 5)),
                )
            return
        if command.kind == CommandKind.HELP:
            await self._card(
                reply,
                Card(
                    title="L4D2 查询帮助",
                    subtitle="",
                    sections=(
                        CardSection(
                            "可用指令",
                            (
                                "查询1服 / 查询 合作服",
                                "综合查询",
                                "服务器列表 / 服务器列表 2",
                                "connect IP:端口",
                                "语音1服 / 查询1服语音 / 1服语音",
                                "帮助",
                            ),
                        ),
                    ),
                    footer="",
                    content="\n".join(
                        (
                            "查询 服务器名",
                            "综合查询",
                            "服务器列表",
                            "connect IP:端口",
                            "语音 服务器名 / 查询服务器名语音 / 服务器名语音",
                            "设置 服务器名 指令",
                            "重启 服务器名",
                            "QQ 闪传链接",
                            "获取群ID",
                            "获取用户ID",
                        )
                    ),
                ),
            )
            return
        if command.kind == CommandKind.VOICE:
            server = match_server(group.servers, command.argument)
            if server is None:
                return
            status = (
                await resources.voice.query(server.name, group.hh_room_id, server.hh_channel_id)
                if resources.voice is not None
                else VoiceStatus(server.name, False, error="未配置黑盒语音客户端。")
            )
            await self._card(
                reply,
                voice_card(status),
                plain_text=status.count in (None, 0),
            )
            return
        if command.kind == CommandKind.OVERVIEW:
            if not group.servers:
                await reply.text("本群未配置任何服务器。")
                return
            statuses = list(
                await asyncio.gather(
                    *(
                        self._query_server(server, group, players=False, resources=resources)
                        for server in group.servers
                    )
                )
            )
            actions = []
            eligible = [
                server
                for server, status in zip(group.servers, statuses, strict=True)
                if status.online and status.player_count > 0
            ]
            for server in eligible[:OVERVIEW_QUERY_BUTTONS]:
                text = server_query_command(server, group.servers)
                selector = text.removeprefix("查询 ") if text.startswith("查询 ") else server.name
                label = f"查询{selector}"
                if len(label) > 10:
                    label = label[:9] + "…"
                actions.append((label, text))
            card = overview_card(statuses)
            if (
                len(eligible) > OVERVIEW_QUERY_BUTTONS
                and self.settings.reply_format == "markdown"
                and self.settings.connect_buttons
            ):
                card = replace(
                    card,
                    content=card.content + "\n\n快捷查询按钮仅显示前 8 台有人服务器，"
                    "其余服务器请手动发送“查询 服务器名”。",
                )
            await self._card(
                reply,
                card,
                query_actions=(*actions, ("再次查询", "综合查询")),
                query_last_row=True,
            )
            return
        if command.kind == CommandKind.ADDRESSES:
            pages = max(1, (len(group.servers) + 5) // 6)
            if command.page > pages:
                await reply.text(f"页码超出范围，当前共 {pages} 页。")
                return
            servers = (
                group.servers[(command.page - 1) * 6 : command.page * 6]
                if command.page
                else group.servers
            )
            if not servers:
                await reply.text("本群未配置任何服务器。")
                return
            use_buttons = self.settings.reply_format == "markdown" and self.settings.connect_buttons
            for offset in range(0, len(servers), MAX_LINK_BUTTONS):
                batch = servers[offset : offset + MAX_LINK_BUTTONS]
                lines = ["服务器列表"]
                links = []
                fallback = []
                for server in batch:
                    url = connect_url(server, config.connect_base_url)
                    command_line = f"[{server.name}] connect {server.address}"
                    if use_buttons and url:
                        lines.append(f"[{server.name}] {server.address}")
                        links.append(CardLink(clean_text(server.name, 10) or "服务器", url))
                        fallback.append(command_line)
                    else:
                        lines.append(command_line)
                await self._card(
                    reply,
                    Card(
                        title="服务器列表",
                        sections=tuple(
                            CardSection(server.name, (f"connect {server.address}",))
                            for server in batch
                        ),
                        footer="",
                        links=tuple(links),
                        content="\n".join(lines),
                    ),
                    fallback_text="\n".join(fallback),
                )
            if self.settings.reply_format == "markdown":
                return
            lines = []
            for server in servers:
                lines.extend([f"{server.name}：connect {server.address}"])
                url = connect_url(server, config.connect_base_url)
                if url:
                    lines.append(url)
            await reply.text("\n".join(lines))
            return
        server = None
        if command.kind == CommandKind.QUERY:
            server = match_server(group.servers, command.argument)
            if server is None:
                return
            status = await self._query_server(server, group, resources=resources)
        else:
            server = next(
                (item for item in group.servers if item.address == command.argument), None
            )
            if server is not None:
                status = await resources.queries.address(server.address, allow_private=True)
            else:
                try:
                    status = await resources.queries.address(
                        command.argument, allow_private=config.query.allow_private_connect
                    )
                except TargetNotAllowed:
                    await reply.text("临时查询仅允许公网地址；私网服务器需由维护者加入本群配置。")
                    return
        target = server or ServerConfig(name=status.alias, address=status.address)
        card = server_card(
            status,
            safe_mode=config.safe_mode,
            temporary=command.kind == CommandKind.CONNECT,
            connection_url=(
                None
                if self.settings.reply_format == "markdown" and self.settings.connect_buttons
                else connect_url(target, config.connect_base_url)
            ),
            voice_connection_url=target.voice_connect_url,
        )
        if not status.online and self.settings.reply_format == "markdown":
            card = replace(card, content=f"{card.title}\n\n{card.content}")
        retry_command = (
            server_query_command(target, group.servers)
            if command.kind == CommandKind.QUERY
            else f"connect {target.address}"
        )
        await self._card(
            reply,
            card,
            server=target,
            config=config,
            plain_text=not status.online and self.settings.reply_format != "markdown",
            query_actions=(("🌐 综合查询", "综合查询"), ("🔍 再次查询", retry_command)),
        )
        if self.settings.reply_format == "markdown":
            return
        if not status.online:
            return
        url = connect_url(target, config.connect_base_url)
        text = f"点击直连: {url}" if url else f"连接指令: connect {status.address}"
        await reply.connection("🎮 进入服务器", text, url, buttons=self.settings.connect_buttons)
