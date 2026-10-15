"""Resource lifecycle for the live WS bot and read-only discovery mode."""

import asyncio
import logging
import signal

import aiohttp

from .app import BotApplication
from .cards.renderer import CardRenderer
from .config import AppConfig, Settings
from .qq.client import QQClient
from .qq.discovery import DiscoveryRecorder
from .qq.gateway import QQGateway
from .qq.protocol import GroupMessage

logger = logging.getLogger(__name__)


async def serve(
    settings: Settings,
    config: AppConfig | None,
    *,
    demo_data: bool = False,
    duration_seconds: float = 0,
) -> None:
    settings.require_credentials()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, stop.set)
    except NotImplementedError:
        pass
    discovery = DiscoveryRecorder(settings.discovery_path)
    timeout = aiohttp.ClientTimeout(total=settings.http_timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
        client = QQClient(
            session,
            app_id=settings.app_id,
            client_secret=settings.client_secret.get_secret_value(),
            api_base=settings.api_base,
            token_url=settings.token_url,
        )
        app = None
        if config is not None:
            if not config.groups or any(
                group.group_openid.startswith(("REPLACE_", "DEMO_")) for group in config.groups
            ):
                raise ValueError("请先运行 discover 获取群 OpenID，再替换 config.json 的群占位值")
            app = BotApplication(
                client,
                config,
                settings,
                CardRenderer(settings.card_font_path),
                discovery,
                demo_data=demo_data,
            )
            app.start()
            on_event = app.offer_event
            logger.info("查询数据模式：%s", "模拟（卡片会明确标记）" if demo_data else "真实 A2S")
            logger.info("卡片回复格式：%s", settings.reply_format)
        else:

            def on_event(payload):
                message = GroupMessage.from_payload(payload)
                if message is not None and discovery.record(message):
                    logger.info(
                        "已发现群 OpenID，保存于 %s；discover 模式不会回复群消息",
                        settings.discovery_path,
                    )
                return True

            logger.info("只监听模式：请在测试群 @ 机器人，OpenID 会写入本地 discovery 文件")
        gateway = QQGateway(client, on_event)
        writer = asyncio.create_task(discovery.run(), name="qq-discovery-writer")
        gateway_task = asyncio.create_task(gateway.run(), name="qq-gateway")
        stop_task = asyncio.create_task(stop.wait(), name="qq-stop")
        try:
            done, _ = await asyncio.wait(
                {gateway_task, stop_task, writer},
                timeout=duration_seconds or None,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                await task
        finally:
            await gateway.close()
            for task in (gateway_task, stop_task, writer):
                task.cancel()
            await asyncio.gather(gateway_task, stop_task, writer, return_exceptions=True)
            if app is not None:
                await app.close()
            await discovery.flush()
