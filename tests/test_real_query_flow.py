import asyncio
import struct
from unittest.mock import AsyncMock

import pytest
from aiohttp import web

from l4d2_qq_bot.app import BotApplication
from l4d2_qq_bot.cli import create_parser
from l4d2_qq_bot.config import Settings
from l4d2_qq_bot.demo import sample_config, sample_event
from l4d2_qq_bot.integrations.http import ExternalHTTP
from l4d2_qq_bot.integrations.maps import MapNameClient
from l4d2_qq_bot.qq.protocol import GroupMessage
from l4d2_qq_bot.qq.replies import ReplySession
from l4d2_qq_bot.services.query import QueryService


def cstring(value):
    return value.encode("utf-8") + b"\0"


class QueryDatagramServer(asyncio.DatagramProtocol):
    def __init__(self, *, answer_players=True):
        self.answer_players = answer_players
        self.requests = []

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, address):
        header = b"\xff\xff\xff\xff"
        self.requests.append(data[4:5])
        if data[4:5] == b"T":
            response = (
                header
                + b"I\x11"
                + cstring("真正的服务器(测试)")
                + cstring("c1m1_hotel")
                + cstring("left4dead2")
                + cstring("Left 4 Dead 2")
                + struct.pack("<HBBB", 550, 2, 8, 0)
                + b"dl\0\x01"
                + cstring("2.2.4.3")
                + b"\0"
            )
        elif data[4:5] == b"U" and self.answer_players:
            if data[-4:] != struct.pack("<I", 69):
                response = header + b"A" + struct.pack("<I", 69)
            else:
                response = header + b"D\x02"
                for index, name in enumerate(("真实玩家", "Alice")):
                    response += bytes([index]) + cstring(name) + struct.pack("<if", 42, 123.5)
        else:
            return
        self.transport.sendto(response, address)


@pytest.fixture
async def udp_server():
    transports = []

    async def start(*, answer_players=True):
        server = QueryDatagramServer(answer_players=answer_players)
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(
            lambda: server, local_addr=("127.0.0.1", 0)
        )
        transports.append(transport)
        return f"127.0.0.1:{transport.get_extra_info('sockname')[1]}", server

    yield start
    for transport in transports:
        transport.close()
    await asyncio.sleep(0)


async def test_real_udp_and_map_http_produce_one_native_card_with_true_name(
    udp_server, local_server, http_session
):
    address, udp = await udp_server()

    async def map_name(request):
        return web.Response(text="死亡中心：旅馆")

    server = web.Application()
    server.router.add_get("/c1m1_hotel", map_name)
    base = await local_server(server)
    maps = MapNameClient(ExternalHTTP(http_session), base)
    config = sample_config()
    config.groups[0].servers[0].address = address
    config.connect_base_url = "https://connect.example.test"
    api = AsyncMock()
    application = BotApplication(
        api, config, Settings(_env_file=None), None, AsyncMock(), maps=maps
    )
    message = GroupMessage.from_payload(sample_event())
    try:
        await application.handle_message(message, ReplySession(api, message))
        api.send_group.assert_awaited_once()
        payload = api.send_group.await_args.args[3]
        content = payload["markdown"]["content"]
        assert payload["msg_type"] == 2
        assert content.startswith("# 服务器: 真正的服务器(测试)")
        assert "死亡中心：旅馆" in content and "人数: 2/8" in content
        assert "真实玩家" not in content and r"真\*\*家" in content
        assert "模拟" not in content and "已脱敏" not in content
        assert "查询时间" not in content and "connect " not in content
        assert "\\(" not in content
        button = payload["keyboard"]["content"]["rows"][0]["buttons"][0]
        assert button["render_data"]["label"] == "进入服务器"
        assert button["action"]["data"] == f"https://connect.example.test/{address}"
        assert b"T" in udp.requests and udp.requests.count(b"U") >= 2
    finally:
        await application.close()


async def test_real_udp_player_timeout_retains_info(udp_server):
    address, _ = await udp_server(answer_players=False)
    async with asyncio.timeout(2):
        status = await QueryService(timeout=0.1).query(address, allow_private=True)
    assert status.online and status.server_name == "真正的服务器(测试)"
    assert status.player_count == 2 and status.players is None


def test_live_entrypoint_has_no_simulated_data_switch():
    with pytest.raises(SystemExit):
        create_parser().parse_args(["run", "--demo-data"])
