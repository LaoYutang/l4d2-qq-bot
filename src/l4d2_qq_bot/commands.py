"""Pure command parsing, without framework event decorators."""

import re
from dataclasses import dataclass
from enum import StrEnum

from .address import normalize_name, parse_address
from .config import ServerConfig


class CommandKind(StrEnum):
    HELP = "help"
    QUERY = "query"
    OVERVIEW = "overview"
    ADDRESSES = "addresses"
    CONNECT = "connect"
    VOICE = "voice"
    GROUP_ID = "group_id"
    USER_ID = "user_id"
    RCON = "rcon"
    RESTART = "restart"
    FLASH = "flash"


@dataclass(frozen=True, slots=True)
class Command:
    kind: CommandKind
    argument: str = ""
    page: int = 1


def command_text(content: str) -> str:
    # QQ's current event already strips the bot mention; older payloads may retain it.
    content = re.sub(r"^\s*<@!?[A-Za-z0-9_-]+>\s*", "", content).strip()
    return content.removeprefix("/").strip()


def parse_command(content: str) -> Command | None:
    content = command_text(content)
    if content.casefold() in {"获取群id", "群id"}:
        return Command(CommandKind.GROUP_ID)
    if content.casefold() in {"获取用户id", "获取我的id", "我的id"}:
        return Command(CommandKind.USER_ID)
    if content in {"帮助", "help"}:
        return Command(CommandKind.HELP)
    if content == "综合查询":
        return Command(CommandKind.OVERVIEW)
    for prefix, kind in (("设置", CommandKind.RCON), ("重启", CommandKind.RESTART)):
        if content.startswith(prefix):
            return Command(kind, content.removeprefix(prefix).strip())
    if match := re.search(r"https?://qfile\.qq\.com/q/[A-Za-z0-9_-]+(?=$|[\s。，；,;?#])", content):
        return Command(CommandKind.FLASH, match[0])
    voice_argument = None
    if content.startswith("语音"):
        voice_argument = content.removeprefix("语音").strip()
    elif content.endswith("语音"):
        voice_argument = content.removeprefix("查询").removesuffix("语音").strip()
    if voice_argument is not None:
        if not voice_argument:
            raise ValueError("请输入服务器快捷名，例如：语音1服")
        return Command(CommandKind.VOICE, voice_argument)
    if match := re.fullmatch(r"(?:服务器列表|服务器地址|连接指令)(?:\s+([0-9]{1,3}))?", content):
        page = int(match[1]) if match[1] else 0
        if match[1] and page < 1:
            raise ValueError("页码从 1 开始")
        return Command(CommandKind.ADDRESSES, page=page)
    if content.startswith("查询"):
        argument = content.removeprefix("查询").strip()
        if not argument:
            raise ValueError("请输入服务器名称，例如：查询 主服务器")
        return Command(CommandKind.QUERY, argument)
    if match := re.fullmatch(r"connect\s+(.+)", content, flags=re.IGNORECASE):
        return Command(CommandKind.CONNECT, parse_address(match[1]).address)
    return None


def match_server(servers: list[ServerConfig], name: str) -> ServerConfig | None:
    target = normalize_name(name)
    matches = [
        server
        for server in servers
        if target in {normalize_name(item) for item in [server.name, *server.aliases]}
    ]
    return matches[0] if len(matches) == 1 else None


def server_query_command(server: ServerConfig, servers: list[ServerConfig]) -> str:
    """Round-trip a configured selector instead of guessing from the A2S title."""
    for selector in (server.name, *server.aliases):
        if any(ord(char) < 32 for char in selector):
            continue
        text = f"查询 {selector}"
        try:
            command = parse_command(text)
        except ValueError:
            continue
        if (
            command is not None
            and command.kind == CommandKind.QUERY
            and match_server(servers, command.argument) is server
        ):
            return text
    # Names ending in “语音” may all select the voice command. CONNECT is an
    # existing query command and keeps an otherwise unaddressable target exact.
    return f"connect {server.address}"
