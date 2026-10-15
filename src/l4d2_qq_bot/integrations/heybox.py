"""Heybox read-only API adapter based on LaoYutang/heybox_voice.py (AGPL-3.0)."""

from ..domain import VoiceStatus
from .cache import AsyncTTLCache
from .http import ExternalAPIError, ExternalHTTP


class HeyboxVoiceClient:
    COMMON_PARAMS = {
        "client_type": "heybox_chat",
        "x_client_type": "web",
        "os_type": "web",
        "x_os_type": "bot",
        "x_app": "heybox_chat",
        "chat_os_type": "bot",
        "chat_version": "1.30.0",
    }

    def __init__(
        self,
        http: ExternalHTTP,
        bot_id: str = "",
        token: str = "",
        *,
        api_base: str = "https://chat.xiaoheihe.cn",
        max_pages: int = 20,
    ):
        self.http, self.bot_id, self._token = http, bot_id, token
        self.api_base = api_base.rstrip("/")
        self.max_pages = max_pages
        self._rooms: AsyncTTLCache[dict[str, str]] = AsyncTTLCache(600, 128)

    def configured(self, room_id: str, channel_id: str) -> bool:
        return bool(self.bot_id and self._token and room_id and channel_id)

    async def _get(self, path: str, **params) -> dict:
        data = await self.http.json(
            "GET",
            self.api_base + path,
            params={**self.COMMON_PARAMS, "heybox_id": self.bot_id, **params},
            headers={"token": self._token},
        )
        if not isinstance(data, dict) or data.get("status") != "ok":
            raise ExternalAPIError("黑盒接口未返回成功状态")
        result = data.get("result")
        if not isinstance(result, dict):
            raise ExternalAPIError("黑盒响应格式错误")
        return result

    async def query(
        self, alias: str, room_id: str, channel_id: str, *, include_names: bool = True
    ) -> VoiceStatus:
        if not self.configured(room_id, channel_id):
            return VoiceStatus(alias, False, error="未配置黑盒机器人凭据、群房间或服务器频道。")
        try:
            result = await self._get(
                "/chatroom/v2/channel/user/list", room_id=room_id, channel_id=channel_id
            )
            user_ids = result.get("user_ids")
            # Upstream treats omitted/null user_ids in a successful channel
            # response as an empty channel. Other value types remain invalid.
            if user_ids is None:
                user_ids = []
            if not isinstance(user_ids, list) or len(user_ids) > 10000:
                raise ExternalAPIError("黑盒成员列表格式错误")
            if any(not isinstance(item, (str, int)) or isinstance(item, bool) for item in user_ids):
                raise ExternalAPIError("黑盒成员 ID 格式错误")
            identifiers = list(dict.fromkeys(str(item) for item in user_ids))
        except ExternalAPIError as exc:
            return VoiceStatus(alias, True, error=str(exc))
        count = len(identifiers)
        if not count or not include_names:
            return VoiceStatus(alias, True, count=count, names=() if not count else None)
        try:
            users = await self._rooms.get(room_id, lambda: self._room_users(room_id))
        except ExternalAPIError:
            return VoiceStatus(alias, True, count=count, error="已获取人数，成员昵称查询失败。")
        names = tuple(users[identifier] for identifier in identifiers if users.get(identifier))
        error = "部分成员未返回昵称或超出分页上限。" if len(names) != count else ""
        return VoiceStatus(alias, True, count=count, names=names, error=error)

    async def _room_users(self, room_id: str) -> dict[str, str]:
        users = {}
        offset = 0
        for _ in range(self.max_pages):
            result = await self._get(
                "/chatroom/v2/room/users", room_id=room_id, offset=offset, limit=100
            )
            room = result.get("room_info")
            if not isinstance(room, dict) or not isinstance(room.get("user_info"), list):
                raise ExternalAPIError("黑盒昵称列表格式错误")
            page = room["user_info"]
            if len(page) > 100:
                raise ExternalAPIError("黑盒分页长度错误")
            for user in page:
                if not isinstance(user, dict) or not isinstance(user.get("user_id"), (str, int)):
                    continue
                name = user.get("nickname") or user.get("username")
                if isinstance(name, str) and name.strip():
                    users[str(user["user_id"])] = name[:200]
            offset += len(page)
            if len(page) < 100:
                break
            total = room.get("user_count")
            if isinstance(total, int) and total >= 0 and offset >= total:
                break
        return users

    async def close(self) -> None:
        await self._rooms.close()
