import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import l4d2_qq_bot.app as app_module
from l4d2_qq_bot.app import BotApplication
from l4d2_qq_bot.cards.markdown import markdown_card
from l4d2_qq_bot.cards.views import server_card
from l4d2_qq_bot.commands import CommandKind, match_server, parse_command, server_query_command
from l4d2_qq_bot.config import GroupConfig, ServerConfig, Settings
from l4d2_qq_bot.demo import sample_config, sample_event, sample_statuses
from l4d2_qq_bot.domain import Player
from l4d2_qq_bot.qq.client import QQAPIError
from l4d2_qq_bot.qq.protocol import GroupMessage
from l4d2_qq_bot.qq.replies import ReplySession


def event(text, identifier, *, member="DEMO_MEMBER_OPENID", group="DEMO_GROUP_OPENID"):
    payload = sample_event()
    payload["t"] = "GROUP_MESSAGE_CREATE"
    payload["d"].update(content=text, id=identifier, group_openid=group)
    payload["d"]["author"]["member_openid"] = member
    return payload


def application(*, config=None, status=None, buttons=True):
    client, query = AsyncMock(), AsyncMock()
    config = config or sample_config()
    config.connect_base_url = "https://connect.example.test"

    async def result(address, *, alias="", **kwargs):
        return replace(status or sample_statuses()[0], alias=alias, address=address)

    query.query.side_effect = result
    app = BotApplication(
        client,
        config,
        Settings(_env_file=None, connect_buttons=buttons),
        None,
        Mock(),
        query_service=query,
    )
    return app, client, query


async def handle(app, client, text, identifier="source"):
    message = GroupMessage.from_payload(event(text, identifier))
    await app.handle_message(message, ReplySession(client, message))


def controls(payload):
    return [
        button
        for row in payload["keyboard"]["content"]["rows"]
        for button in row["buttons"]
        if button["id"].startswith("query_")
    ]


@pytest.mark.parametrize(
    "players,count,expected",
    [
        ((), 0, "当前无玩家在线。"),
        ((), 4, "服务器未返回可显示的玩家名称。"),
        (None, 4, "玩家列表获取失败或未开放，在线人数以服务器报告为准。"),
    ],
)
def test_player_states_are_quoted_without_claiming_a_failed_list_is_empty(players, count, expected):
    status = replace(sample_statuses()[0], players=players, player_count=count)
    content = markdown_card(server_card(status))
    assert f"> {expected}" in content
    assert "在线玩家:" not in content
    if count:
        assert "当前无玩家在线" not in content


@pytest.mark.parametrize(
    "name,aliases,expected",
    [
        ("合作服", ["1服"], "查询 合作服"),
        ("合作语音", ["3服"], "查询 3服"),
        ("语音", ["3服"], "查询 3服"),
        ("合作语音", ["晚间语音"], "connect 203.0.113.10:27015"),
    ],
)
def test_retry_selector_round_trips_the_original_configured_target(name, aliases, expected):
    server = ServerConfig(name=name, aliases=aliases, address="203.0.113.10:27015")
    text = server_query_command(server, [server])
    assert text == expected and not text.startswith("/")
    command = parse_command(text)
    if command.kind == CommandKind.QUERY:
        assert match_server([server], command.argument) is server
    else:
        assert command.kind == CommandKind.CONNECT and command.argument == server.address


@pytest.mark.parametrize(
    "text,expected",
    [
        ("综合查询", [("查询合作服", "查询 合作服"), ("再次查询", "综合查询")]),
        ("查询1服", [("🌐 综合查询", "综合查询"), ("🔍 再次查询", "查询 合作服")]),
        (
            "connect 203.0.113.10:27015",
            [("🌐 综合查询", "综合查询"), ("🔍 再次查询", "connect 203.0.113.10:27015")],
        ),
    ],
)
async def test_query_buttons_send_plain_commands_with_no_callback_or_emoji_data(text, expected):
    app, client, query = application()
    try:
        await handle(app, client, text)
        payload = client.send_group.await_args.args[3]
        assert payload["msg_type"] == 2
        buttons = controls(payload)
        assert [(b["render_data"]["label"], b["action"]["data"]) for b in buttons] == expected
        for button in buttons:
            assert button["action"]["type"] == 2
            assert button["action"]["enter"] is True
            assert button["action"]["reply"] is False
            assert button["action"]["permission"] == {"type": 2}
            assert not button["action"]["data"].startswith("/")
            assert not any(icon in button["action"]["data"] for icon in ("🌐", "🔍", "👥"))
        if text == "综合查询":
            assert "- 👥 3/8" in payload["markdown"]["content"]
            assert query.query.await_args.kwargs["include_players"] is False
    finally:
        await app.close()


async def test_offline_single_server_keeps_a_short_markdown_card_and_query_buttons():
    app, client, _ = application(status=sample_statuses()[1])
    try:
        await handle(app, client, "查询1服")
        payload = client.send_group.await_args.args[3]
        assert payload["msg_type"] == 2
        assert payload["markdown"]["content"].startswith("# 合作服\n")
        assert "无法连接" in payload["markdown"]["content"]
        assert [b["action"]["data"] for b in controls(payload)] == ["综合查询", "查询 合作服"]
    finally:
        await app.close()


async def test_all_player_quotes_and_query_keyboard_survive_pagination():
    players = tuple(Player(f"玩家[{i}]*", duration_seconds=i) for i in range(200))
    status = replace(sample_statuses()[0], players=players, player_count=200)
    app, client, _ = application(status=status)
    app.config.safe_mode = False
    try:
        await handle(app, client, "查询1服")
        calls = client.send_group.await_args_list
        assert 1 < len(calls) <= 5
        assert all("keyboard" not in call.args[3] for call in calls[:-1])
        assert len(controls(calls[-1].args[3])) == 2
        text = "\n".join(c.args[3]["markdown"]["content"] for c in calls)
        assert sum(line.startswith("> 玩家&#91;") for line in text.splitlines()) == 200
        assert "&#93;\\* (" in text
        assert all(c.args[3]["markdown"]["content"].startswith("> ") for c in calls[1:])
        assert all(len(c.args[3]["markdown"]["content"].encode()) <= 3200 for c in calls)
    finally:
        await app.close()


async def test_button_rejection_keeps_copyable_query_text_and_does_not_query_again():
    app, client, query = application()
    client.send_group.side_effect = [QQAPIError(403, "304057"), None]
    try:
        await handle(app, client, "查询1服")
        query.query.assert_awaited_once()
        fallback = client.send_group.await_args.args[3]
        assert "keyboard" not in fallback
        assert "综合查询" in fallback["markdown"]["content"]
        assert "查询 合作服" in fallback["markdown"]["content"]
    finally:
        await app.close()


async def test_button_commands_follow_the_ordinary_group_message_path_and_cooldown(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(app_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    config = sample_config()
    config.groups[0].servers.append(ServerConfig(name="2服", address="203.0.113.20:27015"))
    config.groups.append(GroupConfig(group_openid="OTHER_GROUP", servers=config.groups[0].servers))
    app, client, query = application(config=config)
    app.start()

    async def send(text, identifier, **kwargs):
        assert app.offer_event(event(text, identifier, **kwargs))
        await asyncio.wait_for(app.queue.join(), 1)

    try:
        await send("综合查询", "overview")
        command = controls(client.send_group.await_args.args[3])[-1]["action"]["data"]
        clock[0] += 3
        await send(command, "button-overview")
        assert query.query.await_count == 4
        await send("查询1服", "first")
        await send("查询 合作服", "same-target-alias")
        assert query.query.await_count == 5
        await send("查询1服", "different-member", member="OTHER_MEMBER")
        await send("查询1服", "different-group", group="OTHER_GROUP")
        await send("查询2服", "different-target")
        assert query.query.await_count == 8
        clock[0] += 3
        await send("查询1服", "after-cooldown")
        assert query.query.await_count == 9
        for identifier in ("help-1", "help-2"):
            await send("帮助", identifier)
        assert client.send_group.await_args.args[1] == "help-2"
    finally:
        await app.close()


async def test_inflight_duplicate_is_suppressed_even_after_two_seconds(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(app_module, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    app, client, query = application()
    started, release, duplicate_seen = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def result(address, **kwargs):
        started.set()
        await release.wait()
        return sample_statuses()[0]

    query.query.side_effect = result
    original = app._query_key

    def observe(message):
        key = original(message)
        if message.message_id == "duplicate":
            duplicate_seen.set()
        return key

    monkeypatch.setattr(app, "_query_key", observe)
    app.start()
    try:
        assert app.offer_event(event("查询1服", "first"))
        await asyncio.wait_for(started.wait(), 1)
        clock[0] += 4
        assert app.offer_event(event("查询 合作服", "duplicate"))
        await asyncio.wait_for(duplicate_seen.wait(), 1)
        query.query.assert_awaited_once()
        release.set()
        await asyncio.wait_for(app.queue.join(), 1)
        client.send_group.assert_awaited_once()
    finally:
        release.set()
        await app.close()


@pytest.mark.parametrize("count", [0, 1, 3, 8, 9, 32])
async def test_overview_shortcuts_keep_two_columns_and_a_separate_refresh_row(count):
    config = sample_config()
    config.groups[0].servers = [
        ServerConfig(name=f"{i + 1}服", address=f"203.0.113.{i + 1}:27015")
        for i in range(max(1, count))
    ]
    status = replace(sample_statuses()[0], player_count=3 if count else 0)
    app, client, _ = application(config=config, status=status)
    try:
        await handle(app, client, "综合查询")
        calls = client.send_group.await_args_list
        assert all("keyboard" not in c.args[3] for c in calls[:-1])
        payload = calls[-1].args[3]
        rows = payload["keyboard"]["content"]["rows"]
        assert 1 <= len(rows) <= 5
        assert all(1 <= len(row["buttons"]) <= 2 for row in rows)
        assert len(rows[-1]["buttons"]) == 1
        buttons = controls(payload)
        assert [(b["render_data"]["label"], b["action"]["data"]) for b in buttons] == [
            *((f"查询{i + 1}服", f"查询 {i + 1}服") for i in range(min(count, 8))),
            ("再次查询", "综合查询"),
        ]
        body = "\n".join(c.args[3]["markdown"]["content"] for c in calls)
        assert ("仅显示前 8 台" in body) is (count > 8)
        for server in config.groups[0].servers:
            assert f"&#91;{server.name}&#93;" in body
        assert all(len(c.args[3]["markdown"]["content"].encode()) <= 3200 for c in calls)
    finally:
        await app.close()


async def test_overview_filters_empty_and_failed_servers_without_renumbering():
    config = sample_config()
    config.groups[0].servers = [
        ServerConfig(name=name, address=f"203.0.113.{i + 1}:27015")
        for i, name in enumerate(("5服", "3服", "9服", "12服"))
    ]
    app, client, query = application(config=config)

    async def result(address, *, alias, **kwargs):
        return replace(
            sample_statuses()[0],
            address=address,
            alias=alias,
            online=alias != "3服",
            player_count=0 if alias == "9服" else 3,
        )

    query.query.side_effect = result
    try:
        await handle(app, client, "综合查询")
        assert [
            (b["render_data"]["label"], b["action"]["data"])
            for b in controls(client.send_group.await_args.args[3])
        ] == [("查询5服", "查询 5服"), ("查询12服", "查询 12服"), ("再次查询", "综合查询")]
    finally:
        await app.close()


async def test_disabled_buttons_and_image_offline_messages_keep_the_existing_modes():
    app, client, _ = application(buttons=False)
    try:
        await handle(app, client, "综合查询")
        assert "keyboard" not in client.send_group.await_args.args[3]
        assert "仅显示前 8 台" not in client.send_group.await_args.args[3]["markdown"]["content"]
    finally:
        await app.close()
    app, client, _ = application(status=sample_statuses()[1])
    app.settings.reply_format = "image"
    try:
        await handle(app, client, "查询1服")
        assert client.send_group.await_args.args[3]["msg_type"] == 0
        assert "keyboard" not in client.send_group.await_args.args[3]
    finally:
        await app.close()
