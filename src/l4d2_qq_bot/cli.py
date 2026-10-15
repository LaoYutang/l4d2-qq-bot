"""Local examples, real queries and live QQ WebSocket entry points."""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from pydantic import ValidationError

from . import __version__
from .cards.renderer import CardRenderer
from .cards.views import server_card
from .config import Settings, load_config
from .demo import run_demo, sample_statuses
from .qq.client import QQAPIError, QQTransportError
from .qq.gateway import GatewayFatalError
from .runtime import serve
from .services.query import QueryService


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="L4D2 QQ Bot · 初始化与卡片验收工具")
    parser.add_argument("--version", action="version", version=__version__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="只校验业务配置，不连接网络")
    validate.add_argument("--config", type=Path, default=None)
    preview = subparsers.add_parser("preview", help="生成一张模拟单服卡片")
    preview.add_argument("--output", type=Path, default=Path("artifacts/server-card.png"))
    demo = subparsers.add_parser("demo", help="离线群事件示例，生成四张卡片与报告")
    demo.add_argument("--output", type=Path, default=Path("artifacts/demo"))
    demo.add_argument("--event", type=Path, default=None, help="可选 QQ 群事件 fixture")
    query = subparsers.add_parser("query", help="本地查询真实服务器，保存卡片，不连接 QQ")
    query.add_argument("address", help="IP 或域名，可省略 27015 端口")
    query.add_argument("--output", type=Path, default=Path("artifacts/live-card.png"))
    query.add_argument("--allow-private", action="store_true", help="允许查询自己管理的私网地址")
    query.add_argument("--timeout", type=float, default=3.0)
    run = subparsers.add_parser("run", help="连接 QQ WS，处理配置群中的真实消息")
    run.add_argument("--config", type=Path, default=None)
    run.add_argument(
        "--demo-data", action="store_true", help="QQ 真实收发，查询结果使用标记过的模拟数据"
    )
    discover = subparsers.add_parser("discover", help="连接 QQ，只发现群 OpenID，不回复消息")
    discover.add_argument("--duration", type=float, default=120, help="监听秒数，0 表示持续运行")
    return parser


def main() -> int:
    args = create_parser().parse_args()
    try:
        settings = Settings()
        logging.basicConfig(level=settings.log_level, format="%(levelname)s %(name)s: %(message)s")
        # Library DEBUG logging may include HTTP objects; application logs never need payloads.
        for library in ("aiohttp", "asyncio"):
            logging.getLogger(library).setLevel(logging.WARNING)
        if args.command in {"run", "discover"}:
            settings.require_credentials()
            if args.command == "discover":
                if not 0 <= args.duration <= 3600:
                    raise ValueError("监听时长应在 0–3600 秒之间")
                asyncio.run(serve(settings, None, duration_seconds=args.duration))
            else:
                config = load_config(args.config or settings.config_path)
                asyncio.run(serve(settings, config, demo_data=args.demo_data))
            return 0
        if args.command == "validate":
            config = load_config(args.config or settings.config_path)
            total = sum(len(group.servers) for group in config.groups)
            print(f"配置校验通过：{len(config.groups)} 个群，{total} 台服务器。")
            print("此操作未验证网络、凭据或 OpenID 的真实性。")
            return 0
        renderer = CardRenderer(settings.card_font_path)
        if args.command == "demo":
            report = run_demo(args.output, renderer, args.event)
            print(f"离线示例已生成：{(args.output / 'index.html').resolve()}")
            print(f"{len(report['checks'])} 项自检通过，4 张卡片；未使用网络，未验证真实 QQ 收发。")
            return 0
        if args.command == "preview":
            card = server_card(sample_statuses()[0], demo=True)
            status = None
        else:
            if not 0.1 <= args.timeout <= 15:
                raise ValueError("timeout 应在 0.1–15 秒范围内")
            status = asyncio.run(
                QueryService(timeout=args.timeout).query(
                    args.address, allow_private=args.allow_private
                )
            )
            card = server_card(status, safe_mode=True)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(renderer.render(card))
        print(f"卡片已生成：{args.output.resolve()}")
        if status is not None:
            print(f"connect {status.address}")
            if not status.online:
                print(status.error, file=sys.stderr)
                return 1
        return 0
    except ValidationError as exc:
        # Pydantic's default string representation may include secret input values.
        for error in exc.errors(include_input=False, include_url=False):
            location = ".".join(str(part) for part in error["loc"])
            print(f"配置错误 {location}: {error['msg']}", file=sys.stderr)
        return 2
    except (QQAPIError, QQTransportError, GatewayFatalError) as exc:
        print(f"QQ 接入失败：{exc}", file=sys.stderr)
        return 3
    except KeyboardInterrupt:
        print("机器人已停止。")
        return 130
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"操作失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
