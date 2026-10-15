import asyncio
import base64
import io

import pytest
from aiohttp import web
from PIL import Image

from l4d2_qq_bot.qq.client import QQAPIError, QQClient


def client(session, base):
    return QQClient(
        session,
        app_id="fixture-app",
        client_secret="fixture-secret",
        api_base=base,
        token_url=base + "/token",
    )


async def test_token_refresh_is_shared_and_errors_hide_server_body(local_server, http_session):
    calls = 0
    reject = False

    async def token(request):
        nonlocal calls
        calls += 1
        data = await request.json()
        assert data == {"appId": "fixture-app", "clientSecret": "fixture-secret"}
        if reject:
            return web.json_response({"code": 100016, "message": "fixture-secret"})
        return web.json_response({"access_token": "token", "expires_in": "7200"})

    app = web.Application()
    app.router.add_post("/token", token)
    base = await local_server(app)
    api = client(http_session, base)
    assert await asyncio.gather(*(api.access_token() for _ in range(12))) == ["token"] * 12
    assert calls == 1
    await api.invalidate_token("token")
    reject = True
    with pytest.raises(QQAPIError) as error:
        await api.access_token()
    assert "fixture-secret" not in str(error.value)
    assert error.value.code == 100016


async def test_401_refresh_then_reuses_same_message_sequence(local_server, http_session):
    tokens = 0
    requests = []

    async def token(request):
        nonlocal tokens
        tokens += 1
        return web.json_response({"access_token": str(tokens), "expires_in": 7200})

    async def messages(request):
        requests.append((request.headers.get("Authorization"), await request.json()))
        if len(requests) == 1:
            return web.json_response({"code": 100006}, status=401)
        return web.json_response({"id": "sent"})

    app = web.Application()
    app.router.add_post("/token", token)
    app.router.add_post("/v2/groups/group/messages", messages)
    api = client(http_session, await local_server(app))
    await api.send_group("group", "original-message", 2, {"msg_type": 0, "content": "hello"})
    assert tokens == 2
    assert [item[0] for item in requests] == ["QQBot 1", "QQBot 2"]
    assert all(
        item[1]["msg_seq"] == 2 and item[1]["msg_id"] == "original-message" for item in requests
    )


async def test_server_errors_do_not_blindly_resend(local_server, http_session):
    count = 0

    async def token(request):
        return web.json_response({"access_token": "token", "expires_in": 7200})

    async def messages(request):
        nonlocal count
        count += 1
        return web.json_response({"code": 500, "message": "secret-server-body"}, status=500)

    app = web.Application()
    app.router.add_post("/token", token)
    app.router.add_post("/v2/groups/group/messages", messages)
    api = client(http_session, await local_server(app))
    with pytest.raises(QQAPIError) as error:
        await api.send_group("group", "message", 1, {"msg_type": 0, "content": "hello"})
    assert count == 1 and "secret-server-body" not in str(error.value)


async def test_png_upload_only_transfers_then_returns_file_info(local_server, http_session):
    out = io.BytesIO()
    Image.new("RGB", (2, 2)).save(out, "PNG")
    png = out.getvalue()

    async def token(request):
        return web.json_response({"access_token": "token", "expires_in": 7200})

    async def upload(request):
        assert request.headers["Authorization"] == "QQBot token"
        payload = await request.json()
        assert payload["srv_send_msg"] is False
        assert payload["file_name"].endswith(".png")
        assert base64.b64decode(payload["file_data"]) == png
        return web.json_response({"file_info": "opaque", "ttl": 300})

    app = web.Application()
    app.router.add_post("/token", token)
    app.router.add_post("/v2/groups/group/files", upload)
    api = client(http_session, await local_server(app))
    assert (await api.upload_png("group", png)).file_info == "opaque"


async def test_malformed_business_code_is_safe(local_server, http_session):
    async def token(request):
        return web.json_response({"code": {"detail": "fixture-secret"}})

    app = web.Application()
    app.router.add_post("/token", token)
    api = client(http_session, await local_server(app))
    with pytest.raises(QQAPIError) as error:
        await api.access_token()
    assert "fixture-secret" not in str(error.value)
