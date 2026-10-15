import aiohttp
import pytest
from aiohttp import web


@pytest.fixture
async def local_server():
    runners = []

    async def start(app):
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", 0).start()
        runners.append(runner)
        return f"http://127.0.0.1:{runner.addresses[0][1]}"

    yield start
    for runner in reversed(runners):
        await runner.cleanup()


@pytest.fixture
async def http_session():
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
        yield session
