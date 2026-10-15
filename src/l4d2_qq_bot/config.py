"""Business configuration is separate from application credentials."""

import json
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .address import normalize_name, parse_address


class ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


def base_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("请填写不含凭据、查询参数和片段的 HTTP(S) 基础 URL")
    try:
        _ = parsed.port
    except ValueError:
        raise ValueError("URL 端口无效") from None
    return value.rstrip("/")


class ServerConfig(ConfigModel):
    name: str = Field(
        min_length=1, max_length=80, description="查询快捷名；显示名称由真实服务器返回"
    )
    address: str
    aliases: list[str] = Field(default_factory=list, max_length=20)
    connect_url: str = Field(default="", max_length=2048)
    hh_channel_id: str = ""
    rcon_password_env: str = ""
    panel_url: str = ""
    panel_token_env: str = ""

    @field_validator("address")
    @classmethod
    def valid_address(cls, value: str) -> str:
        return parse_address(value).address

    @field_validator("panel_url")
    @classmethod
    def valid_panel_url(cls, value: str) -> str:
        return base_url(value)

    @field_validator("connect_url")
    @classmethod
    def valid_connect_url(cls, value: str) -> str:
        if value:
            parsed = urlsplit(value)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise ValueError("连接 URL 必须是无凭据的 HTTP(S) 地址")
        return value

    @field_validator("aliases")
    @classmethod
    def valid_aliases(cls, values: list[str]) -> list[str]:
        if any(not normalize_name(value) or len(value) > 80 for value in values):
            raise ValueError("别名不能为空或超过 80 个字符")
        return values

    @field_validator("rcon_password_env", "panel_token_env")
    @classmethod
    def valid_env_reference(cls, value: str) -> str:
        if value and (not value.isidentifier() or not value.isascii()):
            raise ValueError("凭据字段应填写环境变量名，而非密码本身")
        return value


class GroupConfig(ConfigModel):
    group_openid: str = Field(min_length=1, max_length=128)
    name: str = Field(default="", max_length=80)
    admin_openids: list[str] = Field(default_factory=list)
    hh_room_id: str = ""
    servers: list[ServerConfig] = Field(default_factory=list, max_length=32)

    @model_validator(mode="after")
    def unique_names(self) -> "GroupConfig":
        used: set[str] = set()
        for server in self.servers:
            keys = [normalize_name(name) for name in [server.name, *server.aliases]]
            if len(keys) != len(set(keys)) or any(key in used for key in keys):
                raise ValueError("同一群内服务器名称与别名不能重复")
            used.update(keys)
        if any(not member for member in self.admin_openids):
            raise ValueError("管理员 OpenID 不能为空")
        if len(self.admin_openids) != len(set(self.admin_openids)):
            raise ValueError("管理员 OpenID 不能重复")
        return self


class QueryConfig(ConfigModel):
    timeout_seconds: float = Field(default=3.0, ge=0.1, le=15)
    concurrency: int = Field(default=4, ge=1, le=16)
    allow_private_connect: bool = False


class IntegrationConfig(ConfigModel):
    timeout_seconds: float = Field(default=5.0, ge=0.1, le=30)
    concurrency: int = Field(default=4, ge=1, le=16)
    workshop_api_url: str = ""
    workshop_max_items: int = Field(default=20, ge=1, le=100)

    @field_validator("workshop_api_url")
    @classmethod
    def valid_api_url(cls, value: str) -> str:
        return base_url(value)


class AppConfig(ConfigModel):
    safe_mode: bool = True
    connect_base_url: str = ""
    map_name_url: str = ""
    query: QueryConfig = Field(default_factory=QueryConfig)
    integrations: IntegrationConfig = Field(default_factory=IntegrationConfig)
    groups: list[GroupConfig] = Field(default_factory=list, max_length=100)

    @field_validator("connect_base_url", "map_name_url")
    @classmethod
    def valid_base_url(cls, value: str) -> str:
        return base_url(value)

    @model_validator(mode="after")
    def unique_groups(self) -> "AppConfig":
        openids = [group.group_openid for group in self.groups]
        if len(openids) != len(set(openids)):
            raise ValueError("群 OpenID 不能重复")
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="L4D2_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_id: str = ""
    client_secret: SecretStr = SecretStr("")
    api_base: str = "https://api.bot.qq.com"
    token_url: str = "https://api.bot.qq.com/app/getAppAccessToken"
    config_path: Path = Path("config.json")
    card_font_path: Path | None = None
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    heybox_bot_id: str = ""
    heybox_token: SecretStr = SecretStr("")
    http_timeout_seconds: float = Field(default=20, ge=1, le=60)
    command_timeout_seconds: float = Field(default=120, ge=5, le=180)
    worker_count: int = Field(default=2, ge=1, le=8)
    event_queue_size: int = Field(default=100, ge=1, le=500)
    reply_format: Literal["markdown", "image"] = "markdown"
    connect_buttons: bool = True
    discovery_path: Path = Path(".cache/qq-discovery.json")

    def require_credentials(self) -> None:
        missing = []
        if not self.app_id.strip():
            missing.append("L4D2_APP_ID")
        if not self.client_secret.get_secret_value().strip():
            missing.append("L4D2_CLIENT_SECRET")
        if missing:
            raise ValueError("请先在本地 .env 填写：" + ", ".join(missing))
        for url in (self.api_base, self.token_url):
            parsed = urlsplit(url)
            if (
                parsed.scheme != "https"
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("QQ API 与 Token 接口必须使用无凭据的 HTTPS URL")

    @field_validator("card_font_path", mode="before")
    @classmethod
    def blank_font_path(cls, value: str | Path | None) -> str | Path | None:
        return value or None


def load_config(path: Path) -> AppConfig:
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("配置文件超过 1 MiB")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return AppConfig.model_validate(data)
