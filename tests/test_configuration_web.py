import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from l4d2_qq_bot.app import BotApplication
from l4d2_qq_bot.config import Settings, load_config
from l4d2_qq_bot.config_store import ConfigStore
from l4d2_qq_bot.demo import sample_config, sample_event, sample_statuses
from l4d2_qq_bot.qq.protocol import GroupMessage
from l4d2_qq_bot.qq.replies import ReplySession
from l4d2_qq_bot.web import create_web_app

PASSWORD = "fixture-config-password"
HEADERS = {"X-Config-Password": PASSWORD}


def test_missing_web_password_cannot_open_an_unprotected_api(tmp_path):
    with pytest.raises(ValueError, match="L4D2_WEB_PASSWORD"):
        create_web_app(Settings(_env_file=None), ConfigStore(tmp_path / "config.json"))


async def test_password_is_required_on_reads_and_writes(local_server, http_session, tmp_path):
    path = tmp_path / "config.json"
    settings = Settings(
        _env_file=None, web_password=PASSWORD, app_id="private-app-id", client_secret="private-key"
    )
    base = await local_server(create_web_app(settings, ConfigStore(path)))
    for method in ("GET", "POST"):
        for headers in ({}, {"X-Config-Password": "wrong"}, {"Authorization": PASSWORD}):
            async with http_session.request(
                method, base + "/api/config", headers=headers
            ) as response:
                assert response.status == 401
                assert "Set-Cookie" not in response.headers
    assert not path.exists()
    async with http_session.get(base + "/api/config", headers=HEADERS) as response:
        text = await response.text()
        assert response.status == 200 and response.headers["Cache-Control"] == "no-store"
        assert not any(value in text for value in (PASSWORD, "private-app-id", "private-key"))
        assert json.loads(text)["revision"] == "missing"
    for route in ("/", "/app.js", "/style.css"):
        async with http_session.get(base + route) as response:
            assert response.status == 200


async def test_save_validates_conflicts_and_applies_without_restart(
    local_server, http_session, tmp_path
):
    applied = []
    path = tmp_path / "config.json"
    store = ConfigStore(path, applied.append)
    base = await local_server(
        create_web_app(Settings(_env_file=None, web_password=PASSWORD), store)
    )
    config, revision = await store.read()
    data = sample_config().model_dump()
    data["heybox_token"] = "fixture-heybox-token"
    data["groups"][0]["admin_openids"] = ["fixture-admin"]
    data["groups"][0]["admin_remarks"] = {"fixture-admin": "群主"}
    data["groups"][0]["servers"][0]["rcon_password"] = "fixture-rcon-password"
    voice_url = "https://voice.example.test/invite?room=fixture#join"
    data["groups"][0]["servers"][0]["voice_connect_url"] = voice_url
    async with http_session.post(
        base + "/api/config", headers=HEADERS, json={"config": data, "revision": revision}
    ) as response:
        result = await response.json()
        assert response.status == 200 and result["saved"]
    assert applied[-1].heybox_token == "fixture-heybox-token"
    assert result["config"]["groups"][0]["admin_remarks"] == {"fixture-admin": "群主"}
    assert load_config(path).groups[0].admin_remarks == {"fixture-admin": "群主"}
    assert load_config(path).groups[0].servers[0].rcon_password == "fixture-rcon-password"
    assert result["config"]["groups"][0]["servers"][0]["voice_connect_url"] == voice_url
    assert load_config(path).groups[0].servers[0].voice_connect_url == voice_url
    assert path.stat().st_mode & 0o777 == 0o600
    raw = path.read_bytes()
    for payload, status in (
        ({"config": data, "revision": revision}, 409),
        ({"config": data}, 400),
        ({"config": {"groups": [data["groups"][0]] * 2}, "revision": result["revision"]}, 400),
        ({"config": {"client_secret": "must-not-leak"}, "revision": result["revision"]}, 400),
    ):
        async with http_session.post(
            base + "/api/config", headers=HEADERS, json=payload
        ) as response:
            assert response.status == status
            assert "must-not-leak" not in await response.text()
        assert path.read_bytes() == raw
    assert len(applied) == 2  # Initial snapshot and one successful write.


async def test_removed_workshop_settings_are_dropped_on_read_and_save(
    local_server, http_session, tmp_path
):
    path = tmp_path / "config.json"
    data = sample_config().model_dump()
    data["integrations"].update(
        timeout_seconds=7.0,
        concurrency=2,
        workshop_api_url="https://obsolete.example/api",
        workshop_max_items=20,
    )
    path.write_text(json.dumps(data), encoding="utf-8")
    base = await local_server(
        create_web_app(Settings(_env_file=None, web_password=PASSWORD), ConfigStore(path))
    )
    async with http_session.get(base + "/api/config", headers=HEADERS) as response:
        assert response.status == 200
        result = await response.json()
    expected = {"timeout_seconds": 7.0, "concurrency": 2}
    assert result["config"]["integrations"] == expected
    assert load_config(path).integrations.model_dump() == expected
    async with http_session.post(
        base + "/api/config", headers=HEADERS, json={"config": data, "revision": result["revision"]}
    ) as response:
        assert response.status == 200
        saved = await response.json()
    assert saved["config"]["integrations"] == expected
    assert json.loads(path.read_text(encoding="utf-8"))["integrations"] == expected
    assert saved["config"]["groups"] == data["groups"]


async def test_inflight_queries_keep_their_configuration_and_new_queries_use_the_save():
    config = sample_config()
    config.connect_base_url = "https://old.example"
    old_maps, new_maps = AsyncMock(), AsyncMock()
    entered, release = asyncio.Event(), asyncio.Event()

    async def old_name(code):
        entered.set()
        await release.wait()
        return "旧地图名称"

    old_maps.name.side_effect = old_name
    query = AsyncMock()
    query.query.return_value = sample_statuses()[0]
    client = AsyncMock()
    app = BotApplication(
        client,
        config,
        Settings(_env_file=None),
        None,
        AsyncMock(),
        query_service=query,
        maps=old_maps,
    )
    message = GroupMessage.from_payload(sample_event())
    task = asyncio.create_task(app.handle_message(message, ReplySession(client, message)))
    await entered.wait()
    updated = config.model_copy(deep=True)
    updated.connect_base_url = "https://new.example"
    app.reconfigure(updated, maps=new_maps, voice=AsyncMock())
    old_maps.close.assert_not_awaited()
    release.set()
    await task
    payload = client.send_group.await_args.args[3]
    assert "旧地图名称" in payload["markdown"]["content"]
    assert payload["keyboard"]["content"]["rows"][0]["buttons"][0]["action"]["data"].startswith(
        "https://old.example/"
    )
    assert app.config.connect_base_url == "https://new.example"
    old_maps.close.assert_awaited_once()
    await app.close()
