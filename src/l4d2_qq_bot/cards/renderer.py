"""Deterministic local PNG cards, using a system CJK font."""

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..domain import Card
from .views import clean_text

FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "/System/Library/Fonts/PingFang.ttc",
)


class CardRenderer:
    width = 1040
    max_height = 3200
    padding = 48

    def __init__(self, font_path: Path | None = None):
        if font_path is None:
            font_path = next((Path(path) for path in FONT_CANDIDATES if Path(path).is_file()), None)
        if font_path is None or not font_path.is_file():
            raise ValueError("未找到中文字体，请安装 Noto CJK 或设置 L4D2_CARD_FONT_PATH")
        self.font_path = font_path
        self.title_font = ImageFont.truetype(str(font_path), 42)
        self.heading_font = ImageFont.truetype(str(font_path), 23)
        self.body_font = ImageFont.truetype(str(font_path), 25)
        self.small_font = ImageFont.truetype(str(font_path), 19)

    @staticmethod
    def _ellipsize(text: str, font: ImageFont.FreeTypeFont, width: int) -> str:
        if font.getlength(text) <= width:
            return text
        while text and font.getlength(text + "…") > width:
            text = text[:-1]
        return text + "…"

    def _wrap(self, text: str, width: int) -> list[str]:
        text = clean_text(text, 240) or "—"
        lines: list[str] = []
        line = ""
        for index, character in enumerate(text):
            if self.body_font.getlength(line + character) > width and line:
                lines.append(line)
                line = character
                if len(lines) == 2:
                    remainder = text[index:]
                    lines.append(self._ellipsize(remainder, self.body_font, width))
                    return lines
            else:
                line += character
        if line:
            lines.append(line)
        return lines

    def render(self, card: Card) -> bytes:
        if card.content:
            return self._render_content(card)
        content_width = self.width - self.padding * 2 - 48
        layouts: list[tuple[str, list[str], int]] = []
        height = 212
        truncated = len(card.sections) > 8
        for section in card.sections[:8]:
            truncated = truncated or len(section.lines) > 16
            lines: list[str] = []
            for line in section.lines[:16]:
                lines.extend(self._wrap(line, content_width))
            truncated = truncated or len(lines) > 24
            lines = lines[:24]
            section_height = 78 + 38 * len(lines)
            if height + section_height + 108 > self.max_height:
                truncated = True
                break
            layouts.append((section.heading, lines, section_height))
            height += section_height + 16
        height += 96
        image = Image.new("RGB", (self.width, height), "#101711")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((0, 0, self.width - 1, height - 1), radius=28, outline="#344432")
        draw.rounded_rectangle((48, 35, 54, 77), radius=3, fill=card.accent)
        draw.text((70, 44), "L4D2 / SERVER QUERY", font=self.small_font, fill=card.accent)
        title = self._ellipsize(clean_text(card.title, 120), self.title_font, self.width - 96)
        draw.text((48, 88), title, font=self.title_font, fill="#f0f6eb")
        subtitle = self._ellipsize(clean_text(card.subtitle, 240), self.small_font, self.width - 96)
        draw.text((48, 151), subtitle, font=self.small_font, fill="#a7b69e")
        y = 212
        for heading, lines, section_height in layouts:
            draw.rounded_rectangle(
                (48, y, self.width - 48, y + section_height),
                radius=16,
                fill="#1b271c",
                outline="#31422d",
            )
            draw.text(
                (72, y + 19),
                self._ellipsize(clean_text(heading, 100), self.heading_font, content_width),
                font=self.heading_font,
                fill=card.accent,
            )
            line_y = y + 61
            for line in lines:
                draw.text((72, line_y), line, font=self.body_font, fill="#e4ecdd")
                line_y += 38
            y += section_height + 16
        footer = ("部分内容已省略 · " if truncated else "") + clean_text(card.footer, 200)
        draw.text(
            (48, height - 55),
            self._ellipsize(footer, self.small_font, self.width - 96),
            font=self.small_font,
            fill="#8da183",
        )
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        data = output.getvalue()
        if len(data) > 3 * 1024 * 1024:
            raise ValueError("卡片超过 3 MiB，需要拆分后再上传")
        return data

    def _render_content(self, card: Card) -> bytes:
        lines = []
        for line in card.content.splitlines():
            lines.extend(self._wrap(line, self.width - 96) if line else [""])
        maximum = (self.max_height - 96) // 38
        if len(lines) > maximum:
            lines = lines[: maximum - 1] + ["…"]
        image = Image.new("RGB", (self.width, max(172, 96 + 38 * len(lines))), "#101711")
        draw = ImageDraw.Draw(image)
        for index, line in enumerate(lines):
            draw.text((48, 48 + index * 38), line, font=self.body_font, fill="#e4ecdd")
        output = io.BytesIO()
        image.save(output, format="PNG", optimize=True)
        return output.getvalue()
