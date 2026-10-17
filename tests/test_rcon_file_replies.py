import asyncio
import struct
from unittest.mock import AsyncMock

import pytest

from l4d2_qq_bot.commands import parse_command
from l4d2_qq_bot.config import GroupConfig, ServerConfig
from l4d2_qq_bot.qq.client import QQAPIError, QQTransportError, UploadedFile
from l4d2_qq_bot.qq.protocol import GroupMessage
from l4d2_qq_bot.qq.replies import ReplySession
from l4d2_qq_bot.services.management import ManagementService


@pytest.fixture
async def rcon_case():
    client = AsyncMock()
    client.upload_txt.return_value = UploadedFile("opaque")
    service = ManagementService(AsyncMock(), AsyncMock())
    group = GroupConfig(
        group_openid="group",
        admin_openids=["admin"],
        servers=[ServerConfig(name="1服", address="127.0.0.1", rcon_password="fixture-rcon")],
    )
    message = GroupMessage("original", "group", "admin", "设置1服 sm_rn_info")
    yield service, group, message, ReplySession(client, message), client
    await service.close()


async def execute(case, result):
    service, group, message, reply, _ = case
    service.rcon.execute.return_value = result
    await service.start(parse_command(message.content), group, message, reply)
    service.rcon.execute.assert_awaited_once_with("127.0.0.1:27015", "fixture-rcon", "sm_rn_info")


@pytest.mark.parametrize(
    "result",
    [
        "a" * 199,
        "a" * 200,
        "中" * 200,
        "😀" * 200,
        "指令已发送。服务器无文本响应。",
        "RCON 执行出错: 服务器返回了无效的 RCON 数据",
        "操作超时：连接服务器耗时过长，请检查服务器状态或网络连接。",
        "",
        "服务器响应: " + "a" * (200 - len("服务器响应: ")),
    ],
    ids=[
        "ascii-below",
        "ascii-at",
        "chinese-at",
        "emoji-at",
        "no-text",
        "protocol-error",
        "timeout",
        "empty",
        "prefix-at",
    ],
)
async def test_short_boundary_and_empty_results_send_one_plain_message(rcon_case, result):
    await execute(rcon_case, result)
    *_, client = rcon_case
    client.upload_txt.assert_not_awaited()
    client.send_group.assert_awaited_once_with(
        "group",
        "original",
        1,
        {"msg_type": 0, "content": result or "指令已发送。服务器无文本响应。"},
    )


@pytest.mark.parametrize(
    "result",
    [
        "a" * 201,
        "中" * 201,
        "😀" * 201,
        "服务器响应: " + "a" * (201 - len("服务器响应: ")),
    ],
    ids=["ascii-over", "chinese-over", "emoji-over", "prefix-over"],
)
async def test_long_utf8_results_send_only_one_file_card(rcon_case, result):
    await execute(rcon_case, result)
    *_, client = rcon_case
    client.upload_txt.assert_awaited_once_with("group", result)
    client.send_group.assert_awaited_once_with(
        "group",
        "original",
        1,
        {"msg_type": 7, "media": {"file_info": "opaque"}},
    )


@pytest.mark.parametrize("prefix_length, expected_type", [(197, 0), (198, 7)])
async def test_redaction_happens_before_threshold_and_file_upload(
    rcon_case, prefix_length, expected_type
):
    result = "a" * prefix_length + "fixture-rcon"
    redacted = "a" * prefix_length + "***"
    await execute(rcon_case, result)
    *_, client = rcon_case
    assert client.send_group.await_args.args[3]["msg_type"] == expected_type
    if expected_type == 7:
        client.upload_txt.assert_awaited_once_with("group", redacted)
    else:
        client.upload_txt.assert_not_awaited()
        assert client.send_group.await_args.args[3]["content"] == redacted


@pytest.mark.parametrize("stage", ["upload", "send"])
@pytest.mark.parametrize(
    "failure", [QQAPIError(403, 850019), QQTransportError(), ValueError("fixture-private-body")]
)
async def test_file_failures_send_one_short_notice_without_reexecuting(
    rcon_case, stage, failure, caplog
):
    *_, client = rcon_case
    if stage == "upload":
        client.upload_txt.side_effect = failure
    else:
        client.send_group.side_effect = [failure, None]
    await execute(rcon_case, "fixture-private-body\n" * 500 + "fixture-rcon")
    client.upload_txt.assert_awaited_once()
    calls = client.send_group.await_args_list
    assert len(calls) == (1 if stage == "upload" else 2)
    notice = calls[-1].args[3]
    assert notice["msg_type"] == 0
    assert "未重新执行指令" in notice["content"]
    assert len(notice["content"]) <= 200
    assert "fixture-rcon" not in caplog.text and "fixture-private-body" not in caplog.text
    assert (
        "fixture-rcon" not in notice["content"] and "fixture-private-body" not in notice["content"]
    )
    if stage == "send":
        assert [call.args[2] for call in calls] == [1, 2]
        assert calls[0].args[3] == {"msg_type": 7, "media": {"file_info": "opaque"}}
        if isinstance(failure, QQTransportError):
            assert "状态未确认" in notice["content"]


async def test_file_failure_notice_failure_is_bounded(rcon_case):
    *_, client = rcon_case
    client.send_group.side_effect = QQTransportError()
    with pytest.raises(QQTransportError):
        await execute(rcon_case, "中文结果" * 500)
    assert rcon_case[0].rcon.execute.await_count == 1
    client.upload_txt.assert_awaited_once()
    assert client.send_group.await_count == 2


async def test_exhausted_reply_budget_does_not_upload_a_file():
    client = AsyncMock()
    reply = ReplySession(client, GroupMessage("original", "group", "admin", "fixture"))
    reply.sequence = 5
    with pytest.raises(ValueError, match="回复上限"):
        await reply.text_file("中文结果" * 500)
    client.upload_txt.assert_not_awaited()
    client.send_group.assert_not_awaited()


async def test_plain_text_replies_keep_their_existing_pagination():
    client = AsyncMock()
    reply = ReplySession(client, GroupMessage("original", "group", "member", "fixture"))
    await reply.text("a" * 7001)
    assert [call.args[3]["msg_type"] for call in client.send_group.await_args_list] == [0, 0, 0]
    assert [call.args[2] for call in client.send_group.await_args_list] == [1, 2, 3]
    client.upload_txt.assert_not_awaited()


async def test_multiple_rcon_packets_become_one_complete_redacted_file(rcon_case):
    from l4d2_qq_bot.integrations.rcon import RCONClient

    service, group, message, reply, client = rcon_case
    body = ("[Rogue] 中文属性😀\n" * 500 + "fixture-rcon\n").encode("utf-8")
    commands = []

    def packet(identifier, kind, raw=b""):
        return struct.pack("<iii", len(raw) + 10, identifier, kind) + raw + b"\0\0"

    async def endpoint(reader, writer):
        async def receive():
            size = struct.unpack("<i", await reader.readexactly(4))[0]
            raw = await reader.readexactly(size)
            return *struct.unpack("<ii", raw[:8]), raw[8:-2]

        try:
            identifier, kind, password = await receive()
            assert (identifier, kind, password) == (1, 3, b"fixture-rcon")
            auth = packet(1, 0) + packet(1, 2)
            writer.write(auth[:2])
            await writer.drain()
            await asyncio.sleep(0)
            writer.write(auth[2:])
            await writer.drain()
            command_id, kind, command = await receive()
            commands.append(command)
            assert (command_id, kind, command) == (2, 2, b"sm_rn_info")
            marker_id, kind, raw = await receive()
            assert (marker_id, kind, raw) == (3, 0, b"")
            for start in range(0, len(body), 4000):
                writer.write(packet(command_id, 0, body[start : start + 4000]))
            writer.write(packet(marker_id, 0))
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(endpoint, "127.0.0.1", 0)
    group.servers[0].address = f"127.0.0.1:{server.sockets[0].getsockname()[1]}"
    service.rcon = RCONClient()
    try:
        await service.start(parse_command(message.content), group, message, reply)
        expected = "服务器响应: " + body.decode("utf-8").replace("fixture-rcon", "***")
        client.upload_txt.assert_awaited_once_with("group", expected)
        client.send_group.assert_awaited_once_with(
            "group",
            "original",
            1,
            {"msg_type": 7, "media": {"file_info": "opaque"}},
        )
        assert commands == [b"sm_rn_info"]
    finally:
        server.close()
        await server.wait_closed()
