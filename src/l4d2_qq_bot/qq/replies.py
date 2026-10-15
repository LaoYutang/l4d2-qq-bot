"""Passive replies with a per-message sequence and a five-message budget."""

import logging
from typing import Any

from ..domain import CardLink
from .client import QQAPIError, QQClient
from .protocol import GroupMessage

logger = logging.getLogger(__name__)

MARKDOWN_REJECTIONS = {"304036", "40034127", "50056"}
KEYBOARD_REJECTIONS = {"304057"}
URL_REJECTIONS = {"304003", "40054010"}


def split_text(text: str, max_bytes: int = 3500) -> list[str]:
    chunks: list[str] = []
    current = ""
    size = 0
    for line in text.splitlines(keepends=True):
        line_size = len(line.encode("utf-8"))
        if size and size + line_size > max_bytes:
            chunks.append(current.rstrip())
            current, size = "", 0
        for character in line:
            character_size = len(character.encode("utf-8"))
            if size + character_size > max_bytes:
                chunks.append(current.rstrip())
                current, size = "", 0
            current += character
            size += character_size
    if current.strip():
        chunks.append(current.rstrip())
    return chunks


def connection_keyboard(label: str, url: str, *, command: str = "") -> dict[str, Any]:
    if not url and not command:
        raise ValueError("连接按钮缺少跳转地址或连接指令")
    action = {
        "type": 0 if url else 2,
        "data": url or command,
        "permission": {"type": 2},
        "unsupport_tips": "当前客户端不支持此按钮，请更新 QQ 后重试。",
    }
    if not url:
        action["enter"] = False
    return {
        "content": {
            "rows": [
                {
                    "buttons": [
                        {
                            "id": "connect_server",
                            "render_data": {
                                "label": label[:10],
                                "visited_label": label[:10],
                                "style": 1,
                            },
                            "action": action,
                        }
                    ]
                }
            ],
        }
    }


def link_keyboard(links: tuple[CardLink, ...]) -> dict[str, Any]:
    buttons = []
    for index, link in enumerate(links[:5]):
        button = connection_keyboard(link.label, link.url)["content"]["rows"][0]["buttons"][0]
        button["id"] = f"link_{index}"
        buttons.append(button)
    return {"content": {"rows": [{"buttons": buttons}]}}


class ReplySession:
    def __init__(self, client: QQClient, message: GroupMessage):
        self.client = client
        self.message = message
        self.sequence = 0
        self.sent_count = 0
        self._markdown_available = True
        self._keyboard_available = True

    async def send(self, content: dict[str, Any]) -> None:
        if self.sequence >= 5:
            raise ValueError("已达到本条群消息的回复上限")
        self.sequence += 1
        await self.client.send_group(
            self.message.group_openid, self.message.message_id, self.sequence, content
        )
        self.sent_count += 1

    async def text(self, text: str) -> None:
        chunks = split_text(text)
        if len(chunks) > 5 - self.sequence:
            raise ValueError("文字过长，请缩小查询范围或使用服务器列表页码")
        for chunk in chunks:
            await self.send({"msg_type": 0, "content": chunk})

    async def image(self, png: bytes) -> None:
        media = await self.client.upload_png(self.message.group_openid, png)
        await self.send({"msg_type": 7, "media": {"file_info": media.file_info}})

    async def markdown(
        self, content: str, *, keyboard: dict[str, Any] | None = None, fallback_text: str = ""
    ) -> None:
        if not self._markdown_available:
            await self.text(content + (f"\n\n{fallback_text}" if fallback_text else ""))
            return
        payload: dict[str, Any] = {"msg_type": 2, "markdown": {"content": content}}
        if keyboard is not None and self._keyboard_available:
            payload["keyboard"] = keyboard
        elif fallback_text:
            payload["markdown"]["content"] += f"\n\n{fallback_text}"
        try:
            await self.send(payload)
            logger.info(
                "原生 Markdown 卡片已发送（按钮：%s）", "有" if "keyboard" in payload else "无"
            )
            return
        except QQAPIError as exc:
            code = str(exc.code)
            if "keyboard" in payload and (
                code in KEYBOARD_REJECTIONS | MARKDOWN_REJECTIONS | URL_REJECTIONS
                or exc.status == 403
            ):
                self._keyboard_available = False
                reason = (
                    "按钮链接被平台拒绝，请检查 URL 报备"
                    if code in URL_REJECTIONS
                    else "自定义按钮被平台拒绝，请检查应用按钮权限"
                )
                logger.warning("%s（HTTP %s，业务码 %s）", reason, exc.status, exc.code)
                # Only replace a confirmed rejection. Keep the original passive msg_id.
                replacement = content + f"\n\n> {reason}。"
                if fallback_text:
                    replacement += f"\n\n{fallback_text}"
                await self.markdown(replacement)
                return
            if code not in MARKDOWN_REJECTIONS and exc.status != 403:
                raise
            self._markdown_available = False
            logger.warning("原生 Markdown 被平台拒绝（HTTP %s，业务码 %s）", exc.status, exc.code)
        await self.text(
            "原生卡片被平台拒绝，请检查应用 Markdown 权限。\n\n"
            + content
            + (f"\n\n{fallback_text}" if fallback_text else "")
        )

    async def connection(self, label: str, text: str, url: str, *, buttons: bool) -> None:
        if buttons and url:
            await self.markdown(text, keyboard=connection_keyboard(label, url))
            return
        await self.text(text)
