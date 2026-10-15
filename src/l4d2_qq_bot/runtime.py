"""Resource lifecycle for the live WS bot and read-only discovery mode."""

import asyncio
import logging
import signal
from pathlib import Path

import aiohttp
from aiohttp import web

from .app import BotApplication
from .cards.renderer import CardRenderer
from .config import AppConfig, Settings
from .config_store import ConfigStore
from .integrations.heybox import HeyboxVoiceClient
from .integrations.http import ExternalHTTP
from .integrations.maps import MapNameClient
from .qq.client import QQClient
from .qq.discovery import DiscoveryRecorder
from .qq.gateway import QQGateway
from .qq.protocol import GroupMessage
from .services.management import ManagementService
from .web import create_web_app

logger = logging.getLogger(__name__)


def integrations(session: aiohttp.ClientSession, config: AppConfig, settings: Settings) -> dict:
    http = ExternalHTTP(
        session,
        timeout=config.integrations.timeout_seconds,
        concurrency=config.integrations.concurrency,
    )
    return {
        "maps": MapNameClient(http, config.map_name_url),
        "voice": HeyboxVoiceClient(
            http,
            config.heybox_bot_id or settings.heybox_bot_id,
            config.heybox_token or settings.heybox_token.get_secret_value(),
        ),
    }


async def serve(
    settings: Settings,
    config: AppConfig | None,
    *,
    duration_seconds: float = 0,
    config_path: Path | None = None,
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
    async with (
        aiohttp.ClientSession(timeout=timeout, trust_env=True) as session,
        aiohttp.ClientSession(trust_env=True) as external_session,
    ):
        client = QQClient(
            session,
            app_id=settings.app_id,
            client_secret=settings.client_secret.get_secret_value(),
            api_base=settings.api_base,
            token_url=settings.token_url,
        )
        app = None
        web_runner = None
        if config is not None:
            if any(group.group_openid.startswith(("REPLACE_", "DEMO_")) for group in config.groups):
                raise ValueError("请替换群占位值，或使用空 groups 启动后在群中发送“获取群ID”")
            clients = integrations(external_session, config, settings)
            app = BotApplication(
                client,
                config,
                settings,
                CardRenderer(settings.card_font_path) if settings.reply_format == "image" else None,
                discovery,
                management=ManagementService(clients["maps"].http),
                **clients,
            )
            if settings.web_password.get_secret_value():
                store = ConfigStore(config_path or settings.config_path)
                await store.read()

                def apply_config(updated):
                    app.reconfigure(updated, **integrations(external_session, updated, settings))

                store.on_change = apply_config
                web_runner = web.AppRunner(create_web_app(settings, store), access_log=None)
                try:
                    await web_runner.setup()
                    await web.TCPSite(web_runner, settings.web_host, settings.web_port).start()
                except BaseException:
                    await web_runner.cleanup()
                    await app.close()
                    raise
                logger.info("配置界面已启动，端口 %s", settings.web_port)
            else:
                logger.warning("未设置 L4D2_WEB_PASSWORD，配置界面未启用")
            app.start()
            on_event = app.offer_event
            logger.info("查询数据模式：真实 A2S，外部查询使用真实接口")
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

            logger.info(
                "只监听模式：请在测试群发送消息（未开启全量接收时需 @ 机器人），"
                "OpenID 会写入本地 discovery 文件"
            )
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
            if web_runner is not None:
                await web_runner.cleanup()
            await gateway.close()
            for task in (gateway_task, stop_task, writer):
                task.cancel()
            await asyncio.gather(gateway_task, stop_task, writer, return_exceptions=True)
            if app is not None:
                await app.close()
            await discovery.flush()
