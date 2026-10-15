import asyncio
from unittest.mock import AsyncMock

from l4d2_qq_bot.app import BotApplication
from l4d2_qq_bot.config import Settings
from l4d2_qq_bot.demo import sample_config, sample_event
from l4d2_qq_bot.qq.discovery import DiscoveryRecorder


def full_group_event(content, message_id="source-message"):
    event = sample_event()
    event["t"] = "GROUP_MESSAGE_CREATE"
    event["d"].update(content=content, id=message_id)
    return event


def test_unmatched_chatter_never_blocks_command_queue(tmp_path):
    client = AsyncMock()
    app = BotApplication(
        client,
        sample_config(),
        Settings(_env_file=None, event_queue_size=1),
        None,
        DiscoveryRecorder(tmp_path / "discovery.json"),
    )
    for index in range(100):
        for content in ("今晚一起玩吧", "查询不存在", "语音不存在"):
            assert app.offer_event(full_group_event(content, f"chat-{index}-{content}"))
    assert app.queue.empty()
    assert app.offer_event(full_group_event("查询1服", "command-1"))
    assert app.queue.full()
    assert app.offer_event(full_group_event("收到", "chat-after-command"))
    assert not app.offer_event(full_group_event("查询1服", "command-2"))
    client.send_group.assert_not_called()


def test_full_messages_keep_group_and_bot_isolation(tmp_path):
    client = AsyncMock()
    app = BotApplication(
        client,
        sample_config(),
        Settings(_env_file=None),
        None,
        DiscoveryRecorder(tmp_path / "discovery.json"),
    )
    other_group = full_group_event("帮助", "other-group")
    other_group["d"]["group_openid"] = "OTHER_GROUP"
    bot_message = full_group_event("帮助", "bot-message")
    bot_message["d"]["author"]["bot"] = True
    channel_message = full_group_event("帮助", "channel-message")
    channel_message["t"] = "MESSAGE_CREATE"
    for event in (other_group, bot_message, channel_message):
        assert app.offer_event(event)
    assert app.queue.empty()
    client.send_group.assert_not_called()


async def test_invalid_command_without_mention_gets_passive_usage_help(tmp_path):
    client = AsyncMock()
    app = BotApplication(
        client,
        sample_config(),
        Settings(_env_file=None),
        None,
        DiscoveryRecorder(tmp_path / "discovery.json"),
    )
    app.start()
    try:
        assert app.offer_event(full_group_event("查询", "invalid-query"))
        await asyncio.wait_for(app.queue.join(), 1)
        client.send_group.assert_awaited_once()
        call = client.send_group.await_args
        assert call.args[:3] == ("DEMO_GROUP_OPENID", "invalid-query", 1)
        assert call.args[3]["msg_type"] == 0
        assert "请输入服务器名称" in call.args[3]["content"]
    finally:
        await app.close()
