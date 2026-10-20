import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web

from l4d2_qq_bot.app import BotApplication, route_event
from l4d2_qq_bot.cards.views import overview_card, server_card, voice_card
from l4d2_qq_bot.commands import CommandKind, parse_command
from l4d2_qq_bot.config import AppConfig, GroupConfig, ServerConfig, Settings
from l4d2_qq_bot.demo import sample_event, sample_statuses
from l4d2_qq_bot.domain import Player, VoiceStatus
from l4d2_qq_bot.integrations.http import ExternalHTTP
from l4d2_qq_bot.integrations.maps import MapNameClient
from l4d2_qq_bot.services.queries import QueryCoordinator


@pytest.mark.parametrize("event_type", ["GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"])
async def test_group_id_is_the_only_bootstrap_command_and_duplicates_are_ignored(event_type):
    client = AsyncMock()
    app = BotApplication(client, AppConfig(), Settings(_env_file=None), None, Mock())
    event = sample_event()
    event["t"] = event_type
    event["d"]["content"] = "<@!bot> /获取群ID"
    assert route_event(event, AppConfig()).command.kind == CommandKind.GROUP_ID
    app.start()
    try:
        assert app.offer_event(event)
        assert app.offer_event(event)
        await asyncio.wait_for(app.queue.join(), 1)
        client.send_group.assert_awaited_once_with(
            "DEMO_GROUP_OPENID",
            "DEMO_MESSAGE_ID",
            1,
            {"msg_type": 0, "content": "DEMO_GROUP_OPENID"},
        )
        for content in (
            "帮助",
            "查询1服",
            "查询1服语音",
            "1服语音",
            "设置1服 status",
            "获取用户ID",
            "晚上好",
        ):
            event["d"].update(content=content, id=content)
            assert app.offer_event(event)
        assert app.queue.empty()
    finally:
        await app.close()


def test_upstream_reply_contract_has_no_product_explanations():
    status = replace(
        sample_statuses()[0],
        server_name="真实服(测试)",
        map_name="死亡中心 | 旅馆",
        player_count=1,
        max_players=8,
        ping_ms=42,
        players=(Player("幸存者", duration_seconds=86401),),
        voice_count=2,
    )
    assert server_card(status, connection_url="https://connect.example/1").content == (
        "真实服(测试)\n地址: 203.0.113.10:27015\n地图: 死亡中心 | 旅馆\n"
        "人数: 1/8 (🎧2)\n延迟: 42ms\n"
        "\n在线玩家:\n幸*者 (1:00:00:01)\n点击直连: https://connect.example/1"
    )
    temporary = server_card(status, temporary=True).content
    assert "地址: 203.0.113.10:27015" in temporary and "🎧" not in temporary
    overview = overview_card([status, sample_statuses()[1]]).content
    assert overview.startswith("L4D2 服务器概览\n服务器: 1/2 在线\n人数: 1/8\n")
    assert "[合作服] 真实服(测试)" in overview and "| 旅馆" not in overview
    assert overview.endswith("[对抗服] 离线或无法连接")
    assert voice_card(VoiceStatus("1服", True, count=0, names=())).content == (
        "1服 语音频道当前无人在线。"
    )


async def test_all_query_routes_share_one_map_cache(local_server, http_session):
    calls = 0

    async def lookup(request):
        nonlocal calls
        calls += 1
        return web.Response(text="真实地图名称")

    server = web.Application()
    server.router.add_get("/c1m1_hotel", lookup)
    base = await local_server(server)
    maps = MapNameClient(ExternalHTTP(http_session), base)
    query = AsyncMock()
    query.query.return_value = sample_statuses()[0]
    coordinator = QueryCoordinator(query, maps)
    try:
        results = await asyncio.gather(
            coordinator.server(
                ServerConfig(name="甲", address="203.0.113.1"), GroupConfig(group_openid="a")
            ),
            coordinator.server(
                ServerConfig(name="乙", address="203.0.113.2"),
                GroupConfig(group_openid="b"),
                players=False,
            ),
            coordinator.address("203.0.113.3"),
        )
        assert [result.map_name for result in results] == ["真实地图名称"] * 3
        assert calls == 1
    finally:
        await maps.close()


def test_management_command_parser_keeps_raw_rcon_arguments():
    assert parse_command("设置 My Server say hello").argument == "My Server say hello"
    assert parse_command("重启1服").kind == CommandKind.RESTART
    assert parse_command("https://qfile.qq.com/q/abc_123").kind == CommandKind.FLASH
    assert parse_command("https://qfile.qq.com.evil.test/q/abc_123") is None
