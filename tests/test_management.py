import asyncio
import struct
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from l4d2_qq_bot.app import BotApplication
from l4d2_qq_bot.commands import parse_command
from l4d2_qq_bot.config import AppConfig, GroupConfig, ServerConfig, Settings
from l4d2_qq_bot.integrations.http import ExternalHTTP
from l4d2_qq_bot.integrations.rcon import RCONClient
from l4d2_qq_bot.qq.protocol import GroupMessage
from l4d2_qq_bot.services.management import ManagementService


def group():
    return GroupConfig(
        group_openid="group",
        admin_openids=["admin", "other-admin"],
        servers=[
            ServerConfig(
                name="My Server", aliases=["1服"], address="127.0.0.1", rcon_password="fixture-rcon"
            )
        ],
    )


@pytest.mark.parametrize("content", ["帮助", "查询不存在", "查询", "取消"])
async def test_other_input_cancels_confirmation_in_the_message_router(content):
    service = ManagementService(AsyncMock(), AsyncMock())
    config = AppConfig(groups=[group()])
    app = BotApplication(
        AsyncMock(), config, Settings(_env_file=None), None, AsyncMock(), management=service
    )
    message = GroupMessage("one", "group", "admin", "重启1服")
    reply = AsyncMock()
    try:
        await app.handle_message(message, reply)
        assert service.has_session(message)
        await app.handle_message(replace(message, content=content, message_id="two"), reply)
        assert not service.has_session(message)
        assert reply.text.await_args.args[0] == "已取消"
        await app.handle_message(replace(message, content="确认", message_id="three"), reply)
        service.rcon.execute.assert_not_awaited()
    finally:
        await app.close()


@pytest.mark.parametrize("command", ["status", "_restart"])
async def test_rcon_executes_immediately_with_exact_target_and_admin_permission(command):
    rcon = AsyncMock()
    rcon.execute.return_value = "服务器响应: fixture-rcon"
    service = ManagementService(AsyncMock(), rcon)
    reply = AsyncMock()
    message = GroupMessage("one", "group", "visitor", "设置1服 status")
    try:
        await service.start(parse_command(message.content), group(), message, reply)
        assert reply.text.await_args.args[0] == "权限不足：您不在管理员列表中。"
        assert not service.has_session(message)
        message = replace(message, member_openid="admin")
        await service.start(parse_command("设置1服status"), group(), message, reply)
        assert not service.has_session(message)
        rcon.execute.assert_not_awaited()
        await service.start(parse_command(f"设置 MyServer {command}"), group(), message, reply)
        assert not service.has_session(message)
        rcon.execute.assert_awaited_once_with("127.0.0.1:27015", "fixture-rcon", command)
        assert reply.text.await_args.args[0] == "服务器响应: ***"
        confirmation = replace(message, message_id="two", content="确认")
        await service.respond(group(), replace(confirmation, member_openid="other-admin"), reply)
        await service.respond(group(), replace(confirmation, group_openid="other-group"), reply)
        await service.respond(group(), confirmation, reply)
        await service.respond(group(), confirmation, reply)
        rcon.execute.assert_awaited_once_with("127.0.0.1:27015", "fixture-rcon", command)
    finally:
        await service.close()


@pytest.mark.parametrize(
    ("password", "timeout_seconds", "expected"),
    [
        ("", 90, "服务器 My Server 未配置 RCON 密码，无法执行指令。"),
        ("fixture-rcon", 15, "超时取消"),
    ],
)
async def test_rcon_does_not_execute_without_password_or_time(
    password, timeout_seconds, expected
):
    service = ManagementService(AsyncMock(), AsyncMock())
    config = group()
    config.servers[0].rcon_password = password
    message = GroupMessage("one", "group", "admin", "设置1服 status")
    reply = AsyncMock()
    try:
        await service.start(
            parse_command(message.content),
            config,
            message,
            reply,
            timeout_seconds=timeout_seconds,
        )
        service.rcon.execute.assert_not_awaited()
        assert not service.has_session(message)
        reply.text.assert_awaited_once_with(expected)
    finally:
        await service.close()


async def test_configuration_change_and_cancel_clear_pending_operations():
    service = ManagementService(AsyncMock(), AsyncMock())
    service.rcon.execute.return_value = "指令已发送。服务器正在重启..."
    reply = AsyncMock()
    message = GroupMessage("one", "group", "admin", "重启1服")
    try:
        await service.start(parse_command(message.content), group(), message, reply)
        service.cancel_all()
        await service.respond(group(), replace(message, content="确认"), reply)
        service.rcon.execute.assert_not_awaited()
        await service.start(parse_command(message.content), group(), message, reply)
        await service.respond(group(), replace(message, content="取消"), reply)
        assert reply.text.await_args.args[0] == "已取消"
        service.rcon.execute.assert_not_awaited()
        await service.start(parse_command(message.content), group(), message, reply)
        assert service.has_session(message)
        service.rcon.execute.assert_not_awaited()
        confirmation = replace(message, content="确认")
        await service.respond(group(), replace(confirmation, member_openid="other-admin"), reply)
        await service.respond(group(), replace(confirmation, group_openid="other-group"), reply)
        assert service.has_session(message)
        service.rcon.execute.assert_not_awaited()
        await service.respond(group(), replace(message, content="确认"), reply)
        await service.respond(group(), replace(message, content="确认"), reply)
        service.rcon.execute.assert_awaited_once_with("127.0.0.1:27015", "fixture-rcon", "_restart")
    finally:
        await service.close()


@pytest.mark.parametrize("cancel_during_add", [False, True])
async def test_batch_reports_unsubmitted_files_when_budget_or_configuration_changes(
    cancel_during_add,
):
    http = AsyncMock()
    http.json.return_value = {
        "items": [
            {"filename": name, "supported": True, "file_url": f"https://files.example/{name}"}
            for name in ("a.vpk", "b.vpk")
        ]
    }
    service = ManagementService(http)
    config = group()
    config.servers[0].panel_url = "https://panel.example"
    config.servers[0].panel_token = "fixture-panel"
    message = GroupMessage("one", "group", "admin", "https://qfile.qq.com/q/fixture")
    reply = AsyncMock()

    async def add(*args, **kwargs):
        service.cancel_all()
        return b"ok"

    http.read.side_effect = add
    try:
        await service.start(parse_command(message.content), config, message, reply)
        await service.respond(config, replace(message, content="1"), reply)
        await service.respond(
            config,
            replace(message, content="全部"),
            reply,
            timeout_seconds=90 if cancel_during_add else 0,
        )
        if cancel_during_add:
            assert http.read.await_count == 1
            assert "已提交 1 个下载任务" in reply.text.await_args.args[0]
            assert "配置已变更，已取消" in reply.text.await_args.args[0]
        else:
            http.read.assert_not_awaited()
            assert "处理超时，未提交" in reply.text.await_args.args[0]
    finally:
        await service.close()


async def test_source_rcon_packets_are_authenticated_and_complete():
    commands = []
    restarted = asyncio.Event()

    async def endpoint(reader, writer):
        async def receive():
            size = struct.unpack("<i", await reader.readexactly(4))[0]
            raw = await reader.readexactly(size)
            return *struct.unpack("<ii", raw[:8]), raw[8:-2]

        async def send(identifier, kind, body):
            raw = struct.pack("<ii", identifier, kind) + body + b"\0\0"
            writer.write(struct.pack("<i", len(raw)) + raw)
            await writer.drain()

        try:
            identifier, kind, password = await receive()
            assert kind == 3
            if password != b"fixture":
                await send(-1, 2, b"")
                return
            await send(identifier, 0, b"")
            await send(identifier, 2, b"")
            command_id, kind, command = await receive()
            commands.append(command)
            if command == b"_restart":
                restarted.set()
                return
            check_id, kind, _ = await receive()
            assert kind == 0
            await send(command_id, 0, b"first ")
            await send(command_id, 0, b"second")
            await send(check_id, 0, b"")
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(endpoint, "127.0.0.1", 0)
    address = f"127.0.0.1:{server.sockets[0].getsockname()[1]}"
    try:
        assert await RCONClient().execute(address, "wrong", "status") == (
            "RCON 执行出错: RCON 认证失败：密码错误"
        )
        assert commands == []
        assert await RCONClient().execute(address, "fixture", "status") == (
            "服务器响应: first second"
        )
        assert await RCONClient().execute(address, "fixture", "_restart") == (
            "指令已发送。服务器正在重启..."
        )
        await asyncio.wait_for(restarted.wait(), 1)
        assert commands == [b"status", b"_restart"]
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.parametrize("fail_add", [False, True])
async def test_flash_transfer_selection_filters_files_and_never_retries(
    fail_add, local_server, http_session
):
    parsed, added = [], []

    async def parse(request):
        assert request.headers["Authorization"] == "Bearer fixture-panel-token"
        parsed.append(await request.json())
        return web.json_response(
            {
                "items": [
                    {
                        "filename": "folder/a.vpk",
                        "file_url": "https://files.example/a",
                        "supported": True,
                        "file_size": 1024,
                    },
                    {"filename": "b.ZIP", "file_url": "https://files.example/b", "supported": True},
                    {
                        "filename": "bad.exe",
                        "file_url": "https://files.example/bad",
                        "supported": True,
                    },
                    {"filename": "bad.rar", "file_url": "file:///etc/passwd", "supported": True},
                ]
            }
        )

    async def add(request):
        assert request.headers["Authorization"] == "Bearer fixture-panel-token"
        form = await request.post()
        added.append(dict(form))
        return web.Response(status=500 if fail_add else 200, text="fixture-panel-token")

    panel = web.Application()
    panel.router.add_post("/download/link/parse", parse)
    panel.router.add_post("/download/add", add)
    base = await local_server(panel)
    config = group()
    config.servers[0].panel_url = base
    config.servers[0].panel_token = "fixture-panel-token"
    service = ManagementService(ExternalHTTP(http_session))
    reply = AsyncMock()
    message = GroupMessage("one", "group", "admin", "https://qfile.qq.com/q/fixture")
    try:
        await service.start(parse_command(message.content), config, message, reply)
        assert not parsed and not added
        await service.respond(config, replace(message, content="1", message_id="two"), reply)
        assert parsed == [{"url": "https://qfile.qq.com/q/fixture"}]
        prompt = reply.text.await_args.args[0]
        assert "2 个可下载文件" in prompt and "bad.exe" not in prompt
        assert not added
        await service.respond(config, replace(message, content="全部", message_id="three"), reply)
        assert [item["filename"] for item in added] == ["a.vpk", "b.ZIP"]
        assert reply.text.await_args.args[0].startswith(
            "提交失败 2 个" if fail_add else "已提交 2 个下载任务"
        )
        assert "fixture-panel-token" not in reply.text.await_args.args[0]
        await service.respond(config, replace(message, content="全部", message_id="three"), reply)
        assert len(added) == 2
    finally:
        await service.close()
