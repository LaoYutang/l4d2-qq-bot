import asyncio
from unittest.mock import AsyncMock

import pytest

from l4d2_qq_bot.app import BotApplication
from l4d2_qq_bot.cards.markdown import markdown_card
from l4d2_qq_bot.cards.views import server_card
from l4d2_qq_bot.config import ServerConfig, Settings
from l4d2_qq_bot.demo import sample_config, sample_event, sample_statuses
from l4d2_qq_bot.domain import Card, CardSection
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
    assert "\\[恶意\\]\\(" in content
    assert "已省略" in content
    assert "connect 203.0.113.1:27015" in content and content.endswith("模拟数据")


def test_native_preserves_offline_and_unknown_player_states():
    statuses = sample_statuses()
    assert "离线" in markdown_card(server_card(statuses[1], demo=True))
    content = markdown_card(server_card(statuses[2], demo=True))
    assert "获取失败或未开放" in content and "4 / 16" in content


async def test_simultaneous_queries_keep_button_targets_in_the_same_single_message():
    client = AsyncMock()
    config = sample_config()
    config.connect_base_url = "https://steam-connect.example.test"
    config.groups[0].servers.append(
        ServerConfig(name="对抗服", address="203.0.113.20:27016", aliases=["2服"])
    )
    renderer = AsyncMock()
    app = BotApplication(
        client, config, Settings(_env_file=None), renderer, AsyncMock(), demo_data=True
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
        assert f"connect {address}" in payload["markdown"]["content"]
        button = payload["keyboard"]["content"]["rows"][0]["buttons"][0]
        assert button["action"]["data"] == f"https://steam-connect.example.test/{address}"
    client.upload_png.assert_not_awaited()
    renderer.render.assert_not_called()


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
