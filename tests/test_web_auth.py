import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from l4d2_qq_bot import web as config_web
from l4d2_qq_bot.config import Settings
from l4d2_qq_bot.config_store import ConfigStore

PASSWORD = "fixture-password"
HEADERS = {"X-Config-Password": PASSWORD}


@pytest.fixture(autouse=True)
def password_clock(monkeypatch):
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(config_web, "time", SimpleNamespace(monotonic=lambda: clock.now))
    return clock


async def test_concurrent_bad_passwords_share_a_bucket_across_methods_and_connections(
    local_server, http_session, tmp_path
):
    path = tmp_path / "config.json"
    base = await local_server(
        config_web.create_web_app(
            Settings(_env_file=None, web_password=PASSWORD), ConfigStore(path)
        )
    )

    async def attempt(index):
        method = "GET" if index % 2 else "POST"
        async with http_session.request(
            method,
            base + "/api/config",
            headers={"X-Config-Password": f"wrong-{index}"},
        ) as response:
            result = await response.json()
            assert response.headers["Cache-Control"] == "no-store"
            assert "Content-Security-Policy" in response.headers
            assert "Set-Cookie" not in response.headers
            assert "config" not in result and PASSWORD not in str(result)
            if response.status == 429:
                assert response.headers["Retry-After"] == "60"
                assert result["retry_after"] == 60
            return response.status

    assert sorted(await asyncio.gather(*(attempt(i) for i in range(20)))) == [401] * 5 + [429] * 15
    assert not path.exists()


async def test_correct_password_does_not_consume_tokens_or_bypass_an_exhausted_bucket(
    local_server, http_session, tmp_path, password_clock
):
    path = tmp_path / "config.json"
    base = await local_server(
        config_web.create_web_app(
            Settings(_env_file=None, web_password=PASSWORD), ConfigStore(path)
        )
    )
    for _ in range(7):
        async with http_session.get(base + "/api/config", headers=HEADERS) as response:
            assert response.status == 200
    for _ in range(5):
        async with http_session.get(base + "/api/config") as response:
            assert response.status == 401
    async with http_session.get(base + "/api/config") as response:
        assert response.status == 429
    async with http_session.get(base + "/api/config", headers=HEADERS) as response:
        assert response.status == 429
    async with http_session.post(
        base + "/api/config", headers=HEADERS, json={"config": {}, "revision": "missing"}
    ) as response:
        assert response.status == 429
    assert not path.exists()
    password_clock.now += 60
    async with http_session.get(base + "/api/config", headers=HEADERS) as response:
        assert response.status == 200
        current = await response.json()
    current["config"]["safe_mode"] = False
    async with http_session.post(base + "/api/config", headers=HEADERS, json=current) as response:
        assert response.status == 200
        assert (await response.json())["saved"]
    assert path.exists()
    async with http_session.post(base + "/api/config") as response:
        assert response.status == 401
    async with http_session.post(base + "/api/config") as response:
        assert response.status == 429


async def test_retry_after_and_refill_use_elapsed_time_and_keep_the_five_token_cap(
    local_server, http_session, tmp_path, password_clock
):
    base = await local_server(
        config_web.create_web_app(
            Settings(_env_file=None, web_password=PASSWORD), ConfigStore(tmp_path / "config.json")
        )
    )

    async def attempt(status, retry_after=None):
        async with http_session.get(base + "/api/config") as response:
            assert response.status == status
            if retry_after is not None:
                assert response.headers["Retry-After"] == str(retry_after)
                result = await response.json()
                assert result["retry_after"] == retry_after
                assert str(retry_after) in result["error"]

    for _ in range(5):
        await attempt(401)
    await attempt(429, 60)
    password_clock.now += 59.25
    await attempt(429, 1)
    password_clock.now += 0.75
    await attempt(401)
    await attempt(429, 60)
    password_clock.now += 3000
    for _ in range(5):
        await attempt(401)
    await attempt(429, 60)


async def test_forwarding_headers_and_static_pages_cannot_reset_the_bucket(
    local_server, http_session, tmp_path
):
    base = await local_server(
        config_web.create_web_app(
            Settings(_env_file=None, web_password=PASSWORD), ConfigStore(tmp_path / "config.json")
        )
    )
    for route in ("/", "/app.js", "/style.css"):
        async with http_session.get(base + route) as response:
            assert response.status == 200
    for index in range(7):
        async with http_session.get(
            base + "/api/config",
            headers={
                "X-Config-Password": "wrong",
                "X-Forwarded-For": f"192.0.2.{index}",
                "X-Real-IP": f"192.0.2.{index}",
                "Forwarded": f"for=192.0.2.{index}",
            },
        ) as response:
            assert response.status == (401 if index < 5 else 429)
    async with http_session.get(base + "/") as response:
        assert response.status == 200
    async with http_session.get(base + "/api/other") as response:
        assert response.status == 429


def test_sources_are_independent_and_failed_retries_do_not_extend_the_wait(password_clock):
    limiter = config_web.PasswordAttemptLimiter()
    for _ in range(5):
        assert limiter.consume("192.0.2.1") == 0
    assert limiter.consume("192.0.2.1") == 60
    for _ in range(5):
        assert limiter.consume("2001:db8::1") == 0
    password_clock.now += 30
    assert limiter.consume("192.0.2.1") == 30
    password_clock.now += 30
    assert limiter.consume("192.0.2.1") == 0
    assert limiter.consume("192.0.2.1") == 60
    assert limiter.consume("2001:db8::1") == 0


async def test_middleware_uses_peer_ip_and_skips_password_comparison_while_limited(
    tmp_path, monkeypatch
):
    compare = Mock(wraps=config_web.hmac.compare_digest)
    monkeypatch.setattr(config_web, "hmac", SimpleNamespace(compare_digest=compare))
    app = config_web.create_web_app(
        Settings(_env_file=None, web_password=PASSWORD), ConfigStore(tmp_path / "config.json")
    )
    handler = AsyncMock(return_value=web.json_response({"ok": True}))

    async def attempt(source, password):
        transport = Mock()
        transport.get_extra_info.return_value = (source, 12345)
        request = make_mocked_request(
            "GET", "/api/config", headers={"X-Config-Password": password}, transport=transport
        )
        return await app.middlewares[0](request, handler)

    for _ in range(5):
        assert (await attempt("192.0.2.1", "wrong")).status == 401
    assert compare.call_count == 5
    assert (await attempt("192.0.2.1", PASSWORD)).status == 429
    assert (await attempt("192.0.2.1", "new-guess")).status == 429
    assert compare.call_count == 5
    assert (await attempt("192.0.2.2", PASSWORD)).status == 200
    assert compare.call_count == 6
    handler.assert_awaited_once()


def test_full_bucket_cache_rejects_new_sources_without_evicting_active_limits(password_clock):
    limiter = config_web.PasswordAttemptLimiter(max_sources=2)
    for source in ("192.0.2.1", "192.0.2.2"):
        for _ in range(5):
            assert limiter.consume(source) == 0
    for index in range(3, 50):
        assert limiter.consume(f"192.0.2.{index}") == 300
    assert len(limiter._buckets) == 2
    assert limiter.consume("192.0.2.1") == 60
    password_clock.now += 300
    for _ in range(5):
        assert limiter.consume("192.0.2.3") == 0
    assert len(limiter._buckets) == 1
    assert limiter.consume("192.0.2.3") == 60


def test_partial_bucket_usage_preserves_order_during_expiration(password_clock):
    limiter = config_web.PasswordAttemptLimiter(max_sources=2)
    assert limiter.consume("192.0.2.1") == 0
    assert limiter.consume("192.0.2.2") == 0
    password_clock.now += 30
    assert limiter.consume("192.0.2.1") == 0
    password_clock.now += 270
    assert limiter.consume("192.0.2.3") == 0
    assert set(limiter._buckets) == {"192.0.2.1", "192.0.2.3"}
    # An idle, partly used bucket refills instead of accumulating extra capacity.
    for _ in range(5):
        assert limiter.consume("192.0.2.1") == 0
    assert limiter.consume("192.0.2.1") == 60
