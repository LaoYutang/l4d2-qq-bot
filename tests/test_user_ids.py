import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from l4d2_qq_bot.app import BotApplication, route_event
from l4d2_qq_bot.commands import CommandKind, parse_command
from l4d2_qq_bot.config import AppConfig, Settings
from l4d2_qq_bot.demo import sample_config, sample_event
from l4d2_qq_bot.qq.protocol import GroupMessage


@pytest.mark.parametrize(
    "content, argument",
    [
        ("获取用户ID", ""),
        ("获取用户id", ""),
        ("获取我的ID", ""),
        ("我的id", ""),
        ("获取用户ID <@TARGET_OPENID>", "<@TARGET_OPENID>"),
        ("获取用户id<@!TARGET_OPENID>", "<@!TARGET_OPENID>"),
        ("获取用户ID@成员", "@成员"),
        (" /获取用户Id @成员 ", "@成员"),
        ("<@!BOT_OPENID> /获取用户ID @成员", "@成员"),
        ('获取用户ID<qqbot-at-user id="TARGET_OPENID" />', '<qqbot-at-user id="TARGET_OPENID" />'),
    ],
)
def test_user_id_command_preserves_target(content, argument):
    command = parse_command(content)
    assert command.kind == CommandKind.USER_ID
    assert command.argument == argument


@pytest.mark.parametrize("event_type", ["GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"])
@pytest.mark.parametrize(
    "content, mentions, expected",
    [
        ("获取用户ID", None, "DEMO_MEMBER_OPENID"),
        (
            "<@!BOT_OPENID> /获取用户ID",
            [{"id": "BOT_OPENID", "is_you": True}],
            "DEMO_MEMBER_OPENID",
        ),
        ("获取用户ID <@TARGET_OPENID>", None, "TARGET_OPENID"),
        ("获取用户ID<@!TARGET_OPENID>", None, "TARGET_OPENID"),
        ('获取用户ID <qqbot-at-user id="TARGET_OPENID" />', None, "TARGET_OPENID"),
        ("获取用户ID <qqbot-at-user id='TARGET_OPENID'/>", None, "TARGET_OPENID"),
        (
            "<@!BOT_OPENID> /获取用户id <@TARGET_TOKEN>",
            [
                {"id": "BOT_OPENID", "is_you": True},
                {"id": "TARGET_TOKEN", "member_openid": "TARGET_OPENID", "scope": "single"},
            ],
            "TARGET_OPENID",
        ),
        (
            "获取用户ID <@TARGET_OPENID>",
            [{"id": "TARGET_TOKEN", "member_openid": "TARGET_OPENID"}],
            "TARGET_OPENID",
        ),
        (
            "获取用户ID <@TARGET_TOKEN>",
            [{"id": "TARGET_TOKEN", "user_openid": "TARGET_OPENID"}],
            "TARGET_OPENID",
        ),
        ("获取用户ID @成员", [{"id": "TARGET_OPENID", "username": "成员"}], "TARGET_OPENID"),
        (
            "获取用户ID @成员 名字",
            [{"member_openid": "TARGET_OPENID", "nickname": "成员 名字"}],
            "TARGET_OPENID",
        ),
        (
            "获取用户ID @成员",
            [{"member_openid": "TARGET_OPENID", "nickname": "成员"}] * 2,
            "TARGET_OPENID",
        ),
        ("获取用户ID <@DEMO_MEMBER_OPENID>", None, "DEMO_MEMBER_OPENID"),
    ],
)
async def test_user_id_lookup_replies_with_requested_member(
    event_type, content, mentions, expected
):
    client, query = AsyncMock(), AsyncMock()
    app = BotApplication(
        client, sample_config(), Settings(_env_file=None), None, Mock(), query_service=query
    )
    event = sample_event()
    event["t"] = event_type
    event["d"]["content"] = content
    if mentions is not None:
        event["d"]["mentions"] = mentions
    message = GroupMessage.from_payload(event)
    assert message.member_openid == "DEMO_MEMBER_OPENID"
    assert route_event(event, app.config).command.kind == CommandKind.USER_ID
    app.start()
    try:
        assert app.offer_event(event)
        await asyncio.wait_for(app.queue.join(), 1)
        client.send_group.assert_awaited_once_with(
            "DEMO_GROUP_OPENID", "DEMO_MESSAGE_ID", 1, {"msg_type": 0, "content": expected}
        )
        query.query.assert_not_awaited()
    finally:
        await app.close()


@pytest.mark.parametrize("event_type", ["GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"])
@pytest.mark.parametrize(
    "target, mentions",
    [
        ("@成员", None),
        ("123456", None),
        ("@123456", None),
        ("成员", [{"id": "TARGET_OPENID", "username": "成员"}]),
        ("@成员", [{"id": "OTHER_OPENID", "username": "另一个人"}]),
        (
            "@成员",
            [
                {"member_openid": "FIRST_OPENID", "username": "成员"},
                {"member_openid": "SECOND_OPENID", "username": "成员"},
            ],
        ),
        ("<@FIRST_OPENID> <@SECOND_OPENID>", [{"id": "FIRST_OPENID"}, {"id": "SECOND_OPENID"}]),
        ("<@FIRST_OPENID>\n<@SECOND_OPENID>", [{"id": "FIRST_OPENID"}, {"id": "SECOND_OPENID"}]),
        ("<@UNKNOWN_TOKEN>", [{"id": "TARGET_TOKEN", "member_openid": "TARGET_OPENID"}]),
        ("<@TARGET_OPENID> 多余参数", [{"id": "TARGET_OPENID"}]),
        ("<@BOT_OPENID>", [{"id": "BOT_OPENID", "is_you": True}]),
        ("<@BOT_OPENID>", [{"id": "BOT_OPENID", "bot": True}]),
        ("@所有人", [{"id": "ALL_OPENID", "username": "所有人", "scope": "all"}]),
        ("<@all>", None),
        ("<@everyone>", None),
        ("<qqbot-at-everyone />", None),
        ("<@>", None),
        ("<qqbot-at-user id=\"TARGET_OPENID' />", None),
        (
            "<@SAME_TOKEN>",
            [
                {"id": "SAME_TOKEN", "member_openid": "FIRST_OPENID"},
                {"id": "SAME_TOKEN", "member_openid": "SECOND_OPENID"},
            ],
        ),
    ],
)
async def test_unresolved_user_id_target_never_returns_sender(event_type, target, mentions):
    client = AsyncMock()
    app = BotApplication(client, sample_config(), Settings(_env_file=None), None, Mock())
    event = sample_event()
    event["t"] = event_type
    event["d"].update(content=f"获取用户ID {target}", mentions=mentions)
    app.start()
    try:
        assert app.offer_event(event)
        await asyncio.wait_for(app.queue.join(), 1)
        client.send_group.assert_awaited_once()
        group, message_id, sequence, reply = client.send_group.await_args.args
        assert (group, message_id, sequence) == ("DEMO_GROUP_OPENID", "DEMO_MESSAGE_ID", 1)
        assert reply["msg_type"] == 0
        assert "只选择一位群成员" in reply["content"]
        assert "DEMO_MEMBER_OPENID" not in reply["content"]
    finally:
        await app.close()


@pytest.mark.parametrize(
    "mentions",
    [
        None,
        {},
        "invalid",
        1,
        [],
        [None, 1, "invalid", {}],
        [{"member_openid": 123456, "username": "成员"}],
        [{"member_openid": "", "username": "成员"}],
        [{"member_openid": "INVALID\nOPENID", "username": "成员"}],
    ],
)
def test_invalid_optional_mentions_do_not_drop_message_or_invent_ids(mentions):
    event = sample_event()
    event["d"]["mentions"] = mentions
    message = GroupMessage.from_payload(event)
    assert message is not None
    assert message.member_openid == "DEMO_MEMBER_OPENID"
    assert message.mentioned_member_openid("@成员") is None


@pytest.mark.parametrize("event_type", ["GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"])
def test_user_id_lookup_remains_disabled_in_unconfigured_groups(event_type):
    client = AsyncMock()
    app = BotApplication(client, AppConfig(), Settings(_env_file=None), None, Mock())
    event = sample_event()
    event["t"] = event_type
    for content in ("获取用户ID", "获取用户ID <@TARGET_OPENID>", "获取用户ID @成员"):
        event["d"]["content"] = content
        assert route_event(event, app.config) is None
        assert app.offer_event(event)
    assert app.queue.empty()
    client.send_group.assert_not_called()


async def test_user_id_lookup_is_deduplicated_across_group_events():
    client = AsyncMock()
    app = BotApplication(client, sample_config(), Settings(_env_file=None), None, Mock())
    event = sample_event()
    event["d"]["content"] = "获取用户ID <@TARGET_OPENID>"
    app.start()
    try:
        assert app.offer_event(event)
        event["t"] = "GROUP_MESSAGE_CREATE"
        assert app.offer_event(event)
        await asyncio.wait_for(app.queue.join(), 1)
        client.send_group.assert_awaited_once_with(
            "DEMO_GROUP_OPENID",
            "DEMO_MESSAGE_ID",
            1,
            {"msg_type": 0, "content": "TARGET_OPENID"},
        )
    finally:
        await app.close()
