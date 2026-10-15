"""Member-scoped, expiring confirmations and upstream QQ flash-transfer workflow."""

import asyncio
import hashlib
import logging
from dataclasses import dataclass, field

from ..cards.views import clean_text
from ..commands import Command, CommandKind, command_text, match_server
from ..config import GroupConfig, ServerConfig
from ..integrations.http import ExternalHTTP
from ..integrations.panel import PanelClient, PanelError, file_size, selection
from ..integrations.rcon import RCONClient
from ..qq.protocol import GroupMessage
from ..qq.replies import ReplySession

logger = logging.getLogger(__name__)


@dataclass
class PendingOperation:
    stage: str
    servers: tuple[ServerConfig, ...] = ()
    server: ServerConfig | None = None
    command: str = ""
    share_url: str = ""
    items: list[dict] = field(default_factory=list)
    timer: asyncio.TimerHandle | None = None


class ManagementService:
    def __init__(self, http: ExternalHTTP, rcon: RCONClient | None = None):
        self.http = http
        self.rcon = rcon or RCONClient()
        self._sessions: dict[tuple[str, str], PendingOperation] = {}
        self._timeouts: set[asyncio.Task] = set()
        self._generation = 0

    def has_session(self, message: GroupMessage) -> bool:
        return (message.group_openid, message.member_openid) in self._sessions

    def cancel_all(self) -> None:
        self._generation += 1
        for pending in self._sessions.values():
            if pending.timer:
                pending.timer.cancel()
        self._sessions.clear()

    async def close(self) -> None:
        self.cancel_all()
        for task in self._timeouts:
            task.cancel()
        await asyncio.gather(*self._timeouts, return_exceptions=True)
        self._timeouts.clear()

    def _pop(self, message: GroupMessage) -> PendingOperation | None:
        pending = self._sessions.pop((message.group_openid, message.member_openid), None)
        if pending and pending.timer:
            pending.timer.cancel()
        return pending

    def _remember(self, message: GroupMessage, reply: ReplySession, pending: PendingOperation):
        self._pop(message)
        if len(self._sessions) >= 64:
            raise ValueError("当前等待操作过多，请稍后重试。")
        key = message.group_openid, message.member_openid
        self._sessions[key] = pending

        async def notify():
            try:
                await reply.text("超时取消")
            except Exception:
                logger.warning("操作超时通知发送失败")

        def expire():
            if self._sessions.get(key) is pending:
                self._sessions.pop(key)
                task = asyncio.create_task(notify())
                self._timeouts.add(task)
                task.add_done_callback(self._timeouts.discard)

        pending.timer = asyncio.get_running_loop().call_later(30, expire)

    @staticmethod
    def _audit(message: GroupMessage, action: str, success: bool) -> None:
        identity = hashlib.sha256(
            f"{message.group_openid}:{message.member_openid}:{message.message_id}".encode()
        ).hexdigest()[:12]
        logger.info("管理操作 %s action=%s success=%s", identity, action, success)

    @staticmethod
    def _rcon_target(servers: list[ServerConfig], argument: str):
        # Only a complete, unique configured name/alias followed by whitespace is accepted.
        candidates = []
        for index, character in enumerate(argument):
            if character.isspace():
                server = match_server(servers, argument[:index])
                if server:
                    candidates.append((index, server, argument[index:].strip()))
        if candidates:
            _, server, command = max(candidates, key=lambda entry: entry[0])
            return server, command
        return match_server(servers, argument), ""

    async def start(
        self, command: Command, group: GroupConfig, message: GroupMessage, reply: ReplySession
    ) -> None:
        self._pop(message)
        if message.member_openid not in group.admin_openids:
            if command.kind != CommandKind.FLASH:
                await reply.text("权限不足：您不在管理员列表中。")
            return
        if command.kind == CommandKind.FLASH:
            servers = tuple(s for s in group.servers if s.panel_url and s.panel_token)
            if not servers:
                await reply.text(
                    "本群尚未配置带面板地址和面板凭据的服务器，请先在插件配置页填写后再试。"
                )
                return
            prompt = (
                "检测到 QQ 闪传链接，请选择要上传的服务器"
                "（回复序号，输入其他内容取消，30 秒内有效）：\n"
                + "\n".join(f"{i}. {clean_text(s.name)}" for i, s in enumerate(servers, 1))
            )
            await reply.text(prompt)
            self._remember(
                message,
                reply,
                PendingOperation("server", servers=servers, share_url=command.argument),
            )
            return
        if command.kind == CommandKind.RESTART:
            if not command.argument:
                await reply.text("请输入服务器名称，例如：重启 主服务器")
                return
            server, text = match_server(group.servers, command.argument), "_restart"
            if server is None:
                return
        else:
            server, text = self._rcon_target(group.servers, command.argument)
            if server is None:
                await reply.text("未找到指定名称的服务器。")
                return
            if not text:
                await reply.text("请输入要执行的指令。")
                return
            if len(text.encode("utf-8")) > 4000 or any(ord(char) < 32 for char in text):
                await reply.text("指令格式错误。")
                return
        if not server.rcon_password:
            action = "重启" if command.kind == CommandKind.RESTART else "指令"
            await reply.text(f"服务器 {server.name} 未配置 RCON 密码，无法执行{action}。")
            return
        prompt = (
            f"确认重启 {clean_text(server.name)}？"
            if text == "_restart"
            else f"确认向 {clean_text(server.name)} 发送指令: {clean_text(text, 500)}？"
        )
        await reply.text(prompt + "（回复“确认”执行，输入其他内容取消，30 秒内有效）")
        self._remember(message, reply, PendingOperation("rcon", server=server, command=text))

    async def respond(
        self,
        group: GroupConfig,
        message: GroupMessage,
        reply: ReplySession,
        *,
        timeout_seconds: float = 90,
    ) -> None:
        pending = self._pop(message)
        if pending is None:
            return
        if message.member_openid not in group.admin_openids:
            await reply.text("权限不足：您不在管理员列表中。")
            return
        generation = self._generation
        try:
            text = command_text(message.content)
            if pending.stage == "rcon":
                if text != "确认":
                    await reply.text("已取消")
                    return
                server = self._current_server(group, pending.server)
                if not server or not server.rcon_password:
                    await reply.text("配置已变更，已取消")
                    return
                if timeout_seconds < 16:
                    await reply.text("超时取消")
                    return
                result = await self.rcon.execute(
                    server.address, server.rcon_password, pending.command
                )
                self._audit(message, "rcon", not result.startswith(("RCON 执行出错", "操作超时")))
                # A misconfigured server may echo the password; never forward it to QQ.
                secret = server.rcon_password
                await reply.text(result.replace(secret, "***") if secret else result)
                return
            if pending.stage == "server":
                index = selection(text, len(pending.servers), multiple=False)[0]
                server = self._current_server(group, pending.servers[index])
                if not server or not server.panel_token:
                    await reply.text("配置已变更，已取消")
                    return
                panel = PanelClient(self.http, server.panel_url, server.panel_token)
                try:
                    items = await panel.parse(pending.share_url)
                except PanelError as exc:
                    await reply.text(f"解析闪传链接失败：{exc}")
                    return
                if generation != self._generation:
                    await reply.text("配置已变更，已取消")
                    return
                if not items:
                    await reply.text("未解析到可下载的文件（仅支持 .vpk/.zip/.rar/.7z）。")
                    return
                lines = [
                    f"{clean_text(server.name)} 解析成功，共 {len(items)} 个可下载文件，"
                    "请选择要下载的文件（回复序号，多个用逗号分隔；回复“全部”下载全部；"
                    "输入其他内容取消，30 秒内有效）："
                ]
                lines.extend(
                    f"{i}. {clean_text(item['filename'], 40)}（{file_size(item.get('file_size'))}）"
                    for i, item in enumerate(items, 1)
                )
                await reply.text("\n".join(lines))
                self._remember(
                    message, reply, PendingOperation("files", server=server, items=items)
                )
                return
            indices = selection(text, len(pending.items))
            server = self._current_server(group, pending.server)
            if not server or not server.panel_token:
                await reply.text("配置已变更，已取消")
                return
            panel = PanelClient(self.http, server.panel_url, server.panel_token)
            results = []
            deadline = asyncio.get_running_loop().time() + timeout_seconds
            for index in indices:
                remaining = deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    results.append((pending.items[index]["filename"], False, "处理超时，未提交"))
                    continue
                if generation != self._generation:
                    results.append((pending.items[index]["filename"], False, "配置已变更，已取消"))
                    continue
                item = pending.items[index]
                try:
                    async with asyncio.timeout(remaining):
                        await panel.add(item)
                    results.append((item["filename"], True, ""))
                except TimeoutError:
                    results.append((item["filename"], False, "提交状态未确认，请检查面板任务列表"))
                except PanelError as exc:
                    results.append((item["filename"], False, str(exc)))
            self._audit(message, "download", all(ok for _, ok, _ in results))
            await reply.text(format_transfer_result(results))
        except ValueError as exc:
            await reply.text(str(exc))

    @staticmethod
    def _current_server(group: GroupConfig, server: ServerConfig | None) -> ServerConfig | None:
        return next(
            (
                s
                for s in group.servers
                if server and s.name == server.name and s.address == server.address
            ),
            None,
        )


def format_transfer_result(results: list[tuple[str, bool, str]]) -> str:
    added = [name for name, ok, _ in results if ok]
    failed = [(name, error) for name, ok, error in results if not ok]
    lines = []
    if added:
        lines.append(f"已提交 {len(added)} 个下载任务：")
        lines.extend(f"- {clean_text(name, 40)}" for name in added)
    if failed:
        if lines:
            lines.append("")
        lines.append(f"提交失败 {len(failed)} 个：")
        lines.extend(f"- {clean_text(name, 40)}：{error}" for name, error in failed)
    return "\n".join(lines) or "没有提交任何下载任务。"
