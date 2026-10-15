"""Real Steam details/collections, with the upstream downloader API as an option."""

from ..domain import WorkshopItem, WorkshopResult
from .cache import AsyncTTLCache
from .http import ExternalAPIError, ExternalHTTP, public_link


def item_id(value: object) -> str:
    text = str(value)
    if not text.isascii() or not text.isdigit() or len(text) > 20:
        raise ValueError("工坊 ID 必须是有效数字")
    number = int(text)
    if not 0 < number < 2**64:
        raise ValueError("工坊 ID 超出范围")
    return str(number)


def workshop_url(identifier: str) -> str:
    return f"https://steamcommunity.com/sharedfiles/filedetails/?id={item_id(identifier)}"


class WorkshopClient:
    def __init__(
        self,
        http: ExternalHTTP,
        *,
        api_url: str = "",
        steam_api_base: str = "https://api.steampowered.com",
        max_items: int = 20,
    ):
        self.http, self.api_url = http, api_url
        self.steam_api_base = steam_api_base.rstrip("/")
        self.max_items = max_items
        self._cache: AsyncTTLCache[WorkshopResult] = AsyncTTLCache(60, 128)

    async def query(self, identifier: str) -> WorkshopResult:
        identifier = item_id(identifier)
        try:
            return await self._cache.get(identifier, lambda: self._query(identifier))
        except ExternalAPIError as exc:
            return WorkshopResult(identifier, error=str(exc))

    async def _details(self, identifiers: list[str]) -> dict[str, dict]:
        if self.api_url:
            # Upstream workshop_utils.py POSTs a JSON array of integer IDs.
            details = await self.http.json("POST", self.api_url, json=[int(i) for i in identifiers])
        else:
            data = await self.http.json(
                "POST",
                self.steam_api_base + "/ISteamRemoteStorage/GetPublishedFileDetails/v1/",
                data={
                    "itemcount": str(len(identifiers)),
                    **{
                        f"publishedfileids[{i}]": identifier
                        for i, identifier in enumerate(identifiers)
                    },
                },
            )
            response = data.get("response") if isinstance(data, dict) else None
            details = response.get("publishedfiledetails") if isinstance(response, dict) else None
        if not isinstance(details, list) or len(details) > 100:
            raise ExternalAPIError("工坊详情格式错误")
        found = {}
        for detail in details:
            if not isinstance(detail, dict):
                continue
            key = str(detail.get("publishedfileid", ""))
            if key in identifiers:
                found[key] = detail
        return found

    async def _collection(self, identifier: str) -> list[str] | None:
        data = await self.http.json(
            "POST",
            self.steam_api_base + "/ISteamRemoteStorage/GetCollectionDetails/v1/",
            data={"collectioncount": "1", "publishedfileids[0]": identifier},
        )
        response = data.get("response") if isinstance(data, dict) else None
        details = response.get("collectiondetails") if isinstance(response, dict) else None
        if not isinstance(details, list) or not details or not isinstance(details[0], dict):
            raise ExternalAPIError("工坊合集格式错误")
        detail = details[0]
        if str(detail.get("publishedfileid", "")) != identifier:
            raise ExternalAPIError("工坊合集返回了不匹配的 ID")
        if detail.get("result") != 1:
            return None
        return self._child_ids(detail.get("children"))

    @staticmethod
    def _child_ids(children: object) -> list[str]:
        if not isinstance(children, list) or len(children) > 10000:
            raise ExternalAPIError("工坊合集列表格式错误")
        result = []
        seen = set()
        for child in children:
            if not isinstance(child, dict):
                raise ExternalAPIError("工坊合集条目格式错误")
            try:
                identifier = item_id(child.get("publishedfileid", ""))
            except ValueError:
                raise ExternalAPIError("工坊合集 ID 错误") from None
            if identifier not in seen:
                result.append(identifier)
                seen.add(identifier)
        return result

    @staticmethod
    def _item(identifier: str, raw: dict | None) -> WorkshopItem:
        if not raw or raw.get("result") != 1:
            return WorkshopItem(identifier, available=False)
        title = raw.get("title")
        filename = raw.get("filename")
        size = raw.get("file_size")
        try:
            size = int(size)
            if size < 0 or size >= 2**63 or isinstance(raw.get("file_size"), bool):
                size = None
        except (TypeError, ValueError, OverflowError):
            size = None
        return WorkshopItem(
            identifier,
            title=title[:200] if isinstance(title, str) else "",
            filename=filename.replace("\\", "/").rsplit("/", 1)[-1][:200]
            if isinstance(filename, str)
            else "",
            size_bytes=size,
            download_url=public_link(raw.get("file_url")),
        )

    async def _query(self, identifier: str) -> WorkshopResult:
        first = (await self._details([identifier])).get(identifier)
        if not first or first.get("result") != 1:
            return WorkshopResult(identifier, error="条目不可访问、已删除或不是公开工坊内容。")
        title = first.get("title")
        title = title[:200] if isinstance(title, str) else ""
        children = None
        if "children" in first:
            children = self._child_ids(first["children"])
        elif not self.api_url:
            # Steam's real details response can give collections a file_url while
            # omitting file_type. Only GetCollectionDetails can distinguish them.
            children = await self._collection(identifier)
        if children is None:
            return WorkshopResult(
                identifier, title=title, total_count=1, items=(self._item(identifier, first),)
            )
        selected = children[: self.max_items]
        details = await self._details(selected) if selected else {}
        return WorkshopResult(
            identifier,
            title=title,
            collection=True,
            total_count=len(children),
            items=tuple(self._item(child, details.get(child)) for child in selected),
        )

    async def close(self) -> None:
        await self._cache.close()
