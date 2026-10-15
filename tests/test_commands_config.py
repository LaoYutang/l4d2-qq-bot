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


def test_bad_base_url_rejected():
    data = sample_config().model_dump()
    data["connect_base_url"] = "https://user:password@example.com"
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_admin_remarks_are_optional_and_follow_member_ids():
    data = sample_config().model_dump()
    group = data["groups"][0]
    group["admin_openids"] = ["fixture-admin", "fixture-other-admin"]
    group.pop("admin_remarks", None)
    assert AppConfig.model_validate(data).groups[0].admin_remarks == {}

    group["admin_remarks"] = {
        "fixture-admin": " 群主 ",
        "fixture-other-admin": "",
        "fixture-visitor": "只有备注的成员",
    }
    loaded = AppConfig.model_validate(data).groups[0]
    assert loaded.admin_openids == ["fixture-admin", "fixture-other-admin"]
    assert loaded.admin_remarks == {"fixture-admin": "群主"}

    group["admin_openids"] = ["fixture-other-admin"]
    assert AppConfig.model_validate(data).groups[0].admin_remarks == {}


def test_voice_connection_url_preserves_invitation_parameters_and_is_optional():
    data = sample_config().model_dump()
    server = data["groups"][0]["servers"][0]
    server.pop("voice_connect_url", None)
    assert AppConfig.model_validate(data).groups[0].servers[0].voice_connect_url == ""
    url = "https://voice.example.test/invite?room=fixture&channel=one#join"
    server["voice_connect_url"] = url
    assert AppConfig.model_validate(data).groups[0].servers[0].voice_connect_url == url


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://user:password@voice.example.test/invite",
        "https://voice.example.test:invalid/invite",
        "https://voice.example.test/in vite",
        "https://voice.example.test/in\nvite",
    ],
)
def test_invalid_voice_connection_url_rejected(url):
    data = sample_config().model_dump()
    data["groups"][0]["servers"][0]["voice_connect_url"] = url
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_removed_server_fields_do_not_resolve_environment_credentials(monkeypatch):
    monkeypatch.setenv("LEGACY_RCON", "fixture-env-rcon")
    monkeypatch.setenv("LEGACY_PANEL", "fixture-env-panel")
    data = sample_config().model_dump()
    server = data["groups"][0]["servers"][0]
    server.update(
        rcon_password="fixture-config-rcon",
        panel_token="fixture-config-panel",
        rcon_password_env="LEGACY_RCON",
        panel_token_env="LEGACY_PANEL",
        connect_url="https://obsolete.example/join",
    )
    loaded = AppConfig.model_validate(data).groups[0].servers[0]
    assert loaded.rcon_password == "fixture-config-rcon"
    assert loaded.panel_token == "fixture-config-panel"
    removed = {"rcon_password_env", "panel_token_env", "connect_url"}
    assert not removed.intersection(loaded.model_dump())
    server["rcon_password"] = ""
    server["panel_token"] = ""
    loaded = AppConfig.model_validate(data).groups[0].servers[0]
    assert loaded.rcon_password == "" and loaded.panel_token == ""
    server["unexpected_field"] = True
    with pytest.raises(ValidationError):
        AppConfig.model_validate(data)


def test_example_config():
    path = Path(__file__).resolve().parents[1] / "config.example.json"
    config = load_config(path)
    assert len(config.groups[0].servers) == 2


def test_removed_integration_settings_are_absent_from_schema_and_unknown_keys_still_fail():
    schema = AppConfig.model_json_schema()["$defs"]["IntegrationConfig"]["properties"]
    assert set(schema) == {"timeout_seconds", "concurrency"}
    with pytest.raises(ValidationError):
        AppConfig.model_validate({"integrations": {"unexpected_field": True}})


def test_empty_font_environment_and_secret_repr(monkeypatch):
    monkeypatch.setenv("L4D2_CARD_FONT_PATH", "")
    monkeypatch.setenv("L4D2_CLIENT_SECRET", "not-a-real-secret")
    settings = Settings(_env_file=None)
    assert settings.card_font_path is None
    assert "not-a-real-secret" not in repr(settings)
