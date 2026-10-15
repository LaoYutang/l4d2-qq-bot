import asyncio
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from l4d2_qq_bot.address import Endpoint
from l4d2_qq_bot.services import query
from l4d2_qq_bot.services.query import QueryService, TargetNotAllowed, resolve_target


def info(player_count=3):
    return SimpleNamespace(
        server_name="Test server", map_name="c1m1_hotel", player_count=player_count, max_players=8
    )


def records(*addresses):
    return [(socket.AF_INET, socket.SOCK_DGRAM, 17, "", (ip, 27015)) for ip in addresses]


async def test_dns_checks_all_addresses_and_pins_target(monkeypatch):
    loop = asyncio.get_running_loop()
    resolver = AsyncMock(return_value=records("8.8.8.8", "127.0.0.1"))
    monkeypatch.setattr(loop, "getaddrinfo", resolver)
    with pytest.raises(TargetNotAllowed):
        await resolve_target(Endpoint("example.test"), allow_private=False, timeout_seconds=1)
    resolver.return_value = records("8.8.8.8")
    result = await resolve_target(Endpoint("example.test"), allow_private=False, timeout_seconds=1)
    assert result == Endpoint("8.8.8.8")


@pytest.fixture
def pinned(monkeypatch):
    resolver = AsyncMock(return_value=Endpoint("8.8.8.8"))
    monkeypatch.setattr(query, "resolve_target", resolver)
    return resolver


async def test_player_failure_keeps_server_online(monkeypatch, pinned):
    info_call = AsyncMock(return_value=info())
    monkeypatch.setattr(query.a2s, "ainfo", info_call)
    monkeypatch.setattr(query.a2s, "aplayers", AsyncMock(side_effect=TimeoutError))
    status = await QueryService().query("public.example")
    assert status.online and status.player_count == 3
    assert status.players is None
    assert info_call.await_args.args[0] == ("8.8.8.8", 27015)


async def test_confirmed_zero_players_does_not_fetch_players(monkeypatch, pinned):
    monkeypatch.setattr(query.a2s, "ainfo", AsyncMock(return_value=info(0)))
    players = AsyncMock()
    monkeypatch.setattr(query.a2s, "aplayers", players)
    status = await QueryService().query("public.example")
    assert status.online and status.players == ()
    players.assert_not_awaited()


async def test_player_wall_clock_timeout_does_not_discard_info(monkeypatch, pinned):
    monkeypatch.setattr(query.a2s, "ainfo", AsyncMock(return_value=info()))

    async def never_finishes(*args, **kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr(query.a2s, "aplayers", never_finishes)
    async with asyncio.timeout(0.25):
        status = await QueryService(timeout=0.01).query("public.example")
    assert status.online and status.player_count == 3 and status.players is None


async def test_info_timeout_returns_offline(monkeypatch, pinned):
    monkeypatch.setattr(query.a2s, "ainfo", AsyncMock(side_effect=TimeoutError))
    status = await QueryService().query("public.example")
    assert not status.online and status.players is None


async def test_temporary_private_target_is_not_reported_as_server_offline(monkeypatch):
    monkeypatch.setattr(query, "resolve_target", AsyncMock(side_effect=TargetNotAllowed("private")))
    with pytest.raises(TargetNotAllowed):
        await QueryService().query("localhost")


async def test_overview_is_bounded_and_preserves_order(monkeypatch, pinned):
    active = peak = 0

    async def fake_info(*args, **kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return info()

    monkeypatch.setattr(query.a2s, "ainfo", fake_info)
    statuses = await QueryService(concurrency=2).query_many(
        [(str(index), f"public{index}.example") for index in range(7)]
    )
    assert peak == 2
    assert [status.alias for status in statuses] == [str(index) for index in range(7)]
