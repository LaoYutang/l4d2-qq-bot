"""State transitions shared by the live gateway and offline tests."""

import math
from dataclasses import dataclass
from enum import IntEnum
from typing import Any

GROUP_AND_C2C_INTENT = 1 << 25


class OpCode(IntEnum):
    DISPATCH = 0
    HEARTBEAT = 1
    IDENTIFY = 2
    RESUME = 6
    RECONNECT = 7
    INVALID_SESSION = 9
    HELLO = 10
    HEARTBEAT_ACK = 11


class HeartbeatTimeout(ConnectionError):
    pass


@dataclass(slots=True)
class GatewayState:
    sequence: int | None = None
    session_id: str = ""
    heartbeat_interval: float = 0
    awaiting_ack: bool = False

    def accept(self, payload: dict[str, Any]) -> str:
        op = payload.get("op")
        if op == OpCode.HELLO:
            interval = float(payload["d"]["heartbeat_interval"]) / 1000
            if not math.isfinite(interval) or interval <= 0:
                raise ValueError("无效的网关心跳周期")
            self.heartbeat_interval = interval
            self.awaiting_ack = False
            return "hello"
        if op == OpCode.HEARTBEAT_ACK:
            self.awaiting_ack = False
            return "ack"
        if op == OpCode.HEARTBEAT:
            return "heartbeat_requested"
        if op == OpCode.INVALID_SESSION:
            self.sequence, self.session_id, self.awaiting_ack = None, "", False
            return "identify"
        if op == OpCode.RECONNECT:
            return "reconnect"
        if op == OpCode.DISPATCH:
            sequence = payload.get("s")
            if type(sequence) is int:
                self.sequence = sequence
            if payload.get("t") == "READY":
                session_id = payload["d"].get("session_id")
                if not isinstance(session_id, str) or not session_id:
                    raise ValueError("QQ WS READY 缺少 session_id")
                self.session_id = session_id
                return "ready"
            return "dispatch"
        return "ignore"

    def authentication(self, access_token: str) -> dict[str, Any]:
        if self.session_id and self.sequence is not None:
            return {
                "op": OpCode.RESUME,
                "d": {
                    "token": f"QQBot {access_token}",
                    "session_id": self.session_id,
                    "seq": self.sequence,
                },
            }
        return {
            "op": OpCode.IDENTIFY,
            "d": {
                "token": f"QQBot {access_token}",
                "intents": GROUP_AND_C2C_INTENT,
                "shard": [0, 1],
            },
        }

    def heartbeat(self) -> dict[str, Any]:
        if self.awaiting_ack:
            raise HeartbeatTimeout("上次心跳未收到 ACK，应关闭连接后重连")
        self.awaiting_ack = True
        return {"op": OpCode.HEARTBEAT, "d": self.sequence}


@dataclass(frozen=True, slots=True)
class GroupMessage:
    message_id: str
    group_openid: str
    member_openid: str
    content: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "GroupMessage | None":
        if payload.get("op") != OpCode.DISPATCH or payload.get("t") not in {
            "GROUP_AT_MESSAGE_CREATE",
            "GROUP_MESSAGE_CREATE",
        }:
            return None
        data = payload.get("d")
        if not isinstance(data, dict) or not isinstance(data.get("author"), dict):
            return None
        author = data["author"]
        fields = [data.get("id"), data.get("group_openid"), author.get("member_openid")]
        content = data.get("content")
        if (
            author.get("bot")
            or not all(isinstance(value, str) and value for value in fields)
            or not isinstance(content, str)
            or len(content) > 4096
        ):
            return None
        return cls(*fields, content)
