"""Native QQ Markdown from the same sanitized presentation model as PNG cards."""

import html
import re

from ..domain import Card
from .views import clean_text


def escape_text(value: str, limit: int = 200) -> str:
    value = html.escape(clean_text(value, limit), quote=False)
    # QQ treats \( ... \) and \[ ... \] as math delimiters. Bare parentheses
    # are ordinary prose; entities keep square brackets literal without creating links.
    value = re.sub(r"([\\`*_{}#+!|>~])", r"\\\1", value)
    # Escape list/separator markers without splitting hyphens inside URL hostnames.
    value = re.sub(r"^-", r"\\-", value)
    return value.replace("[", "&#91;").replace("]", "&#93;")


def markdown_text(content: str, *, player_list_continuation: bool = False) -> str:
    """Style the existing reply body without adding titles, labels or footer text."""
    blocks: list[str] = []
    first = True
    in_player_list = player_list_continuation
    lines = content.splitlines()
    is_overview = "L4D2 服务器概览" in lines[:2]
    is_server = not is_overview and any(line.startswith("地图: ") for line in lines[:4])
    for line in lines:
        padding = line[: len(line) - len(line.lstrip())]
        text = escape_text(line.lstrip(), len(line))
        if not text:
            blocks.append("")
            continue
        if line == "在线玩家:" and (not first or player_list_continuation):
            in_player_list = True
        is_player = in_player_list and re.fullmatch(r".+ \(\d+(?::\d{2}){1,3}\)", line)
        if line != "在线玩家:" and not is_player:
            in_player_list = False
        short = len(text.encode("utf-8")) <= 1000
        is_quote = not padding and (
            (is_overview and line.startswith(("服务器: ", "人数: ")))
            or (is_server and line.startswith(("地图: ", "人数: ", "延迟: ")))
        )
        if is_player:
            item = f"- {text}"
            if blocks and blocks[-1].startswith("- "):
                blocks[-1] += f"\n{item}"
            else:
                blocks.append(item)
        elif is_quote:
            if blocks and blocks[-1].startswith("> "):
                blocks[-1] += f"\n> {text}"
            else:
                blocks.append(f"> {text}")
        elif not padding and short and line.startswith("["):
            blocks.append(f"**{text}**")
        elif first and short:
            blocks.append(f"# {text}")
        elif not padding and short and text.endswith((":", "：")):
            blocks.append(f"## {text}")
        elif not padding and short and re.match(r"^.{1,12}[:：] ", line):
            blocks.append(f"**{text}**")
        else:
            # Escape numbered prompts so Markdown cannot renumber file/server choices.
            text = re.sub(r"^(\d+)\.(?=\s)", r"\1\\.", text)
            # Plain-text alignment spaces would create an indented code block in QQ.
            blocks.append(text)
        first = False
    return "\n\n".join(blocks)


def markdown_text_pages(content: str, *, max_bytes: int = 3200) -> list[tuple[str, str]]:
    """Paginate source text before formatting so escapes and styles stay complete.

    Each page keeps its original text for an explicitly rejected Markdown reply.
    """
    pages: list[tuple[str, str]] = []
    start = 0
    player_list_start = content.find("\n在线玩家:\n")
    while start < len(content):
        player_list_continuation = 0 <= player_list_start < start
        lower, upper = 0, min(len(content) - start, max_bytes)
        while lower < upper:
            middle = (lower + upper + 1) // 2
            formatted = markdown_text(
                content[start : start + middle], player_list_continuation=player_list_continuation
            )
            if len(formatted.encode("utf-8")) <= max_bytes:
                lower = middle
            else:
                upper = middle - 1
        if not lower:
            raise ValueError("Markdown 单页字节预算不足")
        end = start + lower
        if end < len(content):
            boundary = content.rfind("\n", start, end)
            if boundary >= start + lower // 2:
                end = boundary + 1
        plain = content[start:end]
        formatted = markdown_text(plain, player_list_continuation=player_list_continuation)
        if formatted.strip():
            pages.append((formatted, plain))
        start = end
    return pages


def markdown_card(card: Card, *, connection_command: str = "", max_bytes: int = 3200) -> str:
    """Bound one message and reserve space for the connection command and footer."""
    if card.content:
        content = markdown_text(card.content)
        if len(content.encode("utf-8")) > max_bytes:
            content = (
                content.encode("utf-8")[: max_bytes - 3].decode("utf-8", errors="ignore") + "…"
            )
        return content
    ending = []
    if connection_command:
        ending.append(f"**连接指令**\n{escape_text(connection_command)}")
    for link in card.links[:2]:
        if len(link.url) <= 512:
            url = link.url.replace("(", "%28").replace(")", "%29").replace("\\", "%5C")
            ending.append(f"[{escape_text(link.label, 40)}]({url})")
        else:
            ending.append(f"{escape_text(link.label, 40)}：请使用下方按钮。")
    if card.footer:
        ending.append(f"> {escape_text(card.footer)}")
    suffix = "\n\n".join(ending)
    notice = "\n\n部分内容较长，已省略。"
    budget = max_bytes - len((suffix + notice + "\n\n").encode("utf-8"))
    blocks = [f"# {escape_text(card.title, 80)}"]
    if card.subtitle:
        blocks.append(escape_text(card.subtitle))
    for section in card.sections:
        if connection_command and section.heading in {"连接服务器", "服务器地址"}:
            continue
        blocks.append(f"## {escape_text(section.heading, 80)}")
        blocks.extend(escape_text(line, 160) for line in section.lines)
    result = ""
    truncated = False
    for block in blocks:
        candidate = result + ("\n\n" if result else "") + block
        if len(candidate.encode("utf-8")) > budget:
            truncated = True
            break
        result = candidate
    if truncated:
        result += notice
    return result + ("\n\n" + suffix if suffix else "")
