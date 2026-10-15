import asyncio
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from l4d2_qq_bot.cards.views import voice_card
from l4d2_qq_bot.integrations.cache import AsyncTTLCache
from l4d2_qq_bot.integrations.heybox import HeyboxVoiceClient
from l4d2_qq_bot.integrations.http import ExternalAPIError, ExternalHTTP
from l4d2_qq_bot.integrations.maps import MapNameClient


@pytest.mark.parametrize("map_name", ["死亡中心：旅馆", "[三方图] 返回学校"])
async def test_real_map_http_coalesces_and_caches_names(map_name, local_server, http_session):
    calls = 0

    async def lookup(request):
        nonlocal calls
        calls += 1
        assert request.match_info["code"] == "c1m1_hotel"
        assert "Authorization" not in request.headers and "token" not in request.headers
        return web.Response(text=map_name)

    server = web.Application()
    server.router.add_get("/maps/{code}", lookup)
    base = await local_server(server)
    maps = MapNameClient(ExternalHTTP(http_session), base + "/maps")
    try:
        results = await asyncio.gather(*(maps.name("c1m1_hotel") for _ in range(8)))
        assert results == [map_name] * 8
        assert await maps.name("c1m1_hotel") == map_name
        assert calls == 1
    finally:
        await maps.close()


@pytest.mark.parametrize(
    "response", ["", "<html>error</html>", "bad" * 1500, '{"error":"bad"}', '["error"]']
)
async def test_bad_map_response_keeps_real_code(response, local_server, http_session):
    async def bad(request):
        return web.Response(text=response)

    server = web.Application()
    server.router.add_get("/c1m1_hotel", bad)
    base = await local_server(server)
    maps = MapNameClient(ExternalHTTP(http_session), base)
    try:
        assert await maps.name("c1m1_hotel") == "c1m1_hotel"
        assert await maps.name("../private") == "../private"
    finally:
        await maps.close()


async def test_cancelled_map_waiter_does_not_cancel_shared_read():
    cache = AsyncTTLCache[str](3600)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def load():
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        return "real name"

    first = asyncio.create_task(cache.get("map", load))
    await entered.wait()
    second = asyncio.create_task(cache.get("map", load))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    assert await second == "real name" and calls == 1
    await cache.close()


async def test_external_errors_never_include_response_body(local_server, http_session):
    async def error(request):
        return web.Response(status=403, text="private response with token")

    server = web.Application()
    server.router.add_get("/error", error)
    base = await local_server(server)
    with pytest.raises(ExternalAPIError) as error:
        await ExternalHTTP(http_session).json("GET", base + "/error")
    assert "private" not in str(error.value) and "token" not in str(error.value)


async def test_heybox_real_http_names_are_cached_but_counts_remain_live(local_server, http_session):
    counts, names = 0, 0

    async def channel(request):
        nonlocal counts
        counts += 1
        assert request.headers["token"] == "fixture-voice-token"
        assert "Authorization" not in request.headers
        assert request.query["room_id"] == "room" and request.query["channel_id"] == "channel"
        return web.json_response({"status": "ok", "result": {"user_ids": ["1", "2"]}})

    async def room(request):
        nonlocal names
        names += 1
        return web.json_response(
            {
                "status": "ok",
                "result": {
                    "room_info": {
                        "user_count": 2,
                        "user_info": [
                            {"user_id": "1", "nickname": "幸存者"},
                            {"user_id": "2", "username": "Alice"},
                        ],
                    }
                },
            }
        )

    server = web.Application()
    server.router.add_get("/chatroom/v2/channel/user/list", channel)
    server.router.add_get("/chatroom/v2/room/users", room)
    base = await local_server(server)
    voice = HeyboxVoiceClient(
        ExternalHTTP(http_session), "bot", "fixture-voice-token", api_base=base
    )
    try:
        for _ in range(2):
            status = await voice.query("1服", "room", "channel")
            assert status.count == 2 and status.names == ("幸存者", "Alice")
        assert counts == 2 and names == 1
        card = voice_card(status)
        content = " ".join(line for section in card.sections for line in section.lines)
        assert "幸存者" in content and "Alice" in content
        assert "- 幸存者" in card.content and "- Alice" in card.content
    finally:
        await voice.close()


@pytest.mark.parametrize("result", [{"user_ids": []}, {}, {"user_ids": None}])
@pytest.mark.parametrize("include_names", [True, False])
async def test_heybox_upstream_empty_channel_responses(
    result, include_names, local_server, http_session
):
    async def channel(request):
        return web.json_response({"status": "ok", "result": result})

    server = web.Application()
    server.router.add_get("/chatroom/v2/channel/user/list", channel)
    base = await local_server(server)
    voice = HeyboxVoiceClient(ExternalHTTP(http_session), "bot", "secret", api_base=base)
    try:
        status = await voice.query("1服", "room", "channel", include_names=include_names)
        assert status.count == 0 and status.names == () and not status.error
        assert voice_card(status).content == "1服 语音频道当前无人在线。"
    finally:
        await voice.close()


@pytest.mark.parametrize("user_ids", ["", {}, 0, False])
async def test_heybox_invalid_channel_list_is_not_zero(user_ids):
    http = AsyncMock()
    http.json.return_value = {"status": "ok", "result": {"user_ids": user_ids}}
    voice = HeyboxVoiceClient(http, "bot", "fixture-token")
    try:
        status = await voice.query("1服", "room", "channel")
        assert status.count is None and status.error == "外部接口不可用（黑盒成员列表格式错误）"
    finally:
        await voice.close()


@pytest.mark.parametrize(
    "payload",
    [
        {"status": "error", "result": {"user_ids": []}},
        {"status": "ok", "result": None},
        {"status": "ok", "result": []},
        {"status": "ok"},
    ],
)
async def test_heybox_unsuccessful_or_malformed_response_is_not_empty(payload):
    http = AsyncMock()
    http.json.return_value = payload
    voice = HeyboxVoiceClient(http, "bot", "fixture-token")
    try:
        status = await voice.query("1服", "room", "channel")
        assert status.count is None and status.error
    finally:
        await voice.close()


async def test_heybox_empty_channel_does_not_cache_zero_after_members_join():
    http = AsyncMock()
    http.json.side_effect = [
        {"status": "ok", "result": {}},
        {"status": "ok", "result": {"user_ids": ["1"]}},
        {
            "status": "ok",
            "result": {"room_info": {"user_info": [{"user_id": "1", "nickname": "幸存者"}]}},
        },
        {"status": "ok", "result": {"user_ids": None}},
    ]
    voice = HeyboxVoiceClient(http, "bot", "fixture-token")
    try:
        assert (await voice.query("1服", "room", "channel")).count == 0
        occupied = await voice.query("1服", "room", "channel")
        assert occupied.count == 1 and occupied.names == ("幸存者",)
        assert (await voice.query("1服", "room", "channel")).count == 0
        assert http.json.await_count == 4
    finally:
        await voice.close()


async def test_heybox_missing_credentials_do_not_make_up_zero():
    http = AsyncMock()
    voice = HeyboxVoiceClient(http)
    status = await voice.query("1服", "room", "channel")
    assert not status.configured and status.count is None and status.error
    http.json.assert_not_awaited()
    await voice.close()


async def test_heybox_nickname_failure_preserves_count(local_server, http_session):
    async def channel(request):
        return web.json_response({"status": "ok", "result": {"user_ids": ["1"]}})

    async def room(request):
        return web.Response(status=500)

    server = web.Application()
    server.router.add_get("/chatroom/v2/channel/user/list", channel)
    server.router.add_get("/chatroom/v2/room/users", room)
    base = await local_server(server)
    voice = HeyboxVoiceClient(ExternalHTTP(http_session), "bot", "secret", api_base=base)
    try:
        status = await voice.query("1服", "room", "channel")
        assert status.count == 1 and status.names is None and status.error
    finally:
        await voice.close()


async def test_heybox_room_user_pages_are_bounded_and_preserve_order(local_server, http_session):
    offsets = []

    async def channel(request):
        return web.json_response({"status": "ok", "result": {"user_ids": ["2", "101"]}})

    async def room(request):
        offset = int(request.query["offset"])
        offsets.append(offset)
        users = [
            {"user_id": str(i), "nickname": f"name-{i}"}
            for i in range(offset + 1, min(offset + 101, 121))
        ]
        return web.json_response(
            {
                "status": "ok",
                "result": {
                    "room_info": {
                        "user_count": 120,
                        "user_info": users,
                    }
                },
            }
        )

    server = web.Application()
    server.router.add_get("/chatroom/v2/channel/user/list", channel)
    server.router.add_get("/chatroom/v2/room/users", room)
    base = await local_server(server)
    voice = HeyboxVoiceClient(ExternalHTTP(http_session), "bot", "secret", api_base=base)
    try:
        status = await voice.query("1服", "room", "channel")
        assert status.count == 2 and status.names == ("name-2", "name-101")
        assert offsets == [0, 100]
    finally:
        await voice.close()


async def test_game_status_survives_map_and_voice_failures():
    from l4d2_qq_bot.config import GroupConfig, ServerConfig
    from l4d2_qq_bot.domain import ServerStatus, VoiceStatus
    from l4d2_qq_bot.services.queries import QueryCoordinator

    query, maps, voice = AsyncMock(), AsyncMock(), AsyncMock()
    query.query.return_value = ServerStatus(
        "1服", "203.0.113.1:27015", True, server_name="真名", map_code="real_map", player_count=7
    )
    maps.name.return_value = "real_map"
    voice.configured = lambda *args: True
    voice.query.return_value = VoiceStatus("1服", True, error="外部接口不可用")
    group = GroupConfig(group_openid="fixture", hh_room_id="room")
    server = ServerConfig(name="1服", address="203.0.113.1", hh_channel_id="channel")
    result = await QueryCoordinator(query, maps, voice).server(server, group)
    assert result.online and result.server_name == "真名" and result.player_count == 7
    assert result.map_name == "real_map" and result.voice_count is None and result.voice_error
