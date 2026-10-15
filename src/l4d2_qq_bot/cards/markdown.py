"""Native QQ Markdown from the same sanitized presentation model as PNG cards."""

import html
import re

from ..domain import Card
from .views import clean_text


def escape_text(value: str, limit: int = 200) -> str:
    value = html.escape(clean_text(value, limit), quote=False)
    # QQ treats \( ... \) and \[ ... \] as math delimiters. Bare parentheses
    # are ordinary prose; entities keep square brackets literal without creating links.
    value = re.sub(r"([\\`*_{}#+!|>~-])", r"\\\1", value)
    return value.replace("[", "&#91;").replace("]", "&#93;")


def markdown_card(card: Card, *, connection_command: str = "", max_bytes: int = 3200) -> str:
    """Bound one message and reserve space for the connection command and footer."""
    if card.content:
        lines = []
        for line in card.content.splitlines():
            padding = line[: len(line) - len(line.lstrip())]
            lines.append(padding + escape_text(line.lstrip(), 2400))
        content = "\n".join(lines)
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
