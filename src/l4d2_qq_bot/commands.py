"""Pure command parsing, without framework event decorators."""

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import parse_qs, urlsplit

from .address import normalize_name, parse_address
from .config import ServerConfig
from .integrations.workshop import item_id


class CommandKind(StrEnum):
    HELP = "help"
    QUERY = "query"
    OVERVIEW = "overview"
    ADDRESSES = "addresses"
    CONNECT = "connect"
    VOICE = "voice"
    WORKSHOP = "workshop"


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
    if content.startswith("语音"):
        argument = content.removeprefix("语音").strip()
        if not argument:
            raise ValueError("请输入服务器快捷名，例如：语音1服")
        return Command(CommandKind.VOICE, argument)
    if match := re.fullmatch(r"工坊\s+(\S+)(?:\s+([0-9]{1,3}))?", content):
        identifier = workshop_id_from_url(match[1]) if "://" in match[1] else item_id(match[1])
        if not identifier:
            raise ValueError("请输入有效的 Steam 工坊链接或条目 ID")
        page = int(match[2] or "1")
        if page < 1:
            raise ValueError("页码从 1 开始")
        return Command(CommandKind.WORKSHOP, identifier, page)
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
    for match in re.finditer(r"https?://[^\s<>]+", content):
        identifier = workshop_id_from_url(match[0].rstrip("。，；,;"))
        if identifier:
            return Command(CommandKind.WORKSHOP, identifier)
    return None


def workshop_id_from_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname not in {"steamcommunity.com", "www.steamcommunity.com"}
            or parsed.username is not None
            or parsed.password is not None
        ):
            return ""
        path = parsed.path.rstrip("/")
        if path in {"/sharedfiles/filedetails", "/workshop/filedetails"}:
            values = parse_qs(parsed.query).get("id", [])
            return item_id(values[0]) if len(values) == 1 else ""
        if match := re.fullmatch(r"/(?:sharedfiles|workshop)/filedetails/([0-9]+)", path):
            return item_id(match[1])
    except ValueError:
        return ""
    return ""


def match_server(servers: list[ServerConfig], name: str) -> ServerConfig | None:
    target = normalize_name(name)
    matches = [
        server
        for server in servers
        if target in {normalize_name(item) for item in [server.name, *server.aliases]}
    ]
    return matches[0] if len(matches) == 1 else None
