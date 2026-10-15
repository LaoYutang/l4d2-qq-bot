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


@dataclass(frozen=True, slots=True)
class Command:
    kind: CommandKind
    argument: str = ""
    page: int = 1


def parse_command(content: str) -> Command | None:
    # QQ's current event already strips the bot mention; older payloads may retain it.
    content = re.sub(r"^\s*<@!?[A-Za-z0-9_-]+>\s*", "", content).strip()
    content = content.removeprefix("/").strip()
    if content in {"帮助", "help"}:
        return Command(CommandKind.HELP)
    if content == "综合查询":
        return Command(CommandKind.OVERVIEW)
    if match := re.fullmatch(r"(?:服务器列表|服务器地址|连接指令)(?:\s+([0-9]{1,3}))?", content):
        page = int(match[1] or "1")
        if page < 1:
            raise ValueError("页码从 1 开始")
        return Command(CommandKind.ADDRESSES, page=page)
    if content.startswith("查询"):
        argument = content.removeprefix("查询").strip()
        if not argument:
            raise ValueError("请输入服务器名称，例如：查询 合作服")
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
