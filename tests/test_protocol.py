import copy

import pytest

from l4d2_qq_bot.app import route_event
from l4d2_qq_bot.demo import sample_config, sample_event
from l4d2_qq_bot.qq.protocol import GatewayState, GroupMessage, HeartbeatTimeout, OpCode


def test_identify_ready_resume_and_invalidation():
    state = GatewayState()
    assert state.accept({"op": 10, "d": {"heartbeat_interval": 45000}}) == "hello"
    identify = state.authentication("fake-token")
    assert identify["op"] == OpCode.IDENTIFY
    assert identify["d"]["intents"] == 1 << 25
    assert state.heartbeat_interval == 45
    state.accept({"op": 0, "s": 1, "t": "READY", "d": {"session_id": "session"}})
    state.accept({"op": 0, "s": 42, "t": "GROUP_AT_MESSAGE_CREATE", "d": {}})
    assert state.heartbeat()["d"] == 42
    state.accept({"op": 11})
    assert state.accept({"op": 7}) == "reconnect"
    resume = state.authentication("refreshed-token")
    assert resume["op"] == OpCode.RESUME
    assert resume["d"]["seq"] == 42
    assert resume["d"]["token"] == "QQBot refreshed-token"
    state.accept({"op": 9, "d": False})
    assert state.authentication("token")["op"] == OpCode.IDENTIFY


def test_missing_ack_detected():
    state = GatewayState()
    assert state.heartbeat()["d"] is None
    with pytest.raises(HeartbeatTimeout):
        state.heartbeat()
    state.accept({"op": 11})
    state.heartbeat()


@pytest.mark.parametrize("interval", [0, -1, float("inf"), float("nan")])
def test_invalid_hello_interval(interval):
    with pytest.raises(ValueError):
        GatewayState().accept({"op": 10, "d": {"heartbeat_interval": interval}})


def test_reply_uses_message_id_not_envelope_id():
    routed = route_event(sample_event(), sample_config())
    assert routed is not None
    assert routed.message.message_id == "DEMO_MESSAGE_ID"
    assert routed.message.member_openid == "DEMO_MEMBER_OPENID"


@pytest.mark.parametrize("field", ["id", "group_openid", "author"])
def test_malformed_group_event_not_routed(field):
    event = copy.deepcopy(sample_event())
    del event["d"][field]
    assert GroupMessage.from_payload(event) is None


def test_bot_messages_ignored():
    event = sample_event()
    event["d"]["author"]["bot"] = True
    assert route_event(event, sample_config()) is None
