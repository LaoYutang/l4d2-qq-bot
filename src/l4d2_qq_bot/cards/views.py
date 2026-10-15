"""Build presentation models; preserve unknown and offline states."""

import math
import unicodedata

from ..domain import Card, CardLink, CardSection, ServerStatus, VoiceStatus, WorkshopResult
from ..integrations.workshop import workshop_url


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
    if not status.online:
        return Card(
            title=clean_text(status.alias),
            subtitle=f"{label}离线 / 无法连接 · 未获取到真实服务器名",
            accent="#ff987c",
            sections=(CardSection("连接状态", (status.error or "服务器暂不可达，请稍后重试。",)),),
            footer="",
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
    voice_sections = ()
    if status.voice_configured:
        voice_sections = (
            CardSection(
                "黑盒语音",
                (f"{status.voice_count} 人在线",)
                if status.voice_count is not None
                else ("语音人数查询失败，不影响游戏服务器状态。",),
            ),
        )
    return Card(
        title=clean_text(status.server_name) or "服务器未返回名称",
        subtitle=f"{label}在线 · 查询快捷名：{clean_text(status.alias)}",
        sections=(
            CardSection(
                "实时状态",
                (
                    f"地图   {map_name}",
                    f"人数   {status.player_count} / {status.max_players}"
                    f"     延迟   {status.ping_ms} ms",
                ),
            ),
            CardSection("在线玩家", player_lines),
        )
        + voice_sections,
        footer="",
    )


def overview_card(statuses: list[ServerStatus], *, demo: bool = False) -> Card:
    visible = statuses[:8]
    online = [status for status in statuses if status.online]
    label = "模拟数据 · " if demo else ""
    sections = []
    for status in visible:
        if status.online:
            lines = (
                f"查询快捷名：{clean_text(status.alias)}",
                f"{status.player_count}/{status.max_players} 人 · {status.ping_ms} ms"
                f" · {clean_text(status.map_name or status.map_code)}",
            )
        else:
            lines = ("离线 / 查询超时",)
        if status.voice_configured:
            lines += (
                f"黑盒语音 {status.voice_count} 人在线"
                if status.voice_count is not None
                else "黑盒语音人数暂不可用",
            )
        title = status.server_name if status.online and status.server_name else status.alias
        sections.append(CardSection(clean_text(title), lines))
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


def voice_card(status: VoiceStatus, *, safe_mode: bool = True) -> Card:
    if status.count is None:
        return Card(
            title=f"{clean_text(status.alias)} · 黑盒语音",
            subtitle="未配置" if not status.configured else "查询失败",
            sections=(CardSection("状态", (status.error,)),),
            footer="",
        )
    if status.names is None:
        lines = ("在线人数已获取，成员昵称暂不可用。",)
    elif status.count == 0:
        lines = ("当前频道无人在线。",)
    else:
        lines = tuple(player_name(name, safe_mode=safe_mode) for name in status.names[:20])
        if status.count > len(lines):
            lines += (f"另有 {status.count - len(lines)} 位成员未在本卡片展示。",)
        if status.error:
            lines += (status.error,)
    return Card(
        title=f"{clean_text(status.alias)} · 黑盒语音",
        subtitle=f"{status.count} 人在线",
        sections=(CardSection("在线成员", lines),),
        footer="",
    )


def file_size_text(value: int | None) -> str:
    if value is None:
        return "接口未提供大小"
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return "接口未提供大小"


def workshop_card(result: WorkshopResult, *, page: int = 1) -> Card:
    if result.error:
        return Card(
            title="Steam 工坊查询失败",
            sections=(CardSection("状态", (result.error,)),),
            footer="",
            links=(CardLink("打开工坊", workshop_url(result.item_id)),),
        )
    if not result.items:
        return Card(
            title=clean_text(result.title) or "Steam 工坊合集",
            subtitle="合集没有公开条目",
            footer="",
            links=(CardLink("打开合集", workshop_url(result.item_id)),),
        )
    if not 1 <= page <= len(result.items):
        raise ValueError(f"当前可查看 {len(result.items)} 页，请发送 工坊 {result.item_id} 页码。")
    item = result.items[page - 1]
    lines = [f"工坊 ID：{item.item_id}"]
    if item.available:
        if item.filename:
            lines.append(f"文件：{clean_text(item.filename)}")
        lines.append(f"大小：{file_size_text(item.size_bytes)}")
        if not item.download_url:
            lines.append("接口未提供直接下载地址，请在 Steam 工坊订阅。")
    else:
        lines.append("条目已删除、私有或接口未返回详情。")
    links = [CardLink("打开工坊", workshop_url(item.item_id))]
    if item.download_url:
        links.append(CardLink("下载文件", item.download_url))
    footer = ""
    if result.collection:
        footer = f"合集共 {result.total_count} 项 · 已获取前 {len(result.items)} 项"
        if page < len(result.items):
            footer += f" · 下一页：工坊 {result.item_id} {page + 1}"
    return Card(
        title=clean_text(item.title) or f"工坊条目 {item.item_id}",
        subtitle=f"{clean_text(result.title, 100)} · {page}/{len(result.items)}"
        if result.collection
        else "Steam 工坊",
        sections=(CardSection("文件信息", tuple(lines)),),
        footer=footer,
        links=tuple(links),
    )
