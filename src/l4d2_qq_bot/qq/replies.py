"""Passive replies with a per-message sequence and a five-message budget."""

import logging
from typing import Any

from ..domain import CardLink
from .client import QQAPIError, QQClient, QQTransportError
from .protocol import GroupMessage

logger = logging.getLogger(__name__)

MARKDOWN_REJECTIONS = {"304036", "40034127", "50056"}
KEYBOARD_REJECTIONS = {"304057"}
URL_REJECTIONS = {"304003", "40054010"}
BUTTONS_PER_ROW = 2
MAX_LINK_BUTTONS = 5 * BUTTONS_PER_ROW
TEXT_MESSAGE_MAX_BYTES = 3500


def split_text(text: str, max_bytes: int = TEXT_MESSAGE_MAX_BYTES) -> list[str]:
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
    if len(links) > MAX_LINK_BUTTONS:
        raise ValueError(f"单条卡片最多支持 {MAX_LINK_BUTTONS} 个链接按钮")
    buttons = []
    for index, link in enumerate(links):
        button = connection_keyboard(link.label, link.url)["content"]["rows"][0]["buttons"][0]
        button["id"] = f"link_{index}"
        buttons.append(button)
    rows = [
        {"buttons": buttons[index : index + BUTTONS_PER_ROW]}
        for index in range(0, len(buttons), BUTTONS_PER_ROW)
    ]
    return {"content": {"rows": rows}}


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
        """Send IDs, notices and operation feedback without Markdown styling."""
        await self._plain_text(text)

    async def _plain_text(self, text: str) -> None:
        """Bound ordinary text replies and share the passive message budget."""
        chunks = split_text(text)
        if len(chunks) > 5 - self.sequence:
            raise ValueError("文字过长，请缩小查询范围或使用服务器列表页码")
        for chunk in chunks:
            await self.send({"msg_type": 0, "content": chunk})

    async def text_file(self, text: str) -> None:
        """Send one result file, or one short failure notice, without replaying the operation."""
        if self.sequence >= 5:
            raise ValueError("已达到本条群消息的回复上限")
        stage = "上传"
        try:
            media = await self.client.upload_txt(self.message.group_openid, text)
            stage = "发送"
            await self.send({"msg_type": 7, "media": {"file_info": media.file_info}})
        except (QQAPIError, QQTransportError, ValueError) as exc:
            # Result text, credentials and exception messages must never enter logs.
            logger.warning("RCON 结果文件%s失败（%s）", stage, type(exc).__name__)
            if stage == "发送" and isinstance(exc, QQTransportError):
                notice = "RCON 结果文件发送状态未确认，未重新执行指令。"
            else:
                notice = f"RCON 结果文件{stage}失败，未重新执行指令。"
            await self.text(notice)

    async def image(self, png: bytes) -> None:
        media = await self.client.upload_png(self.message.group_openid, png)
        await self.send({"msg_type": 7, "media": {"file_info": media.file_info}})

    async def markdown(
        self,
        content: str,
        *,
        keyboard: dict[str, Any] | None = None,
        fallback_text: str = "",
        plain_content: str | None = None,
    ) -> None:
        plain = content if plain_content is None else plain_content
        if not self._markdown_available:
            await self._plain_text(plain + (f"\n\n{fallback_text}" if fallback_text else ""))
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
                plain_replacement = plain + f"\n\n{reason}。"
                if fallback_text:
                    replacement += f"\n\n{fallback_text}"
                    plain_replacement += f"\n\n{fallback_text}"
                await self.markdown(replacement, plain_content=plain_replacement)
                return
            if code not in MARKDOWN_REJECTIONS and exc.status != 403:
                raise
            self._markdown_available = False
            logger.warning("原生 Markdown 被平台拒绝（HTTP %s，业务码 %s）", exc.status, exc.code)
        await self._plain_text(
            "原生卡片被平台拒绝，请检查应用 Markdown 权限。\n\n"
            + plain
            + (f"\n\n{fallback_text}" if fallback_text else "")
        )

    async def connection(self, label: str, text: str, url: str, *, buttons: bool) -> None:
        if buttons and url:
            await self.markdown(text, keyboard=connection_keyboard(label, url))
            return
        await self.text(text)
