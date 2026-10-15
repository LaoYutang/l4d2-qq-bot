import copy
from pathlib import Path

import pytest
from pydantic import ValidationError

from l4d2_qq_bot.address import parse_address
from l4d2_qq_bot.app import route_event
from l4d2_qq_bot.commands import CommandKind, match_server, parse_command
from l4d2_qq_bot.config import AppConfig, Settings, load_config
from l4d2_qq_bot.demo import sample_config, sample_event


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("game.example.com", "game.example.com:27015"),
        ("127.0.0.1:27016", "127.0.0.1:27016"),
        ("[2001:db8::1]", "[2001:db8::1]:27015"),
        ("[::1]:27017", "[::1]:27017"),
    ],
)
def test_endpoints(text, expected):
    assert parse_address(text).address == expected


@pytest.mark.parametrize(
    "text",
    ["", "a:0", "a:65536", "a:no", "https://example.com", "a/b", "a b", "::1", "-bad.example"],
)
def test_invalid_endpoints(text):
    with pytest.raises(ValueError):
        parse_address(text)


@pytest.mark.parametrize("text", ["查询1服", "查询 1 服", "/查询 1服", "<@!bot_id> /查询 1服"])
def test_alias_matching_ignores_whitespace(text):
    command = parse_command(text)
    assert command is not None and command.kind == CommandKind.QUERY
    server = match_server(sample_config().groups[0].servers, command.argument)
    assert server is not None and server.name == "合作服"


def test_unmatched_server_and_group_do_not_route():
    config = sample_config()
    event = sample_event()
    event["d"]["content"] = "查询不存在"
    assert route_event(event, config) is None
    event["d"]["group_openid"] = "OTHER_GROUP"
    event["d"]["content"] = "查询"
    assert route_event(event, config) is None


def test_openids_are_strings_and_group_aliases_unique():
    data = sample_config().model_dump()
    invalid = copy.deepcopy(data)
    invalid["groups"][0]["group_openid"] = 123456
    with pytest.raises(ValidationError):
        AppConfig.model_validate(invalid)
    invalid = copy.deepcopy(data)
    invalid["groups"][0]["servers"].append(
        {"name": "其他服", "aliases": ["1 服"], "address": "203.0.113.20"}
    )
    with pytest.raises(ValidationError):
        AppConfig.model_validate(invalid)


def test_bad_url_and_plaintext_credential_reference_rejected():
    data = sample_config().model_dump()
    data["connect_base_url"] = "https://user:password@example.com"
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)
    data["connect_base_url"] = ""
    data["groups"][0]["servers"][0]["rcon_password_env"] = "password with spaces"
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_example_config():
    path = Path(__file__).resolve().parents[1] / "config.example.json"
    config = load_config(path)
    assert len(config.groups[0].servers) == 2


def test_empty_font_environment_and_secret_repr(monkeypatch):
    monkeypatch.setenv("L4D2_CARD_FONT_PATH", "")
    monkeypatch.setenv("L4D2_CLIENT_SECRET", "not-a-real-secret")
    settings = Settings(_env_file=None)
    assert settings.card_font_path is None
    assert "not-a-real-secret" not in repr(settings)
