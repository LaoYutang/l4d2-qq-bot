import asyncio
import base64
from contextlib import suppress
from unittest.mock import AsyncMock

import pytest
from aiohttp import WSMsgType, web

from l4d2_qq_bot.app import BotApplication, connect_url
from l4d2_qq_bot.cards.renderer import CardRenderer
from l4d2_qq_bot.config import ServerConfig, Settings
from l4d2_qq_bot.demo import sample_config, sample_event, sample_statuses
from l4d2_qq_bot.qq.client import QQAPIError, QQClient
from l4d2_qq_bot.qq.discovery import DiscoveryRecorder
from l4d2_qq_bot.qq.gateway import GatewayFatalError, QQGateway
from l4d2_qq_bot.qq.protocol import GroupMessage
from l4d2_qq_bot.qq.replies import ReplySession, split_text


@pytest.mark.parametrize("reply_format", ["image", "markdown"])
@pytest.mark.parametrize("event_type", ["GROUP_AT_MESSAGE_CREATE", "GROUP_MESSAGE_CREATE"])
async def test_ws_event_to_card_and_passive_reply(
    reply_format, event_type, local_server, http_session, tmp_path
):
    received = []
    auth = []
    completed = asyncio.Event()
    uploaded = []

    async def token(request):
        return web.json_response({"access_token": "fixture-token", "expires_in": 7200})

    async def gateway(request):
        return web.json_response({"url": base.replace("http:", "ws:") + "/ws"})

    async def socket(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"op": 10, "d": {"heartbeat_interval": 40}})
        auth.append(await ws.receive_json())
        await ws.send_json({"op": 0, "t": "READY", "s": 1, "d": {"session_id": "session"}})
        event = sample_event()
        event["t"] = event_type
        event["d"]["content"] = "查询1服"
        await ws.send_json(event)
        # The same source message can arrive through both group event names.
        event["t"] = (
            "GROUP_MESSAGE_CREATE"
            if event_type == "GROUP_AT_MESSAGE_CREATE"
            else "GROUP_AT_MESSAGE_CREATE"
        )
        await ws.send_json(event)
        async for message in ws:
            if message.type == WSMsgType.TEXT and message.json().get("op") == 1:
                await ws.send_json({"op": 11})
        return ws

    async def files(request):
        payload = await request.json()
        uploaded.append(base64.b64decode(payload["file_data"]))
        return web.json_response({"file_info": "image-token"})

    async def messages(request):
        received.append(await request.json())
        if len(received) == (2 if reply_format == "image" else 1):
            completed.set()
        return web.json_response({"id": "sent"})

    server = web.Application(client_max_size=5 * 1024 * 1024)
    server.router.add_post("/token", token)
    server.router.add_get("/gateway", gateway)
    server.router.add_get("/ws", socket)
    server.router.add_post("/v2/groups/DEMO_GROUP_OPENID/files", files)
    server.router.add_post("/v2/groups/DEMO_GROUP_OPENID/messages", messages)
    base = await local_server(server)
    client = QQClient(
        http_session,
        app_id="fixture",
        client_secret="fixture",
        api_base=base,
        token_url=base + "/token",
    )
    settings = Settings(
        _env_file=None,
        discovery_path=tmp_path / "discovery.json",
        reply_format=reply_format,
        connect_buttons=reply_format == "markdown",
    )
    recorder = DiscoveryRecorder(settings.discovery_path)
    config = sample_config()
    config.connect_base_url = "https://connect.example.test"
    queries = AsyncMock()
    queries.query.return_value = sample_statuses()[0]
    application = BotApplication(
        client, config, settings, CardRenderer(), recorder, query_service=queries
    )
    application.start()
    connection = QQGateway(client, application.offer_event, max_backoff_seconds=0.01)
    task = asyncio.create_task(connection.run())
    try:
        await asyncio.wait_for(completed.wait(), 5)
        await application.queue.join()
        assert auth[0]["op"] == 2
        assert auth[0]["d"]["intents"] == 1 << 25
        assert all(message["msg_id"] == "DEMO_MESSAGE_ID" for message in received)
        if reply_format == "image":
            assert len(uploaded) == 1 and uploaded[0].startswith(b"\x89PNG")
            assert [message["msg_type"] for message in received] == [7, 0]
            assert [message["msg_seq"] for message in received] == [1, 2]
            assert received[0]["media"]["file_info"] == "image-token"
            assert "203.0.113.10:27015" in received[1]["content"]
        else:
            assert not uploaded
            assert len(received) == 1 and received[0]["msg_type"] == 2
            assert received[0]["msg_seq"] == 1
            assert "模拟数据" not in received[0]["markdown"]["content"]
            assert "路过的幸存者" not in received[0]["markdown"]["content"]
            button = received[0]["keyboard"]["content"]["rows"][0]["buttons"][0]
            assert button["action"]["type"] == 0
            assert button["action"]["data"] == "https://connect.example.test/203.0.113.10:27015"
            queries.query.assert_awaited_once_with(
                "203.0.113.10:27015", alias="合作服", allow_private=True, include_players=True
            )
        await recorder.flush()
        assert settings.discovery_path.is_file()
    finally:
        await connection.close()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await application.close()


@pytest.mark.parametrize("mode", ["reconnect", "missing_ack", "invalid_session"])
async def test_gateway_recovery(mode, local_server, http_session):
    authentications = []
    recovered = asyncio.Event()

    async def token(request):
        return web.json_response({"access_token": "token", "expires_in": 7200})

    async def gateway(request):
        return web.json_response({"url": base.replace("http:", "ws:") + "/ws"})

    async def socket(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"op": 10, "d": {"heartbeat_interval": 20}})
        authentications.append(await ws.receive_json())
        if len(authentications) == 1:
            await ws.send_json({"op": 0, "t": "READY", "s": 10, "d": {"session_id": "session"}})
            if mode != "missing_ack":
                await ws.send_json({"op": 7 if mode == "reconnect" else 9, "d": False})
        else:
            await ws.send_json({"op": 0, "t": "RESUMED", "s": 11, "d": {}})
            recovered.set()
        async for message in ws:
            if message.type == WSMsgType.TEXT and mode != "missing_ack":
                await ws.send_json({"op": 11})
        return ws

    server = web.Application()
    server.router.add_post("/token", token)
    server.router.add_get("/gateway", gateway)
    server.router.add_get("/ws", socket)
    base = await local_server(server)
    api = QQClient(
        http_session,
        app_id="fixture",
        client_secret="fixture",
        api_base=base,
        token_url=base + "/token",
    )
    connection = QQGateway(api, lambda event: True, max_backoff_seconds=0.01)
    task = asyncio.create_task(connection.run())
    try:
        await asyncio.wait_for(recovered.wait(), 3)
        assert authentications[0]["op"] == 2
        assert authentications[1]["op"] == (2 if mode == "invalid_session" else 6)
        if mode != "invalid_session":
            assert authentications[1]["d"]["seq"] == 10
    finally:
        await connection.close()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def test_bad_ws_credentials_fail_without_endless_retry(local_server, http_session):
    async def token(request):
        return web.json_response({"access_token": "token", "expires_in": 7200})

    async def gateway(request):
        return web.json_response({"url": base.replace("http:", "ws:") + "/ws"})

    async def socket(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        await ws.send_json({"op": 10, "d": {"heartbeat_interval": 1000}})
        await ws.receive_json()
        await ws.close(code=4004)
        return ws

    server = web.Application()
    server.router.add_post("/token", token)
    server.router.add_get("/gateway", gateway)
    server.router.add_get("/ws", socket)
    base = await local_server(server)
    api = QQClient(
        http_session,
        app_id="fixture",
        client_secret="fixture",
        api_base=base,
        token_url=base + "/token",
    )
    with pytest.raises(GatewayFatalError):
        await asyncio.wait_for(QQGateway(api, lambda event: True).run(), 3)


async def test_permission_fallback_and_reply_budget():
    api = AsyncMock()
    api.send_group.side_effect = [QQAPIError(200, 304057), None, None, None, None]
    message = GroupMessage.from_payload(sample_event())
    reply = ReplySession(api, message)
    await reply.connection(
        "连接1服", "connect example:27015", "https://example.test/1", buttons=True
    )
    assert [call.args[2] for call in api.send_group.await_args_list] == [1, 2]
    replacement = api.send_group.await_args_list[-1].args[3]
    assert replacement["msg_type"] == 2 and "keyboard" not in replacement
    assert "按钮被平台拒绝" in replacement["markdown"]["content"]
    assert reply.sent_count == 1
    for _ in range(3):
        await reply.text("hello")
    with pytest.raises(ValueError):
        await reply.text("sixth")


def test_connection_targets_are_per_server_and_utf8_chunks_bounded():
    first = ServerConfig(name="1服", address="203.0.113.1", connect_url="https://connect.example/a")
    second = ServerConfig(name="2服", address="203.0.113.2")
    assert connect_url(first, "https://connect.example") == (
        "https://connect.example/203.0.113.1:27015"
    )
    assert connect_url(first, "") == ""
    assert (
        connect_url(second, "https://connect.example")
        == "https://connect.example/203.0.113.2:27015"
    )
    text = "玩家😀" * 2000
    chunks = split_text(text)
    assert "".join(chunks) == text
    assert all(len(chunk.encode("utf-8")) <= 3500 for chunk in chunks)
