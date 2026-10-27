import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from l4d2_qq_bot.app import BotApplication, route_event
from l4d2_qq_bot.commands import match_rcon_target, parse_command
from l4d2_qq_bot.config import AppConfig, GroupConfig, ServerConfig, Settings
from l4d2_qq_bot.demo import sample_event
from l4d2_qq_bot.qq.protocol import GroupMessage
from l4d2_qq_bot.services.management import ManagementService


@pytest.fixture
def discovery():
    return Mock(record=Mock(return_value=False))


@pytest.fixture
def config():
    return AppConfig(
        groups=[
            GroupConfig(
                group_openid="DEMO_GROUP_OPENID",
                admin_openids=["admin"],
                servers=[
                    ServerConfig(
                        name="My Server",
                        aliases=["1服", "CoOp"],
                        address="127.0.0.1:27015",
                        rcon_password="fixture-rcon",
                    ),
                    ServerConfig(
                        name="My Server Two",
                        aliases=["11服"],
                        address="127.0.0.1:27016",
                        rcon_password="fixture-rcon-two",
                    ),
                ],
            ),
            GroupConfig(
                group_openid="OTHER_GROUP",
                servers=[ServerConfig(name="其他群服务器", aliases=["2服"], address="127.0.0.2")],
            ),
        ]
    )


@pytest.mark.parametrize(
    ("argument", "expected_name", "expected_command"),
    [
        ("1服 status", "My Server", "status"),
        ("MyServer status", "My Server", "status"),
        ("my SERVER status", "My Server", "status"),
        ("cOoP status", "My Server", "status"),
        ("1\u3000服\tstatus", "My Server", "status"),
        ("11服 status", "My Server Two", "status"),
        ("my server two status", "My Server Two", "status"),
        ("MyServerTwo status", "My Server Two", "status"),
        ("1服", "My Server", ""),
        ("My Server Two", "My Server Two", ""),
        ("My Server Two   ", "My Server Two", ""),
        ("1服 sv_cheats 0; say hello", "My Server", "sv_cheats 0; say hello"),
        ("1服status", None, ""),
        ("11服status", None, ""),
        ("1服plus status", None, ""),
        ("不存在 status", None, ""),
        ("2服 status", None, ""),
        ("画质 高", None, ""),
        ("", None, ""),
        ("   ", None, ""),
    ],
)
def test_rcon_target_uses_complete_local_names_and_longest_match(
    config, argument, expected_name, expected_command
):
    server, command = match_rcon_target(config.groups[0].servers, argument)
    assert (server.name if server else None) == expected_name
    assert command == expected_command


@pytest.mark.parametrize("member", ["visitor", "admin"])
@pytest.mark.parametrize("event_type", ["GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"])
@pytest.mark.parametrize(
    "content",
    [
        "设置画质 高",
        "设置不存在 status",
        "设置2服 status",
        "设置1服status",
        "设置1服plus status",
        "设置",
        "设置 \t\u3000",
        "<@!bot> /设置 普通聊天",
    ],
)
async def test_unmatched_settings_are_silent_at_all_entry_points(
    config, discovery, member, event_type, content
):
    client, rcon, reply = AsyncMock(), AsyncMock(), AsyncMock()
    service = ManagementService(AsyncMock(), rcon)
    app = BotApplication(
        client, config, Settings(_env_file=None), None, discovery, management=service
    )
    event = sample_event()
    event["t"] = event_type
    event["d"]["content"] = content
    event["d"]["author"]["member_openid"] = member
    message = GroupMessage.from_payload(event)
    try:
        assert route_event(event, config) is None
        assert app.offer_event(event)
        assert app.queue.empty()
        await app.handle_message(message, reply)
        await service.start(parse_command(content), config.groups[0], message, reply)
        reply.text.assert_not_awaited()
        client.send_group.assert_not_awaited()
        rcon.execute.assert_not_awaited()
        assert not service.has_session(message)
    finally:
        await app.close()


@pytest.mark.parametrize("member", ["visitor", "admin"])
@pytest.mark.parametrize("event_type", ["GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"])
@pytest.mark.parametrize(
    ("content", "server_index", "text"),
    [
        ("设置1服 status", 0, "status"),
        ("<@!bot> /设置 MY server status", 0, "status"),
        ("设置 cOoP status", 0, "status"),
        ("设置 1\u3000服\tstatus", 0, "status"),
        ("设置11服 status", 1, "status"),
        ("设置 my server two status", 1, "status"),
        ("设置1服", 0, ""),
        ("设置 1服   ", 0, ""),
        ("设置 My Server Two", 1, ""),
        ("设置1服 status\nquit", 0, "status\nquit"),
    ],
)
async def test_matched_settings_keep_permission_and_argument_checks(
    config, discovery, member, event_type, content, server_index, text
):
    client, rcon = AsyncMock(), AsyncMock()
    rcon.execute.return_value = "服务器响应: ok"
    service = ManagementService(AsyncMock(), rcon)
    app = BotApplication(
        client, config, Settings(_env_file=None), None, discovery, management=service
    )
    event = sample_event()
    event["t"] = event_type
    event["d"]["content"] = content
    event["d"]["author"]["member_openid"] = member
    server = config.groups[0].servers[server_index]
    routed = route_event(event, config)
    assert routed is not None and routed.server is server
    app.start()
    try:
        assert app.offer_event(event)
        assert app.queue.qsize() == 1
        await asyncio.wait_for(app.queue.join(), 2)
        client.send_group.assert_awaited_once()
        payload = client.send_group.await_args.args[3]
        if member == "visitor":
            expected = "权限不足：您不在管理员列表中。"
        elif not text:
            expected = "请输入要执行的指令。"
        elif "\n" in text:
            expected = "指令格式错误。"
        else:
            expected = "服务器响应: ok"
        assert payload["msg_type"] == 0 and payload["content"] == expected
        if expected == "服务器响应: ok":
            rcon.execute.assert_awaited_once_with(server.address, server.rcon_password, text)
        else:
            rcon.execute.assert_not_awaited()
        assert not service.has_session(routed.message)
    finally:
        await app.close()


def test_unmatched_settings_do_not_fill_a_bounded_queue(config, discovery):
    app = BotApplication(
        AsyncMock(), config, Settings(_env_file=None, event_queue_size=1), None, discovery
    )
    for index in range(100):
        event = sample_event()
        event["d"].update(content="设置画质 高", id=f"chat-{index}")
        assert app.offer_event(event)
    assert app.queue.empty()
    event["d"].update(content="设置1服 status", id="real-command")
    assert app.offer_event(event)
    assert app.queue.full()


async def test_removed_target_is_rechecked_before_permission_in_a_worker(config, discovery):
    client, rcon = AsyncMock(), AsyncMock()
    service = ManagementService(AsyncMock(), rcon)
    app = BotApplication(
        client, config, Settings(_env_file=None), None, discovery, management=service
    )
    event = sample_event()
    event["d"]["content"] = "设置1服 status"
    try:
        assert app.offer_event(event)
        app.reconfigure(
            AppConfig(groups=[GroupConfig(group_openid="DEMO_GROUP_OPENID")]),
            maps=AsyncMock(),
            voice=AsyncMock(),
        )
        app.start()
        await asyncio.wait_for(app.queue.join(), 2)
        client.send_group.assert_not_awaited()
        rcon.execute.assert_not_awaited()
    finally:
        await app.close()


async def test_unmatched_settings_still_cancel_a_pending_restart(config, discovery):
    client = AsyncMock()
    service = ManagementService(AsyncMock(), AsyncMock())
    app = BotApplication(
        client, config, Settings(_env_file=None), None, discovery, management=service
    )
    message = GroupMessage("one", "DEMO_GROUP_OPENID", "admin", "重启1服")
    reply = AsyncMock()
    try:
        await app.handle_message(message, reply)
        assert service.has_session(message)
        reply.reset_mock()
        event = sample_event()
        event["d"].update(content="设置画质 高", id="cancel-restart")
        event["d"]["author"]["member_openid"] = "admin"
        app.start()
        assert app.offer_event(event)
        assert app.queue.qsize() == 1
        await asyncio.wait_for(app.queue.join(), 2)
        assert not service.has_session(message)
        await app.handle_message(replace(message, content="确认", message_id="two"), reply)
        reply.text.assert_not_awaited()
        client.send_group.assert_not_awaited()
        service.rcon.execute.assert_not_awaited()
    finally:
        await app.close()
