"""Upstream panel_client API and selection rules, without AstrBot or response-body logs."""

import re
from pathlib import PurePosixPath

import aiohttp

from ..cards.views import clean_text
from .http import ExternalAPIError, ExternalHTTP, public_link


class PanelError(ValueError):
    pass


def selection(text: str, count: int, *, multiple: bool = True) -> list[int]:
    tokens = [token for token in re.split(r"[,，\s]+", text.strip()) if token]
    if text.strip() == "全部" and multiple:
        return list(range(count))
    if (
        not tokens
        or any(not re.fullmatch(r"[0-9]{1,3}", token) for token in tokens)
        or (not multiple and len(tokens) != 1)
    ):
        raise ValueError("已取消")
    numbers = [int(token) for token in tokens]
    if any(not 1 <= number <= count for number in numbers):
        raise ValueError(f"序号超出范围（1-{count}），已取消")
    return list(dict.fromkeys(number - 1 for number in numbers))


def supported_items(items: list[dict]) -> list[dict]:
    result = []
    for item in items:
        name = item.get("filename") or item.get("title")
        if not isinstance(name, str):
            continue
        name = clean_text(name.replace("\\", "/").rsplit("/", 1)[-1], 200)
        if (
            item.get("supported") is True
            and PurePosixPath(name).suffix.lower() in {".vpk", ".zip", ".rar", ".7z"}
            and public_link(item.get("file_url"))
        ):
            result.append({**item, "filename": name})
    return result


def file_size(value: object) -> str:
    try:
        number = float(value) if not isinstance(value, bool) else 0
    except (ValueError, TypeError):
        return clean_text(value, 40) if isinstance(value, str) and value else "未知大小"
    if not 0 < number < 2**63:
        return "未知大小"
    for unit, step in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if number >= step:
            return f"{number / step:.2f} {unit}"
    return f"{int(number)} B"


class PanelClient:
    def __init__(self, http: ExternalHTTP, base_url: str, token: str):
        self.http, self.base_url = http, base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}

    async def parse(self, share_url: str) -> list[dict]:
        try:
            data = await self.http.json(
                "POST",
                self.base_url + "/download/link/parse",
                json={"url": share_url},
                headers=self._headers,
                timeout_seconds=60,
            )
        except ExternalAPIError as exc:
            raise PanelError(f"面板请求失败（{exc.reason}）") from None
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list) or len(items) > 100:
            raise PanelError("面板返回的数据格式异常（缺少文件列表或文件过多）")
        return supported_items([item for item in items if isinstance(item, dict)])

    async def add(self, item: dict) -> None:
        form = aiohttp.FormData()
        for field, value in (
            ("url", item["file_url"]),
            ("filename", item["filename"]),
            ("referer", public_link(item.get("referer"))),
        ):
            if value:
                form.add_field(field, value)
        try:
            await self.http.read(
                "POST",
                self.base_url + "/download/add",
                data=form,
                headers=self._headers,
                timeout_seconds=30,
            )
        except ExternalAPIError as exc:
            # A timed out POST may have been accepted. Never retry it automatically.
            raise PanelError(f"面板请求未确认成功（{exc.reason}），请检查面板任务列表") from None
