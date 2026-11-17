import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from l4d2_qq_bot.app import BotApplication, route_event
from l4d2_qq_bot.commands import parse_command
from l4d2_qq_bot.config import AppConfig, GroupConfig, ServerConfig, Settings
from l4d2_qq_bot.demo import sample_event
from l4d2_qq_bot.qq.protocol import GroupMessage
from l4d2_qq_bot.services.management import ManagementService


@pytest.fixture
def config():
    return AppConfig(
        groups=[
            GroupConfig(
                group_openid="DEMO_GROUP_OPENID",
                admin_openids=["admin", "other-admin"],
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
                servers=[ServerConfig(name="Other Server", aliases=["2服"], address="127.0.0.2")],
            ),
        ]
    )


@pytest.fixture
async def restart_case(config):
    client, rcon = AsyncMock(), AsyncMock()
    rcon.execute.return_value = "服务器响应: ok"
    service = ManagementService(AsyncMock(), rcon)
    app = BotApplication(
        client,
        config,
        Settings(_env_file=None, event_queue_size=1),
        None,
        Mock(record=Mock(return_value=False)),
        query_service=AsyncMock(),
        management=service,
    )
    try:
        yield app, service, client
    finally:
        await app.close()


def restart_event(content, member="admin", event_type="GROUP_MESSAGE_CREATE", message_id="one"):
    event = sample_event()
    event["t"] = event_type
    event["d"].update(content=content, id=message_id)
    event["d"]["author"]["member_openid"] = member
    return event


@pytest.mark.parametrize("member", ["visitor", "admin"])
@pytest.mark.parametrize("event_type", ["GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"])
@pytest.mark.parametrize(
    "content",
    [
        "重启不存在",
        "重启",
        "重启 \t\u3000",
        "重启2服",
        "重启1服plus",
        "重启1服 status",
        "<@!bot> /重启 My Server Two extra",
    ],
)
async def test_unmatched_restarts_are_silent_at_all_entry_points(
    config, restart_case, member, event_type, content
):
    app, service, client = restart_case
    event = restart_event(content, member, event_type)
    message = GroupMessage.from_payload(event)
    reply = AsyncMock()
    assert route_event(event, config) is None
    assert app.offer_event(event)
    assert app.queue.empty()
    await app.handle_message(message, reply)
    await service.start(parse_command(content), config.groups[0], message, reply)
    reply.text.assert_not_awaited()
    client.send_group.assert_not_awaited()
    service.rcon.execute.assert_not_awaited()
    assert not service.has_session(message)


@pytest.mark.parametrize("member", ["visitor", "admin"])
@pytest.mark.parametrize("event_type", ["GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"])
@pytest.mark.parametrize(
    ("content", "server_index"),
    [
        ("重启1服", 0),
        ("<@!bot> /重启 cOoP", 0),
        ("重启 my SERVER two", 1),
        ("重启 1\u3000 1服", 1),
    ],
)
async def test_matched_restarts_require_permission_and_explicit_confirmation(
    config, restart_case, member, event_type, content, server_index
):
    app, service, client = restart_case
    event = restart_event(content, member, event_type)
    message = GroupMessage.from_payload(event)
    server = config.groups[0].servers[server_index]
    routed = route_event(event, config)
    assert routed is not None and routed.server is server
    assert app.offer_event(event)
    assert app.queue.qsize() == 1
    app.start()
    await asyncio.wait_for(app.queue.join(), 2)
    client.send_group.assert_awaited_once()
    payload = client.send_group.await_args.args[3]
    assert payload["msg_type"] == 0
    service.rcon.execute.assert_not_awaited()
    if member == "visitor":
        assert payload["content"] == "权限不足：您不在管理员列表中。"
        assert not service.has_session(message)
    else:
        assert payload["content"].startswith(f"确认重启 {server.name}？")
        assert service.has_session(message)
        await app.handle_message(replace(message, content="确认", message_id="two"), AsyncMock())
        service.rcon.execute.assert_awaited_once_with(
            server.address, server.rcon_password, "_restart"
        )
        assert not service.has_session(message)


@pytest.mark.parametrize("member", ["visitor", "admin"])
async def test_matched_restart_keeps_permission_and_missing_password_checks(
    config, restart_case, member
):
    app, service, client = restart_case
    config.groups[0].servers[0].rcon_password = ""
    event = restart_event("重启1服", member)
    assert app.offer_event(event)
    app.start()
    await asyncio.wait_for(app.queue.join(), 2)
    client.send_group.assert_awaited_once()
    payload = client.send_group.await_args.args[3]
    assert payload["msg_type"] == 0
    assert payload["content"] == (
        "权限不足：您不在管理员列表中。"
        if member == "visitor"
        else "服务器 My Server 未配置 RCON 密码，无法执行重启。"
    )
    service.rcon.execute.assert_not_awaited()
    assert not service.has_session(GroupMessage.from_payload(event))


def test_unmatched_restarts_leave_room_for_a_valid_command(config):
    app = BotApplication(
        AsyncMock(),
        config,
        Settings(_env_file=None, event_queue_size=1),
        None,
        Mock(record=Mock(return_value=False)),
        query_service=AsyncMock(),
    )
    for index in range(20):
        assert app.offer_event(restart_event("重启不存在", message_id=f"chat-{index}"))
    assert app.queue.empty()
    assert app.offer_event(restart_event("重启1服", message_id="valid"))
    assert app.queue.full()


@pytest.mark.parametrize("member", ["visitor", "admin"])
@pytest.mark.parametrize("event_type", ["GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"])
async def test_unconfigured_groups_do_not_route_or_reply(config, restart_case, member, event_type):
    app, service, client = restart_case
    event = restart_event("重启1服", member, event_type)
    event["d"]["group_openid"] = "UNCONFIGURED_GROUP"
    message = GroupMessage.from_payload(event)
    reply = AsyncMock()
    assert route_event(event, config) is None
    assert app.offer_event(event)
    assert app.queue.empty()
    await app.handle_message(message, reply)
    reply.text.assert_not_awaited()
    client.send_group.assert_not_awaited()
    service.rcon.execute.assert_not_awaited()
    assert not service.has_session(message)


@pytest.mark.parametrize("content", ["重启不存在", "重启"])
@pytest.mark.parametrize("event_type", ["GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"])
async def test_unmatched_restart_cancels_the_existing_confirmation(
    restart_case, content, event_type
):
    app, service, client = restart_case
    message = GroupMessage("one", "DEMO_GROUP_OPENID", "admin", "重启1服")
    await app.handle_message(message, AsyncMock())
    assert service.has_session(message)
    event = restart_event(content, event_type=event_type, message_id="cancel-restart")
    assert app.offer_event(event)
    assert app.queue.qsize() == 1
    app.start()
    await asyncio.wait_for(app.queue.join(), 2)
    assert not service.has_session(message)
    reply = AsyncMock()
    await app.handle_message(replace(message, content="确认", message_id="three"), reply)
    reply.text.assert_not_awaited()
    client.send_group.assert_not_awaited()
    service.rcon.execute.assert_not_awaited()


@pytest.mark.parametrize("member", ["visitor", "admin"])
async def test_removed_restart_target_is_rechecked_before_permission_in_worker(
    restart_case, member
):
    app, service, client = restart_case
    event = restart_event("重启1服", member)
    assert app.offer_event(event)
    app.reconfigure(
        AppConfig(groups=[GroupConfig(group_openid="DEMO_GROUP_OPENID")]),
        maps=AsyncMock(),
        voice=AsyncMock(),
    )
    app.start()
    await asyncio.wait_for(app.queue.join(), 2)
    client.send_group.assert_not_awaited()
    service.rcon.execute.assert_not_awaited()
    assert not service.has_session(GroupMessage.from_payload(event))


@pytest.mark.parametrize("change", ["remove", "name", "address", "password", "permission"])
async def test_restart_confirmation_rechecks_current_permission_and_configuration(
    config, restart_case, change
):
    _, service, _ = restart_case
    group = config.groups[0]
    message = GroupMessage("one", group.group_openid, "admin", "重启1服")
    await service.start(parse_command(message.content), group, message, AsyncMock())
    assert service.has_session(message)
    changed = group.model_copy(deep=True)
    if change == "remove":
        changed.servers.clear()
    elif change == "permission":
        changed.admin_openids.remove("admin")
    else:
        field, value = {
            "name": ("name", "Renamed Server"),
            "address": ("address", "127.0.0.1:27017"),
            "password": ("rcon_password", ""),
        }[change]
        setattr(changed.servers[0], field, value)
    reply = AsyncMock()
    await service.respond(changed, replace(message, content="确认", message_id="two"), reply)
    reply.text.assert_awaited_once_with(
        "权限不足：您不在管理员列表中。" if change == "permission" else "配置已变更，已取消"
    )
    assert not service.has_session(message)
    service.rcon.execute.assert_not_awaited()


async def test_restart_confirmation_uses_the_current_nonempty_password(config, restart_case):
    _, service, _ = restart_case
    group = config.groups[0]
    message = GroupMessage("one", group.group_openid, "admin", "重启1服")
    await service.start(parse_command(message.content), group, message, AsyncMock())
    changed = group.model_copy(deep=True)
    changed.servers[0].rcon_password = "fixture-updated-rcon"
    await service.respond(changed, replace(message, content="确认", message_id="two"), AsyncMock())
    service.rcon.execute.assert_awaited_once_with(
        changed.servers[0].address, "fixture-updated-rcon", "_restart"
    )


async def test_restart_confirmation_is_scoped_and_cannot_execute_twice(config, restart_case):
    _, service, _ = restart_case
    group = config.groups[0]
    message = GroupMessage("one", group.group_openid, "admin", "重启1服")
    await service.start(parse_command(message.content), group, message, AsyncMock())
    confirmation = replace(message, content="确认", message_id="two")
    reply = AsyncMock()
    await service.respond(group, replace(confirmation, member_openid="other-admin"), reply)
    await service.respond(group, replace(confirmation, group_openid="OTHER_GROUP"), reply)
    assert service.has_session(message)
    reply.text.assert_not_awaited()
    service.rcon.execute.assert_not_awaited()
    await service.respond(group, confirmation, reply)
    await service.respond(group, confirmation, reply)
    server = group.servers[0]
    service.rcon.execute.assert_awaited_once_with(server.address, server.rcon_password, "_restart")
    assert not service.has_session(message)
