import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest

from l4d2_qq_bot.app import BotApplication
from l4d2_qq_bot.cards.markdown import markdown_card, markdown_text
from l4d2_qq_bot.cards.views import server_card, voice_card
from l4d2_qq_bot.config import ServerConfig, Settings
from l4d2_qq_bot.demo import sample_config, sample_event, sample_statuses
from l4d2_qq_bot.domain import Card, CardSection, Player, VoiceStatus
from l4d2_qq_bot.qq.client import QQAPIError, QQTransportError
from l4d2_qq_bot.qq.protocol import GroupMessage
from l4d2_qq_bot.qq.replies import ReplySession, connection_keyboard


def test_native_content_is_bounded_and_cannot_inject_markup():
    card = Card(
        title="[恶意](https://example.test) <qqbot-at-everyone />\n标题",
        sections=(CardSection("玩家", tuple("名字" * 100 for _ in range(30))),),
        footer="模拟数据",
    )
    content = markdown_card(card, connection_command="connect 203.0.113.1:27015")
    assert len(content.encode("utf-8")) <= 3200
    assert "<qqbot-at-everyone" not in content
    assert "&#91;恶意&#93;(" in content
    assert "已省略" in content
    assert "connect 203.0.113.1:27015" in content and content.endswith("模拟数据")


def test_native_preserves_offline_and_unknown_player_states():
    statuses = sample_statuses()
    assert "离线" in markdown_card(server_card(statuses[1], demo=True))
    content = markdown_card(server_card(statuses[2], demo=True))
    assert "获取失败或未开放" in content and "人数: 4/16" in content


def test_native_player_list_is_compact_and_escapes_player_markup():
    status = replace(
        sample_statuses()[0],
        players=(
            Player("地图: [玩家]*", duration_seconds=90),
            Player("- <qqbot-at-everyone />", duration_seconds=7200),
        ),
        player_count=2,
    )
    content = markdown_card(
        server_card(status, safe_mode=False, connection_url="https://connect.example.test/1")
    )
    assert (
        "## 在线玩家:\n\n"
        "- 地图: &#91;玩家&#93;\\* (1:30)\n"
        "- \\- &lt;qqbot-at-everyone /&gt; (2:00:00)"
    ) in content
    assert "<qqbot-at-everyone" not in content
    assert "\n\n**点击直连: https://connect.example.test/1**" in content


@pytest.mark.parametrize("players", [(), None])
def test_native_player_list_does_not_bullet_empty_or_unknown_states(players):
    status = replace(sample_statuses()[0], players=players)
    content = markdown_card(server_card(status))
    assert not any(line.startswith("- ") for line in content.splitlines())


def test_native_voice_members_are_compact_list_items_with_literal_names():
    card = voice_card(
        VoiceStatus(
            "1服",
            True,
            count=5,
            names=("甲", "地图: [成员]*", "- <qqbot-at-everyone />", "2. 成员", " \x00\u202e "),
        )
    )
    assert markdown_card(card) == (
        "# 1服 语音频道\n\n**在线人数: 5**\n\n"
        "- 甲\n"
        "- 地图: &#91;成员&#93;\\*\n"
        "- \\- &lt;qqbot-at-everyone /&gt;\n"
        "- 2. 成员\n"
        "- 匿名成员"
    )
    assert card.content == (
        "1服 语音频道\n在线人数: 5\n"
        "- 甲\n- 地图: [成员]*\n- - <qqbot-at-everyone />\n- 2. 成员\n- 匿名成员"
    )


@pytest.mark.parametrize(
    "status",
    [
        VoiceStatus("1服", True, count=0, names=()),
        VoiceStatus("1服", True, count=2, names=None),
        VoiceStatus("1服", True, error="语音查询失败。"),
    ],
)
def test_native_voice_states_are_not_list_items(status):
    content = markdown_card(voice_card(status))
    assert not any(line.startswith("- ") for line in content.splitlines())


def test_plain_list_markers_outside_voice_members_remain_escaped():
    content = markdown_text("任务结果\n- 地图[测试].vpk")
    assert content == "# 任务结果\n\n\\- 地图&#91;测试&#93;.vpk"


@pytest.mark.parametrize("with_voice", [False, True])
async def test_simultaneous_queries_keep_button_targets_in_the_same_single_message(with_voice):
    client = AsyncMock()
    config = sample_config()
    config.connect_base_url = "https://steam-connect.example.test"
    if with_voice:
        config.groups[0].servers[0].voice_connect_url = "https://voice.example.test/invite/1"
    config.groups[0].servers.append(
        ServerConfig(
            name="对抗服",
            address="203.0.113.20:27016",
            aliases=["2服"],
            voice_connect_url="https://voice.example.test/invite/2" if with_voice else "",
        )
    )
    renderer = AsyncMock()
    queries = AsyncMock()

    async def query_data(address, *, alias, **kwargs):
        return replace(sample_statuses()[0], alias=alias, address=address)

    queries.query.side_effect = query_data
    app = BotApplication(
        client, config, Settings(_env_file=None), renderer, AsyncMock(), query_service=queries
    )

    async def query(index):
        event = sample_event()
        event["d"]["id"] = f"query-{index}"
        event["d"]["content"] = f"查询{index}服"
        message = GroupMessage.from_payload(event)
        await app.handle_message(message, ReplySession(client, message))

    await asyncio.gather(query(1), query(2))
    assert client.send_group.await_count == 2
    by_id = {call.args[1]: call.args[3] for call in client.send_group.await_args_list}
    for index, address in [(1, "203.0.113.10:27015"), (2, "203.0.113.20:27016")]:
        payload = by_id[f"query-{index}"]
        assert payload["msg_type"] == 2
        assert "connect " not in payload["markdown"]["content"]
        assert "查询时间" not in payload["markdown"]["content"]
        assert "已脱敏" not in payload["markdown"]["content"]
        buttons = payload["keyboard"]["content"]["rows"][0]["buttons"]
        assert len(buttons) == (2 if with_voice else 1)
        button = buttons[0]
        assert button["render_data"]["label"] == "🎮 进入服务器"
        assert button["render_data"]["visited_label"] == "🎮 进入服务器"
        assert button["action"]["data"] == f"https://steam-connect.example.test/{address}"
        if with_voice:
            assert buttons[1]["render_data"]["label"] == "🎧 进入语音"
            assert buttons[1]["render_data"]["visited_label"] == "🎧 进入语音"
            assert buttons[1]["action"]["data"] == f"https://voice.example.test/invite/{index}"
            assert buttons[1]["action"]["type"] == 0
            assert buttons[0]["id"] != buttons[1]["id"]
            assert "voice.example.test" not in payload["markdown"]["content"]
    client.upload_png.assert_not_awaited()
    renderer.render.assert_not_called()
    await app.close()


@pytest.mark.parametrize(
    ("base_url", "buttons_enabled", "rejected"),
    [
        ("", True, False),
        ("https://connect.example.test", False, False),
        ("https://connect.example.test", True, True),
    ],
)
async def test_voice_link_survives_missing_game_url_and_button_fallback(
    base_url, buttons_enabled, rejected
):
    client = AsyncMock()
    if rejected:
        client.send_group.side_effect = [QQAPIError(200, 304057), None]
    config = sample_config()
    config.connect_base_url = base_url
    url = "https://voice.example.test/invite?room=fixture&channel=one#join"
    config.groups[0].servers[0].voice_connect_url = url
    query = AsyncMock()
    query.query.return_value = sample_statuses()[0]
    app = BotApplication(
        client,
        config,
        Settings(_env_file=None, connect_buttons=buttons_enabled),
        None,
        AsyncMock(),
        query_service=query,
    )
    message = GroupMessage.from_payload(sample_event())
    try:
        await app.handle_message(message, ReplySession(client, message))
        calls = client.send_group.await_args_list
        assert len(calls) == (2 if rejected else 1)
        assert all(call.args[1] == message.message_id for call in calls)
        payload = calls[-1].args[3]
        if buttons_enabled and not rejected:
            buttons = payload["keyboard"]["content"]["rows"][0]["buttons"]
            assert buttons[0]["render_data"]["label"] == "🎮 连接指令"
            assert buttons[0]["action"]["data"] == "connect 203.0.113.10:27015"
            assert buttons[0]["action"]["type"] == 2
            assert buttons[0]["action"]["enter"] is False
            assert buttons[1]["render_data"]["label"] == "🎧 进入语音"
            assert buttons[1]["action"]["data"] == url
        else:
            assert "keyboard" not in payload
            assert f"进入语音: {url}" in payload["markdown"]["content"]
            if rejected:
                assert "按钮被平台拒绝" in payload["markdown"]["content"]
                assert "connect 203.0.113.10:27015" in payload["markdown"]["content"]
        query.query.assert_awaited_once()
    finally:
        await app.close()


def test_server_title_preserves_parentheses_without_qq_math_delimiters():
    status = replace(sample_statuses()[0], server_name="真实服务器(中文说明)")
    card = server_card(status)
    content = markdown_card(card)
    assert card.title == "真实服务器(中文说明)"
    assert content.startswith("# 真实服务器(中文说明)")
    assert "## 在线玩家:" in content
    assert "> 人数: 3/8" in content
    assert r"\(" not in content and r"\)" not in content
    assert "已脱敏" not in content and "查询时间" not in content
    assert "connect " not in content


async def test_missing_url_uses_explicit_command_button():
    keyboard = connection_keyboard("连接指令", "", command="connect 203.0.113.1:27015")
    button = keyboard["content"]["rows"][0]["buttons"][0]
    assert button["action"]["type"] == 2 and button["action"]["enter"] is False


async def test_markdown_rejection_preserves_passive_context_and_reports_the_problem():
    client = AsyncMock()
    client.send_group.side_effect = [QQAPIError(200, 304057), QQAPIError(200, 304036), None]
    message = GroupMessage.from_payload(sample_event())
    reply = ReplySession(client, message)
    await reply.markdown("# 1服", keyboard=connection_keyboard("连接1服", "https://example.test/1"))
    assert [call.args[2] for call in client.send_group.await_args_list] == [1, 2, 3]
    assert all(call.args[1] == message.message_id for call in client.send_group.await_args_list)
    assert client.send_group.await_args_list[-1].args[3]["msg_type"] == 0
    assert "原生卡片被平台拒绝" in client.send_group.await_args_list[-1].args[3]["content"]


async def test_uncertain_markdown_send_is_never_replayed():
    client = AsyncMock()
    client.send_group.side_effect = QQTransportError()
    reply = ReplySession(client, GroupMessage.from_payload(sample_event()))
    with pytest.raises(QQTransportError):
        await reply.markdown(
            "# 1服", keyboard=connection_keyboard("连接1服", "https://example.test/1")
        )
    assert client.send_group.await_count == 1
