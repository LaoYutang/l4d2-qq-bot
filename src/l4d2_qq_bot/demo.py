"""A reproducible fixture-to-image example, with no network calls."""

import copy
import html
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PIL import Image

from . import __version__
from .app import route_event
from .cards.renderer import CardRenderer
from .cards.views import overview_card, player_name, server_card
from .config import AppConfig
from .domain import Player, ServerStatus


def sample_event() -> dict[str, Any]:
    return {
        "id": "DEMO_EVENT_ID",
        "op": 0,
        "s": 42,
        "t": "GROUP_AT_MESSAGE_CREATE",
        "d": {
            "id": "DEMO_MESSAGE_ID",
            "group_openid": "DEMO_GROUP_OPENID",
            "author": {"member_openid": "DEMO_MEMBER_OPENID", "bot": False},
            "content": " /查询 1 服 ",
        },
    }


def sample_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "safe_mode": True,
            "groups": [
                {
                    "group_openid": "DEMO_GROUP_OPENID",
                    "servers": [
                        {"name": "合作服", "aliases": ["1服"], "address": "203.0.113.10:27015"}
                    ],
                }
            ],
        }
    )


def sample_statuses() -> list[ServerStatus]:
    return [
        ServerStatus(
            alias="合作服",
            address="203.0.113.10:27015",
            online=True,
            server_name="一起打僵尸 · 合作战役",
            map_code="c1m1_hotel",
            map_name="死亡中心 · 旅馆",
            player_count=3,
            max_players=8,
            ping_ms=42,
            players=(
                Player("路过的幸存者", 120, 3903),
                Player("Alice", 88, 754),
                Player("咸鱼", 42, 42),
            ),
        ),
        ServerStatus(
            alias="对抗服",
            address="203.0.113.20:27016",
            online=False,
            error="离线或查询超时，请检查服务器和 UDP 查询端口。",
        ),
        ServerStatus(
            alias="三方图服",
            address="203.0.113.30:27017",
            online=True,
            server_name="自定义地图 · 周末开黑",
            map_code="school01",
            map_name="重返学校 · 校园",
            player_count=4,
            max_players=16,
            ping_ms=68,
            players=None,
        ),
    ]


def run_demo(
    output: Path, renderer: CardRenderer, event_path: Path | None = None
) -> dict[str, Any]:
    event = (
        sample_event() if event_path is None else json.loads(event_path.read_text(encoding="utf-8"))
    )
    config = sample_config()
    if not isinstance(event, dict):
        raise ValueError("示例事件必须是 JSON 对象")
    routed = route_event(event, config)
    if routed is None or routed.server is None:
        raise ValueError("示例事件未匹配演示群和服务器；请使用 examples/group_event.json")
    statuses = sample_statuses()
    if routed.server.address != statuses[0].address:
        raise ValueError("示例服务器与 fixture 不匹配")
    output.mkdir(parents=True, exist_ok=True)
    cards = [
        ("server-card.png", "单服查询 · 玩家名脱敏", server_card(statuses[0], demo=True)),
        ("overview-card.png", "综合查询 · 部分离线", overview_card(statuses, demo=True)),
        ("offline-card.png", "服务器离线", server_card(statuses[1], demo=True)),
        (
            "players-unavailable-card.png",
            "在线但玩家列表获取失败",
            server_card(statuses[2], demo=True),
        ),
    ]
    files = []
    for filename, label, card in cards:
        data = renderer.render(card)
        (output / filename).write_bytes(data)
        with Image.open(io.BytesIO(data)) as image:
            files.append(
                {
                    "file": filename,
                    "label": label,
                    "width": image.width,
                    "height": image.height,
                    "bytes": len(data),
                }
            )
    unknown = copy.deepcopy(event)
    unknown["d"]["group_openid"] = "ANOTHER_GROUP"
    checks = {
        "query_alias_matches": routed.server.name == "合作服",
        "uses_message_id_in_data": routed.message.message_id == "DEMO_MESSAGE_ID",
        "unconfigured_group_ignored": route_event(unknown, config) is None,
        "short_name_is_masked": player_name("咸鱼", safe_mode=True) == "咸*鱼",
        "offline_is_distinct": not statuses[1].online,
        "missing_players_not_zero": statuses[2].online and statuses[2].players is None,
        "png_size_within_upload_budget": all(file["bytes"] < 3 * 1024 * 1024 for file in files),
    }
    if not all(checks.values()):
        raise ValueError("示例自检失败，请检查 report 中的状态")
    report = {
        "version": __version__,
        "mode": "offline_fixture",
        "generated_at": datetime.now(UTC).isoformat(),
        "input_command": routed.message.content,
        "matched_server": routed.server.name,
        "qq_live_verified": False,
        "network_used": False,
        "checks": checks,
        "files": files,
        "copyable_connection": f"connect {statuses[0].address}",
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    panels = "".join(
        f'<article><h2>{html.escape(label)}</h2><img src="{filename}" alt="{html.escape(label)}">'
        "</article>"
        for filename, label, _ in cards
    )
    page = f"""<!doctype html>
<html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>L4D2 卡片验收 · {__version__}</title>
<style>
body{{margin:0;background:#0d130e;color:#edf5e8;font:16px/1.6 system-ui,sans-serif}}
main{{max-width:1380px;margin:48px auto;padding:0 28px}}h1{{font-size:32px}}
.badge{{color:#b5f36d}}p{{max-width:900px;color:#b8c7af}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:28px}}
article{{min-width:0}}h2{{font-size:18px}}img{{width:100%;height:auto;border-radius:16px}}
code{{padding:6px 10px;background:#24301f;border-radius:8px}}a{{color:#b5f36d}}
@media(max-width:440px){{.grid{{grid-template-columns:1fr}}main{{padding:0 14px}}}}
</style><main><span class="badge">M0 · 离线验收 · v{__version__}</span>
<h1>L4D2 查询卡片示例</h1>
<p>全部为模拟数据。流程：QQ 群事件 fixture → 本群配置 → 指令解析 → 模拟查询结果 → PNG 卡片。
尚未验证真实 QQ 收发，也未向游戏服务器发请求。</p>
<p>输入：<code>{html.escape(routed.message.content)}</code> → 合作服；复制连接指令示例：
<code>connect 203.0.113.10:27015</code>（文档保留地址，不能实际连接）。</p>
<p>验收重点：信息层级、中文字体、名字脱敏、离线状态与玩家列表失败的区别。
<a href="report.json">查看机器验证报告</a></p><div class="grid">{panels}</div></main></html>"""
    (output / "index.html").write_text(page, encoding="utf-8")
    return report
