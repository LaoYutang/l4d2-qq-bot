"""Bounded asynchronous A2S calls; no AstrBot or QQ dependency."""

import asyncio
import ipaddress
import socket
from collections.abc import Sequence

import a2s

from ..address import Endpoint, parse_address
from ..domain import Player, ServerStatus


class TargetNotAllowed(ValueError):
    pass


async def resolve_target(
    endpoint: Endpoint, *, allow_private: bool, timeout_seconds: float
) -> Endpoint:
    loop = asyncio.get_running_loop()
    async with asyncio.timeout(timeout_seconds):
        records = await loop.getaddrinfo(endpoint.host, endpoint.port, type=socket.SOCK_DGRAM)
    addresses = list(dict.fromkeys(record[4][0] for record in records))
    if not addresses:
        raise OSError("DNS 未返回地址")
    if not allow_private and any(not ipaddress.ip_address(ip).is_global for ip in addresses):
        raise TargetNotAllowed(
            "临时查询默认只接受公网地址；自有私网服务器请显式使用 --allow-private"
        )
    addresses.sort(key=lambda ip: ipaddress.ip_address(ip).version)
    # Pin the actual datagram target rather than resolving the hostname a second time.
    return Endpoint(addresses[0], endpoint.port)


class QueryService:
    def __init__(self, timeout: float = 3.0, concurrency: int = 4):
        self.timeout = timeout
        self._semaphore = asyncio.Semaphore(concurrency)

    async def query(
        self,
        address: str,
        *,
        alias: str = "",
        allow_private: bool = False,
        include_players: bool = True,
    ) -> ServerStatus:
        endpoint = parse_address(address)
        alias = alias or endpoint.address
        async with self._semaphore:
            try:
                target = await resolve_target(
                    endpoint, allow_private=allow_private, timeout_seconds=self.timeout
                )
                loop = asyncio.get_running_loop()
                started = loop.time()
                async with asyncio.timeout(self.timeout):
                    info = await a2s.ainfo((target.host, target.port), timeout=self.timeout)
                ping_ms = round((loop.time() - started) * 1000)
            except TargetNotAllowed:
                raise
            except (OSError, TimeoutError, a2s.BrokenMessageError, ValueError):
                return ServerStatus(
                    alias=alias,
                    address=endpoint.address,
                    online=False,
                    error="离线或查询超时，请检查服务器和 UDP 查询端口。",
                )
            players: tuple[Player, ...] | None = None
            if include_players:
                if info.player_count == 0:
                    players = ()
                else:
                    try:
                        # PLAYER has its own wall-clock budget. Its timeout cannot discard INFO.
                        async with asyncio.timeout(self.timeout):
                            raw_players = await a2s.aplayers(
                                (target.host, target.port), timeout=self.timeout
                            )
                        players = tuple(
                            Player(item.name, item.score, item.duration)
                            for item in raw_players
                            if item.name.strip()
                        )
                    except (OSError, TimeoutError, a2s.BrokenMessageError, ValueError):
                        players = None
            return ServerStatus(
                alias=alias,
                address=endpoint.address,
                online=True,
                server_name=info.server_name,
                map_code=info.map_name,
                map_name=info.map_name,
                player_count=info.player_count,
                max_players=info.max_players,
                ping_ms=ping_ms,
                players=players,
            )

    async def query_many(
        self, servers: Sequence[tuple[str, str]], *, allow_private: bool = True
    ) -> list[ServerStatus]:
        return list(
            await asyncio.gather(
                *(
                    self.query(
                        address,
                        alias=name,
                        allow_private=allow_private,
                        include_players=False,
                    )
                    for name, address in servers
                )
            )
        )
