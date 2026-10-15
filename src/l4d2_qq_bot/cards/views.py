"""Build presentation models; preserve unknown and offline states."""

import math
import unicodedata
from datetime import timedelta, timezone

from ..domain import Card, CardSection, ServerStatus


def clean_text(value: str, limit: int = 200) -> str:
    value = "".join(ch for ch in value if unicodedata.category(ch) not in {"Cc", "Cf", "Cs"})
    return value[:limit].strip()


def player_name(value: str, *, safe_mode: bool) -> str:
    value = clean_text(value, 80) or "匿名玩家"
    if not safe_mode:
        return value
    if len(value) == 1:
        return value + "*"
    return value[0] + "*" * max(len(value) - 2, 1) + value[-1]


def duration_text(seconds: float) -> str:
    total = max(0, int(seconds)) if math.isfinite(seconds) else 0
    days, rest = divmod(total, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    if days:
        return f"{days}天 {hours:02d}:{minutes:02d}:{seconds:02d}"
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def server_card(status: ServerStatus, *, safe_mode: bool = True, demo: bool = False) -> Card:
    label = "模拟数据 · " if demo else ""
    timestamp = status.queried_at.astimezone(timezone(timedelta(hours=8))).strftime("%H:%M:%S")
    footer = f"{label}查询时间 {timestamp} (UTC+8) · L4D2 QQ BOT"
    if not status.online:
        return Card(
            title=clean_text(status.alias),
            subtitle=f"{label}离线 / 无法连接",
            accent="#ff987c",
            sections=(
                CardSection("连接状态", (status.error or "服务器暂不可达，请稍后重试。",)),
                CardSection("服务器地址", (f"connect {status.address}",)),
            ),
            footer=footer,
        )
    if status.players is None:
        player_lines = ("玩家列表获取失败或未开放，在线人数以服务器报告为准。",)
    elif not status.players:
        player_lines = (
            ("当前无玩家在线。" if status.player_count == 0 else "服务器未返回可显示的玩家名称。"),
        )
    else:
        player_lines = tuple(
            f"{index:02d}   {player_name(player.name, safe_mode=safe_mode)}"
            f"     {duration_text(player.duration_seconds)}"
            for index, player in enumerate(status.players[:10], 1)
        )
        if len(status.players) > 10:
            player_lines += (f"另有 {len(status.players) - 10} 位玩家未在本卡片展示。",)
    map_name = clean_text(status.map_name or status.map_code)
    return Card(
        title=clean_text(status.alias),
        subtitle=f"{label}在线 · {clean_text(status.server_name)}",
        sections=(
            CardSection(
                "实时状态",
                (
                    f"地图   {map_name}",
                    f"人数   {status.player_count} / {status.max_players}"
                    f"     延迟   {status.ping_ms} ms",
                ),
            ),
            CardSection("在线玩家" + (" · 已脱敏" if safe_mode else ""), player_lines),
            CardSection(
                "连接服务器", (f"connect {status.address}", "在游戏控制台输入此指令连接。")
            ),
        ),
        footer=footer,
    )


def overview_card(statuses: list[ServerStatus], *, demo: bool = False) -> Card:
    visible = statuses[:8]
    online = [status for status in statuses if status.online]
    label = "模拟数据 · " if demo else ""
    sections = []
    for status in visible:
        if status.online:
            lines = (
                clean_text(status.server_name),
                f"{status.player_count}/{status.max_players} 人 · {status.ping_ms} ms"
                f" · {clean_text(status.map_name or status.map_code)}",
            )
        else:
            lines = ("离线 / 查询超时",)
        sections.append(CardSection(clean_text(status.alias), lines))
    if len(statuses) > 8:
        # A later QQ router will paginate before rendering. Local callers still get a visible note.
        sections[-1] = CardSection(
            sections[-1].heading,
            (*sections[-1].lines, f"另有 {len(statuses) - 8} 台服务器需在后续卡片展示。"),
        )
    return Card(
        title="L4D2 服务器概览",
        subtitle=(
            f"{label}{len(online)}/{len(statuses)} 台在线 · "
            f"{sum(status.player_count for status in online)} / "
            f"{sum(status.max_players for status in online)} 人"
        ),
        sections=tuple(sections) or (CardSection("暂无服务器", ("请先配置本群服务器。",)),),
        footer=f"{label}在线统计采用服务器报告 · L4D2 QQ BOT",
    )
