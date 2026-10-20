import html
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from l4d2_qq_bot.app import BotApplication
from l4d2_qq_bot.cards.views import server_card
from l4d2_qq_bot.config import ServerConfig, Settings
from l4d2_qq_bot.demo import sample_config, sample_event, sample_statuses
from l4d2_qq_bot.qq.client import QQAPIError
from l4d2_qq_bot.qq.protocol import GroupMessage
from l4d2_qq_bot.qq.replies import ReplySession

CONNECT_BASE = "https://connect.example.test/custom/join"
VOICE_URL = "https://voice.example.test/invite"


@asynccontextmanager
async def query_reply(
    *,
    address="l4d2-server.example.test:28015",
    saved=True,
    online=True,
    buttons=True,
    base_url=CONNECT_BASE,
    reply_format="markdown",
    rejection_codes=(),
):
    config = sample_config()
    config.connect_base_url = base_url
    server = ServerConfig(
        name="合作服",
        aliases=["1服"],
        address=address,
        voice_connect_url=VOICE_URL,
        panel_url="https://internal-panel.example.test:8443/manage",
        panel_token="fixture-panel-token",
        rcon_password="fixture-rcon-password",
    )
    config.groups[0].servers = [server] if saved else []
    client, query, renderer = AsyncMock(), AsyncMock(), Mock()
    renderer.render.return_value = b"fixture-image"
    client.upload_png.return_value = SimpleNamespace(file_info="fixture-image-info")
    if rejection_codes:
        client.send_group.side_effect = [*(QQAPIError(200, code) for code in rejection_codes), None]

    async def result(address, *, alias="", **kwargs):
        status = sample_statuses()[0 if online else 1]
        return replace(status, alias=alias or address, address=address)

    query.query.side_effect = result
    app = BotApplication(
        client,
        config,
        Settings(_env_file=None, connect_buttons=buttons, reply_format=reply_format),
        renderer,
        Mock(),
        query_service=query,
    )
    message = replace(
        GroupMessage.from_payload(sample_event()),
        content="查询1服" if saved else f"connect {address}",
    )
    try:
        await app.handle_message(message, ReplySession(client, message))
        yield client, query, renderer, server
    finally:
        await app.close()


def body(payload):
    if payload["msg_type"] == 2:
        return html.unescape(payload["markdown"]["content"])
    return payload["content"]


@pytest.mark.parametrize(
    "address", ["203.0.113.42:27016", "l4d2-server.example.test:28015", "[2001:db8::42]:27017"]
)
@pytest.mark.parametrize("online", [True, False])
@pytest.mark.parametrize("saved", [True, False])
async def test_saved_and_temporary_query_put_game_address_at_top(address, online, saved):
    async with query_reply(address=address, online=online, saved=saved) as result:
        client, query, _, server = result
        client.send_group.assert_awaited_once()
        query.query.assert_awaited_once()
        payload = client.send_group.await_args.args[3]
        assert payload["msg_type"] == 2
        lines = body(payload).splitlines()
        assert lines[2] == f"> 地址: {address}"
        assert lines.count(f"> 地址: {address}") == 1
        if online:
            assert lines[3] == "> 地图: 死亡中心 · 旅馆"
        else:
            assert any("无法连接到服务器" in line for line in lines[3:])
            assert not any("地图:" in line or "在线玩家:" in line for line in lines)
        rows = payload["keyboard"]["content"]["rows"]
        game = rows[0]["buttons"][0]
        assert game["render_data"]["label"] == "🎮 进入服务器"
        assert game["action"]["type"] == 0
        assert game["action"]["data"] == f"{CONNECT_BASE}/{address}"
        if saved:
            voice = rows[0]["buttons"][1]
            assert voice["render_data"]["label"] == "🎧 进入语音"
            assert voice["action"]["data"] == VOICE_URL
        assert [button["render_data"]["label"] for button in rows[-1]["buttons"]] == [
            "🌐 综合查询",
            "🔍 再次查询",
        ]
        assert all(
            value not in str(payload)
            for value in (server.panel_url, server.panel_token, server.rcon_password)
        )
        assert CONNECT_BASE not in body(payload) and VOICE_URL not in body(payload)


@pytest.mark.parametrize("online", [True, False])
@pytest.mark.parametrize("rejection_codes", [(304057,), (304057, 304036)])
async def test_saved_query_keeps_top_address_after_button_or_markdown_rejection(
    online, rejection_codes
):
    async with query_reply(online=online, rejection_codes=rejection_codes) as result:
        client, query, _, server = result
        query.query.assert_awaited_once()
        calls = client.send_group.await_args_list
        assert [call.args[2] for call in calls] == list(range(1, len(rejection_codes) + 2))
        payload = calls[-1].args[3]
        assert "keyboard" not in payload
        lines = body(payload).splitlines()
        address_line = f"地址: {server.address}"
        if len(rejection_codes) == 1:
            assert payload["msg_type"] == 2
            assert lines[2] == f"> {address_line}"
        else:
            assert payload["msg_type"] == 0
            assert lines[3] == address_line
        assert "被平台拒绝" in body(payload)
        assert f"connect {server.address}" in body(payload)


@pytest.mark.parametrize("online", [True, False])
@pytest.mark.parametrize("buttons,base_url", [(False, CONNECT_BASE), (True, "")])
async def test_saved_query_keeps_address_without_link_buttons(online, buttons, base_url):
    async with query_reply(online=online, buttons=buttons, base_url=base_url) as result:
        client, _, _, server = result
        payload = client.send_group.await_args.args[3]
        assert body(payload).splitlines()[2] == f"> 地址: {server.address}"
        if buttons:
            game = payload["keyboard"]["content"]["rows"][0]["buttons"][0]
            assert game["render_data"]["label"] == "🎮 连接指令"
            assert game["action"]["type"] == 2
            assert game["action"]["data"] == f"connect {server.address}"
            assert game["action"]["enter"] is False
        else:
            assert "keyboard" not in payload


@pytest.mark.parametrize("online", [True, False])
async def test_saved_image_and_offline_plain_reply_keep_top_address(online):
    async with query_reply(online=online, reply_format="image") as result:
        client, _, renderer, server = result
        if online:
            client.upload_png.assert_awaited_once()
            card = renderer.render.call_args.args[0]
            assert card.content.splitlines()[1] == f"地址: {server.address}"
            assert client.send_group.await_args_list[0].args[3]["msg_type"] == 7
        else:
            client.upload_png.assert_not_awaited()
            payload = client.send_group.await_args.args[3]
            assert payload["msg_type"] == 0
            assert body(payload).splitlines()[0] == f"地址: {server.address}"
            assert "无法连接到服务器 合作服" in body(payload)


@pytest.mark.parametrize("online", [True, False])
def test_status_view_without_game_address_does_not_invent_one(online):
    status = replace(sample_statuses()[0 if online else 1], address="")
    assert "地址:" not in server_card(status).content
