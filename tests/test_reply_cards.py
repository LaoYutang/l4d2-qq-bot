import html
import re
from dataclasses import replace
from unittest.mock import AsyncMock, Mock

import pytest

from l4d2_qq_bot.app import BotApplication
from l4d2_qq_bot.cards.markdown import markdown_card, markdown_text_pages
from l4d2_qq_bot.cards.views import overview_card, server_card, voice_card
from l4d2_qq_bot.config import ServerConfig, Settings
from l4d2_qq_bot.demo import sample_config, sample_event, sample_statuses
from l4d2_qq_bot.domain import VoiceStatus
from l4d2_qq_bot.qq.client import QQAPIError, QQTransportError
from l4d2_qq_bot.qq.protocol import GroupMessage
from l4d2_qq_bot.qq.replies import ReplySession, split_text
from l4d2_qq_bot.services.management import ManagementService


def visible_lines(content):
    lines = []
    for line in content.splitlines():
        line = re.sub(r"^(?:#{1,2} |> )", "", line)
        if line.startswith("**") and line.endswith("**"):
            line = line[2:-2]
        line = html.unescape(re.sub(r"\\(.)", r"\1", line))
        if line.strip():
            lines.append(line.strip())
    return lines


@pytest.mark.parametrize(
    "card",
    [
        server_card(sample_statuses()[0], connection_url="https://connect.example/a"),
        server_card(sample_statuses()[0], temporary=True, connection_url=""),
        server_card(sample_statuses()[1]),
        server_card(sample_statuses()[2]),
        overview_card(sample_statuses()),
        voice_card(VoiceStatus("1服", True, count=2, names=("甲", "Alice"))),
        voice_card(VoiceStatus("1服", True, count=0, names=())),
        voice_card(VoiceStatus("1服", True, error="语音查询失败。")),
    ],
)
def test_native_layout_keeps_the_existing_visible_body(card):
    content = markdown_card(card)
    assert content.startswith("# ")
    assert visible_lines(content) == [
        line.strip() for line in card.content.splitlines() if line.strip()
    ]
    assert "查询快捷名" not in content and "查询时间" not in content and "已脱敏" not in content


@pytest.mark.parametrize(
    "command, expected, msg_type",
    [
        ("帮助", "查询 服务器名", 2),
        ("查询1服", "一起打僵尸", 2),
        ("connect 203.0.113.10:27015", "地址: 203.0.113.10:27015", 2),
        ("综合查询", "L4D2 服务器概览", 2),
        ("服务器列表", "服务器列表", 2),
        ("语音1服", "合作服 语音频道当前无人在线。", 0),
        ("获取群ID", "DEMO_GROUP_OPENID", 0),
        ("获取用户ID", "DEMO_MEMBER_OPENID", 0),
        ("查询", "请输入服务器名称，例如：查询 主服务器", 0),
        ("服务器列表 2", "页码超出范围，当前共 1 页。", 0),
    ],
)
async def test_command_replies_use_cards_for_results_and_plain_text_for_notices(
    command, expected, msg_type
):
    client = AsyncMock()
    query = AsyncMock()
    query.query.return_value = sample_statuses()[0]
    voice = AsyncMock()
    voice.configured = Mock(return_value=False)
    voice.query.return_value = VoiceStatus("合作服", True, count=0, names=())
    app = BotApplication(
        client,
        sample_config(),
        Settings(_env_file=None),
        None,
        Mock(),
        query_service=query,
        voice=voice,
    )
    message = replace(GroupMessage.from_payload(sample_event()), content=command)
    try:
        await app.handle_message(message, ReplySession(client, message))
        client.send_group.assert_awaited_once()
        group, message_id, sequence, payload = client.send_group.await_args.args
        assert (group, message_id, sequence) == (message.group_openid, message.message_id, 1)
        assert payload["msg_type"] == msg_type
        if msg_type == 2:
            assert "content" not in payload
            assert payload["markdown"]["content"].startswith("# ")
            lines = visible_lines(payload["markdown"]["content"])
        else:
            assert "markdown" not in payload
            lines = payload["content"].splitlines()
        assert any(expected in line for line in lines)
        if command == "帮助":
            assert "工坊" not in payload["markdown"]["content"]
        client.upload_png.assert_not_awaited()
    finally:
        await app.close()


@pytest.mark.parametrize(
    "base_url, buttons, expected_urls",
    [
        (
            "https://connect.example",
            True,
            [
                "https://connect.example/203.0.113.10:27015",
                "https://connect.example/203.0.113.20:27015",
            ],
        ),
        ("", True, []),
        ("https://connect.example", False, []),
    ],
)
async def test_address_list_uses_buttons_or_copyable_commands(base_url, buttons, expected_urls):
    client = AsyncMock()
    config = sample_config()
    config.connect_base_url = base_url
    config.groups[0].servers.append(ServerConfig(name="对抗服", address="203.0.113.20:27015"))
    app = BotApplication(
        client, config, Settings(_env_file=None, connect_buttons=buttons), None, Mock()
    )
    message = replace(GroupMessage.from_payload(sample_event()), content="服务器地址")
    try:
        await app.handle_message(message, ReplySession(client, message))
        client.send_group.assert_awaited_once()
        payload = client.send_group.await_args.args[3]
        content = payload["markdown"]["content"]
        assert visible_lines(content)[0] == "服务器列表"
        assert "===" not in content and "点击下方" not in content and "https://" not in content
        assert content.count("connect ") == 2 - len(expected_urls)
        rows = payload.get("keyboard", {}).get("content", {}).get("rows", [])
        actual = [button for row in rows for button in row["buttons"]]
        assert [button["action"]["data"] for button in actual] == expected_urls
        expected_labels = ["合作服", "对抗服"] if len(expected_urls) == 2 else []
        assert [button["render_data"]["label"] for button in actual] == expected_labels
        assert len(visible_lines(content)) == 1 + 2 - len(expected_urls)
    finally:
        await app.close()


@pytest.mark.parametrize("count", [1, 5, 6, 10, 11, 26, 32])
async def test_address_list_keeps_all_button_targets_within_platform_limits(count):
    client = AsyncMock()
    config = sample_config()
    config.connect_base_url = "https://connect.example"
    config.groups[0].servers = [
        ServerConfig(name=f"{index}服", address=f"203.0.113.{index}:27015")
        for index in range(1, count + 1)
    ]
    app = BotApplication(client, config, Settings(_env_file=None), None, Mock())
    message = replace(GroupMessage.from_payload(sample_event()), content="服务器列表")
    try:
        await app.handle_message(message, ReplySession(client, message))
        calls = client.send_group.await_args_list
        assert len(calls) == (count + 9) // 10
        assert [call.args[2] for call in calls] == list(range(1, len(calls) + 1))
        targets = []
        labels = []
        for call in calls:
            assert call.args[1] == message.message_id and call.args[3]["msg_type"] == 2
            assert call.args[3]["markdown"]["content"] == "# 服务器列表"
            rows = call.args[3]["keyboard"]["content"]["rows"]
            assert 1 <= len(rows) <= 5
            assert all(1 <= len(row["buttons"]) <= 2 for row in rows)
            targets.extend(button["action"]["data"] for row in rows for button in row["buttons"])
            labels.extend(
                button["render_data"]["label"] for row in rows for button in row["buttons"]
            )
        assert targets == [
            f"https://connect.example/203.0.113.{index}:27015" for index in range(1, count + 1)
        ]
        assert labels == [f"{index}服" for index in range(1, count + 1)]
    finally:
        await app.close()


async def test_rcon_immediate_redacted_result_is_plain_text():
    client = AsyncMock()
    config = sample_config()
    config.groups[0].admin_openids = ["DEMO_MEMBER_OPENID"]
    config.groups[0].servers[0].rcon_password = "fixture-rcon"
    rcon = AsyncMock()
    rcon.execute.return_value = "服务器响应: fixture-rcon"
    management = ManagementService(AsyncMock(), rcon)
    app = BotApplication(
        client, config, Settings(_env_file=None), None, Mock(), management=management
    )
    message = replace(GroupMessage.from_payload(sample_event()), content="设置1服 status")
    try:
        await app.handle_message(message, ReplySession(client, message))
        assert not management.has_session(message)
        rcon.execute.assert_awaited_once_with("203.0.113.10:27015", "fixture-rcon", "status")
        confirmation = replace(message, message_id="confirmation", content="确认")
        await app.handle_message(confirmation, ReplySession(client, confirmation))
        rcon.execute.assert_awaited_once_with("203.0.113.10:27015", "fixture-rcon", "status")
        calls = client.send_group.await_args_list
        assert [call.args[1] for call in calls] == [message.message_id]
        assert all(call.args[2] == 1 and call.args[3]["msg_type"] == 0 for call in calls)
        assert calls[0].args[3]["content"] == "服务器响应: ***"
    finally:
        await app.close()


async def test_flash_choices_and_submission_result_are_plain_text():
    client = AsyncMock()
    config = sample_config()
    config.groups[0].admin_openids = ["DEMO_MEMBER_OPENID"]
    server = config.groups[0].servers[0]
    server.panel_url = "https://panel.example"
    server.panel_token = "fixture-panel-token"
    http = AsyncMock()
    http.json.return_value = {
        "items": [
            {"filename": "地图.vpk", "file_url": "https://files.example/a", "supported": True}
        ]
    }
    management = ManagementService(http)
    app = BotApplication(
        client, config, Settings(_env_file=None), None, Mock(), management=management
    )
    original = GroupMessage.from_payload(sample_event())
    try:
        for message_id, content in [
            ("share", "https://qfile.qq.com/q/fixture"),
            ("select-server", "1"),
            ("select-file", "全部"),
        ]:
            message = replace(original, message_id=message_id, content=content)
            await app.handle_message(message, ReplySession(client, message))
        calls = client.send_group.await_args_list
        assert [call.args[1] for call in calls] == ["share", "select-server", "select-file"]
        assert all(call.args[2] == 1 and call.args[3]["msg_type"] == 0 for call in calls)
        bodies = [call.args[3]["content"].splitlines() for call in calls]
        assert bodies[0][0].startswith("检测到 QQ 闪传链接，请选择要上传的服务器")
        assert bodies[0][1] == "1. 合作服"
        assert bodies[1][0].startswith("合作服 解析成功，共 1 个可下载文件")
        assert bodies[1][1].startswith("1. 地图.vpk")
        assert bodies[2] == ["已提交 1 个下载任务：", "- 地图.vpk"]
        http.read.assert_awaited_once()
        assert "fixture-panel-token" not in str(bodies)
    finally:
        await app.close()


async def test_paginated_card_keeps_its_button_on_the_last_reply():
    client = AsyncMock()
    config = sample_config()
    config.connect_base_url = "https://connect.example"
    app = BotApplication(client, config, Settings(_env_file=None), None, Mock())
    message = GroupMessage.from_payload(sample_event())
    card = replace(
        server_card(sample_statuses()[0]),
        content="合作服\n在线玩家:\n" + "幸*者 (1:00)\n" * 200,
    )
    try:
        await app._card(ReplySession(client, message), card, server=config.groups[0].servers[0])
        calls = client.send_group.await_args_list
        assert 1 < len(calls) <= 5
        assert [call.args[2] for call in calls] == list(range(1, len(calls) + 1))
        assert all(
            call.args[1] == message.message_id and call.args[3]["msg_type"] == 2 for call in calls
        )
        assert all("keyboard" not in call.args[3] for call in calls[:-1])
        button = calls[-1].args[3]["keyboard"]["content"]["rows"][0]["buttons"][0]
        assert button["render_data"]["label"] == "🎮 进入服务器"
        assert button["action"]["data"] == "https://connect.example/203.0.113.10:27015"
        actual = [
            line for call in calls for line in visible_lines(call.args[3]["markdown"]["content"])
        ]
        assert actual == card.content.splitlines()
        assert all(
            len(call.args[3]["markdown"]["content"].encode("utf-8")) <= 3200 for call in calls
        )
    finally:
        await app.close()


async def test_card_rejection_preserves_original_body_and_notices_stay_plain():
    client = AsyncMock()
    client.send_group.side_effect = [QQAPIError(403, 304036), None, None]
    message = GroupMessage.from_payload(sample_event())
    reply = ReplySession(client, message)
    await reply.markdown(
        "# 权限不足：您不在管理员列表中。",
        plain_content="权限不足：您不在管理员列表中。",
    )
    await reply.text("服务器响应: ***")
    calls = client.send_group.await_args_list
    assert [call.args[2] for call in calls] == [1, 2, 3]
    assert [call.args[3]["msg_type"] for call in calls] == [2, 0, 0]
    assert calls[1].args[3]["content"] == (
        "原生卡片被平台拒绝，请检查应用 Markdown 权限。\n\n权限不足：您不在管理员列表中。"
    )
    assert calls[2].args[3]["content"] == "服务器响应: ***"
    assert reply.sent_count == 2


async def test_uncertain_notice_send_is_never_replayed():
    client = AsyncMock()
    client.send_group.side_effect = QQTransportError()
    reply = ReplySession(client, GroupMessage.from_payload(sample_event()))
    with pytest.raises(QQTransportError):
        await reply.text("确认重启 合作服？（回复“确认”执行）")
    client.send_group.assert_awaited_once()


async def test_long_replies_preserve_text_escaping_sequence_and_budget():
    text = "服务器响应:\n" + "[外部](名称) <qqbot-at-everyone /> *文字*\n" * 100
    pages = markdown_text_pages(text)
    assert 1 < len(pages) <= 5
    assert "".join(plain for _, plain in pages) == text
    assert all(len(content.encode("utf-8")) <= 3200 for content, _ in pages)
    assert all("<qqbot-at-everyone" not in content for content, _ in pages)
    assert [line for content, _ in pages for line in visible_lines(content)] == text.splitlines()
    client = AsyncMock()
    message = GroupMessage.from_payload(sample_event())
    reply = ReplySession(client, message)
    for content, plain in pages:
        await reply.markdown(content, plain_content=plain)
    calls = client.send_group.await_args_list
    assert [call.args[2] for call in calls] == list(range(1, len(pages) + 1))
    assert all(
        call.args[1] == message.message_id and call.args[3]["msg_type"] == 2 for call in calls
    )
    client.reset_mock()
    with pytest.raises(ValueError, match="文字过长"):
        await reply.text(text * 10)
    client.send_group.assert_not_awaited()


async def test_long_plain_text_keeps_literal_content_and_reply_context():
    text = "提示内容 *原样* [保留]😀\n" * 200
    chunks = split_text(text)
    client = AsyncMock()
    message = GroupMessage.from_payload(sample_event())
    reply = ReplySession(client, message)
    await reply.text(text)
    calls = client.send_group.await_args_list
    assert [call.args[2] for call in calls] == list(range(1, len(chunks) + 1))
    assert all(
        call.args[1] == message.message_id and call.args[3]["msg_type"] == 0 for call in calls
    )
    assert [call.args[3]["content"] for call in calls] == chunks
    assert all("markdown" not in call.args[3] and "keyboard" not in call.args[3] for call in calls)


def test_long_literal_line_is_not_cut_inside_an_escape_or_truncated():
    text = "[名字]*" * 1500
    pages = markdown_text_pages(text)
    assert "".join(plain for _, plain in pages) == text
    assert all(len(content.encode("utf-8")) <= 3200 for content, _ in pages)
    assert "".join(line for content, _ in pages for line in visible_lines(content)) == text


def test_numbered_selection_prompt_keeps_original_numbers_and_literal_markup():
    text = "请选择要下载的文件：\n2. 地图[测试](一).vpk\n8. <qqbot-at-everyone />.zip"
    content = markdown_text_pages(text)[0][0]
    assert "2\\. " in content and "8\\. " in content
    assert "<qqbot-at-everyone" not in content
    assert visible_lines(content) == text.splitlines()
