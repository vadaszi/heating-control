"""Heartbeat evaluation (docs/design.md §5.4; D-61, D-73, D-121; scenarios S6, S7)."""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.multizone_floor_heating_manager.core.heartbeat import (
    ExpectedParams,
    FailureKind,
    HeartbeatTracking,
    ShellyRole,
    StatusError,
    heartbeat_alerts,
    param_differences,
    parse_status,
    record_failure,
    record_success,
)
from custom_components.multizone_floor_heating_manager.core.io import EventKind


def status(**changes: Any) -> dict[str, Any]:
    """A valve script's status as in docs/heartbeat-protocol.md."""
    body: dict[str, Any] = {
        "v": 1,
        "role": "valve",
        "script_version": "1.0.0",
        "running": True,
        "state": "normal",
        "heartbeat_seen": True,
        "heartbeat_age_s": 0,
        "uptime_s": 86400,
        "switches": [{"id": 0, "output": False}],
        "params": {"heartbeat_timeout_s": 18000, "check_interval_s": 60, "switch_ids": None},
    }
    body.update(changes)
    return body


# ---------------------------------------------------------------- parse_status


def test_status_is_parsed() -> None:
    parsed = parse_status(status(state="timed_out", heartbeat_age_s=18060), ShellyRole.VALVE)
    assert parsed.role is ShellyRole.VALVE
    assert parsed.state == "timed_out"
    assert parsed.heartbeat_age_s == 18060
    assert parsed.uptime_s == 86400
    assert parsed.params["heartbeat_timeout_s"] == 18000


def test_unknown_fields_are_ignored() -> None:
    parsed = parse_status(status(role="heat_source", season=True, extra=1), ShellyRole.HEAT_SOURCE)
    assert parsed.role is ShellyRole.HEAT_SOURCE


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ([1, 2], "not a JSON object"),
        (status(v=None), "no protocol version"),
        (status(v=True), "no protocol version"),
        (status(v=2), "protocol v2 is not supported"),
        (status(role="heat_source"), "runs the heat_source script, expected the valve script"),
        (status(state=None), "lacks state"),
        (status(heartbeat_age_s="0"), "lacks state"),
        (status(uptime_s=1.5), "lacks state"),
        (status(params=None), "lacks params"),
    ],
)
def test_unusable_status_is_rejected(body: object, reason: str) -> None:
    with pytest.raises(StatusError, match=reason):
        parse_status(body, ShellyRole.VALVE)


# ---------------------------------------------------------------- parameters (D-73)


def test_only_expected_params_are_compared() -> None:
    params = {"heartbeat_timeout_s": 18000, "check_interval_s": 5, "switch_ids": [0]}
    assert param_differences(params, ExpectedParams()) == []  # check interval not expected


def test_param_differences_are_described() -> None:
    expected = ExpectedParams(heartbeat_timeout_s=18000, check_interval_s=60)
    assert param_differences({"heartbeat_timeout_s": 120, "check_interval_s": 5}, expected) == [
        "heartbeat_timeout_s is 120, expected 18000",
        "check_interval_s is 5, expected 60",
    ]
    assert param_differences({}, expected) == [
        "heartbeat_timeout_s is missing, expected 18000",
        "check_interval_s is missing, expected 60",
    ]
    assert param_differences({"heartbeat_timeout_s": True, "check_interval_s": 60}, expected) == [
        "heartbeat_timeout_s is True, expected 18000"
    ]


# ---------------------------------------------------------------- S7: failures and recovery


def test_s07_alert_after_three_failures_then_recovery() -> None:
    tracking = HeartbeatTracking()
    for _ in range(2):  # a Wi-Fi hiccup never alerts (D-61)
        tracking, events = record_failure(
            tracking, "Valves 1", FailureKind.UNREACHABLE, "timeout", 3
        )
        assert events == []
    assert heartbeat_alerts("Valves 1", tracking) == []

    tracking, [event] = record_failure(tracking, "Valves 1", FailureKind.UNREACHABLE, "timeout", 3)
    assert event.kind is EventKind.WATCHDOG_FAILED
    assert event.message.startswith("Shelly Valves 1 is unreachable (timeout); 3 heartbeats")
    assert event.data == {"shelly": "Valves 1", "failure": "unreachable"}
    tracking, events = record_failure(tracking, "Valves 1", FailureKind.UNREACHABLE, "timeout", 3)
    assert events == []  # once
    [alert] = heartbeat_alerts("Valves 1", tracking)
    assert alert.message == "Shelly Valves 1 is unreachable."

    tracking, [event] = record_success(tracking, "Valves 1", [])
    assert event.kind is EventKind.WATCHDOG_RECOVERED
    assert event.message == "Shelly Valves 1 answers again; its watchdog script is running."
    assert tracking == HeartbeatTracking()
    assert heartbeat_alerts("Valves 1", tracking) == []


def test_a_good_answer_resets_the_count() -> None:
    tracking = HeartbeatTracking()
    for _ in range(2):
        tracking, _ = record_failure(tracking, "X", FailureKind.UNREACHABLE, "timeout", 3)
    tracking, events = record_success(tracking, "X", [])
    assert events == []
    tracking, events = record_failure(tracking, "X", FailureKind.UNREACHABLE, "timeout", 3)
    assert events == []
    assert tracking.fail_count == 1


@pytest.mark.parametrize(
    ("kind", "detail", "message", "alert"),
    [
        (
            FailureKind.SCRIPT_NOT_RUNNING,
            "HTTP 404",
            "The watchdog script is not running on Shelly X (HTTP 404: script stopped, or "
            "wrong script_id); 1 heartbeat in a row failed.",
            "The watchdog script is not running on Shelly X.",
        ),
        (
            FailureKind.AUTH_FAILED,
            "HTTP 401",
            "Shelly X rejects the heartbeat (HTTP 401): check the password; 1 heartbeat",
            "Shelly X rejects the heartbeat (authentication).",
        ),
        (
            FailureKind.BAD_ANSWER,
            "protocol v2 is not supported",
            "Shelly X does not answer as expected (protocol v2 is not supported); 1 heartbeat",
            "Shelly X does not answer as expected.",
        ),
    ],
)
def test_failure_messages_name_the_cause(
    kind: FailureKind, detail: str, message: str, alert: str
) -> None:
    tracking, [event] = record_failure(HeartbeatTracking(), "X", kind, detail, 1)
    assert event.message.startswith(message)
    assert [a.message for a in heartbeat_alerts("X", tracking)] == [alert]


def test_the_alert_shows_the_latest_cause() -> None:
    tracking, _ = record_failure(HeartbeatTracking(), "X", FailureKind.UNREACHABLE, "t", 1)
    tracking, events = record_failure(tracking, "X", FailureKind.SCRIPT_NOT_RUNNING, "404", 1)
    assert events == []
    [alert] = heartbeat_alerts("X", tracking)
    assert alert.data["failure"] == "script_not_running"


# ---------------------------------------------------------------- parameter alert


def test_params_alert_once_and_clear_silently() -> None:
    differences = ["heartbeat_timeout_s is 120, expected 18000"]
    tracking, [event] = record_success(HeartbeatTracking(), "X", differences)
    assert event.kind is EventKind.WATCHDOG_PARAMS_MISMATCH
    assert "heartbeat_timeout_s is 120, expected 18000" in event.message
    tracking, events = record_success(tracking, "X", differences)
    assert events == []
    [alert] = heartbeat_alerts("X", tracking)
    assert alert.kind is EventKind.WATCHDOG_PARAMS_MISMATCH

    tracking, events = record_success(tracking, "X", [])
    assert events == []  # no recovery notification (§3.6 table)
    assert heartbeat_alerts("X", tracking) == []


def test_failures_keep_the_params_alert() -> None:
    tracking, _ = record_success(HeartbeatTracking(), "X", ["differs"])
    tracking, _ = record_failure(tracking, "X", FailureKind.UNREACHABLE, "t", 3)
    assert tracking.params_alerted
    tracking, events = record_success(tracking, "X", ["differs"])
    assert events == []  # not notified again


# ---------------------------------------------------------------- persistence


def test_tracking_round_trip() -> None:
    tracking = HeartbeatTracking(3, True, FailureKind.SCRIPT_NOT_RUNNING, True)
    assert HeartbeatTracking.from_dict(tracking.to_dict()) == tracking
    assert HeartbeatTracking.from_dict(HeartbeatTracking().to_dict()) == HeartbeatTracking()


@pytest.mark.parametrize(
    "data",
    [None, "x", {"fail_count": -1, "alerted": "yes", "failure": "gone", "params_alerted": 1}],
)
def test_unusable_tracking_gets_defaults(data: object) -> None:
    assert HeartbeatTracking.from_dict(data) == HeartbeatTracking()
