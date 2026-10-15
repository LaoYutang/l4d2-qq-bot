import io
import json
from pathlib import Path

import pytest
from PIL import Image

from l4d2_qq_bot.cards.renderer import CardRenderer
from l4d2_qq_bot.cards.views import duration_text, player_name, server_card
from l4d2_qq_bot.demo import run_demo, sample_statuses
from l4d2_qq_bot.domain import Card, CardSection


@pytest.mark.parametrize(
    ("name", "masked"), [("甲", "甲*"), ("甲乙", "甲*乙"), ("幸存者", "幸*者")]
)
def test_safe_mode_covers_short_names(name, masked):
    assert player_name(name, safe_mode=True) == masked
    assert player_name(name, safe_mode=False) == name


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (42, "0:42"),
        (3903, "1:05:03"),
        (86401, "1:00:00:01"),
        (-5, "0:00"),
        (float("nan"), "0:00"),
    ],
)
def test_duration_format(seconds, expected):
    assert duration_text(seconds) == expected


def test_player_failure_view_does_not_claim_empty_server():
    status = sample_statuses()[2]
    card = server_card(status)
    lines = [line for section in card.sections for line in section.lines]
    assert any("获取失败" in line for line in lines)
    assert not any("当前无玩家在线" in line for line in lines)


def test_renderer_bounds_untrusted_text():
    renderer = CardRenderer()
    card = Card(
        title="很长的中文标题" * 1000,
        sections=tuple(CardSection("章节", ("玩家\x00\n名字" * 300,) * 30) for _ in range(30)),
    )
    data = renderer.render(card)
    with Image.open(io.BytesIO(data)) as image:
        assert image.width == 1040
        assert 0 < image.height <= renderer.max_height
        image.verify()
    assert len(data) < 3 * 1024 * 1024


def test_invalid_explicit_font_does_not_silently_fallback(tmp_path):
    with pytest.raises(ValueError, match="中文字体"):
        CardRenderer(tmp_path / "missing-font.ttf")


def test_fixture_to_cards_report(tmp_path):
    fixture = Path(__file__).resolve().parents[1] / "examples/group_event.json"
    report = run_demo(tmp_path, CardRenderer(), fixture)
    assert all(report["checks"].values())
    assert not report["network_used"] and not report["qq_live_verified"]
    assert (tmp_path / "index.html").is_file()
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8")) == report
    for item in report["files"]:
        with Image.open(tmp_path / item["file"]) as image:
            image.verify()
