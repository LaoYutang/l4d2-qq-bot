"""Strict Source-server endpoint parsing, shared by config and queries."""

import ipaddress
import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Endpoint:
    host: str
    port: int = 27015

    @property
    def address(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"{host}:{self.port}"


def parse_address(value: str) -> Endpoint:
    value = value.strip()
    if not value or len(value) > 260 or any(ch.isspace() for ch in value):
        raise ValueError("服务器地址为空、过长或包含空白")
    host, port_text = value, "27015"
    if value.startswith("["):
        match = re.fullmatch(r"\[([^]]+)\](?::([0-9]+))?", value)
        if not match:
            raise ValueError("IPv6 地址应使用 [地址]:端口 格式")
        host, port_text = match[1], match[2] or "27015"
    elif value.count(":") == 1:
        host, port_text = value.rsplit(":", 1)
    elif ":" in value:
        raise ValueError("IPv6 地址应使用 [地址]:端口 格式")
    if not port_text.isascii() or not port_text.isdigit() or not 1 <= int(port_text) <= 65535:
        raise ValueError("服务器端口必须在 1–65535 范围内")
    try:
        host = str(ipaddress.ip_address(host))
    except ValueError:
        host = host.rstrip(".").lower()
        labels = host.split(".")
        if (
            not host
            or len(host) > 253
            or not all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
            )
        ):
            raise ValueError("服务器地址必须是 IP 或合法域名，不含协议或路径") from None
    return Endpoint(host, int(port_text))


def normalize_name(value: str) -> str:
    return "".join(value.split()).casefold()
