"""Evaluation of the Shelly watchdog heartbeat (docs/design.md §5.4, D-61, D-73, D-121).

The adapter sends the heartbeat (protocol v1, docs/heartbeat-protocol.md) and passes in
what came back; this module decides what it means:
- `parse_status`: the status JSON of a script, checked against the protocol version and
  the role HA expects from the YAML wiring (D-120). Unknown fields are ignored (D-100).
- `param_differences`: the script's `params` against HA's expected values (D-73); only
  the parameters HA has an expectation for are compared.
- `record_failure` / `record_success`: per Shelly, consecutive failed calls. After
  `limit` failures in a row one alert names the cause; the next good answer is a
  recovery. A parameter difference is alerted once while it lasts and cleared silently.
- `heartbeat_alerts`: the alerts that are active now (the alerts sensor).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeIs

from .io import Event, EventKind

PROTOCOL_VERSION = 1


class ShellyRole(StrEnum):
    """Which watchdog script a Shelly runs; fixed by the script file (D-100)."""

    VALVE = "valve"
    HEAT_SOURCE = "heat_source"


class FailureKind(StrEnum):
    """Why a heartbeat call failed (D-61, D-121)."""

    UNREACHABLE = "unreachable"  # connection error or timeout
    SCRIPT_NOT_RUNNING = "script_not_running"  # HTTP 404: script stopped or wrong id
    AUTH_FAILED = "auth_failed"  # HTTP 401
    BAD_ANSWER = "bad_answer"  # another HTTP status, or not a usable status


class StatusError(ValueError):
    """The answer is not a usable protocol v1 status; the call counts as failed."""


@dataclass(frozen=True)
class ShellyStatus:
    """The fields of a status HA uses."""

    role: ShellyRole
    state: str  # "normal", "timed_out", "failsafe" (heat source script 1.1.0, D-153)
    heartbeat_age_s: int
    uptime_s: int
    params: Mapping[str, Any]


def _whole(value: object) -> TypeIs[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_status(body: object, role: ShellyRole) -> ShellyStatus:
    """Check a status answer; raises `StatusError` with a short reason."""
    if not isinstance(body, Mapping):
        raise StatusError("the answer is not a JSON object")
    version = body.get("v")
    if not _whole(version):
        raise StatusError("the answer has no protocol version")
    if version != PROTOCOL_VERSION:
        raise StatusError(
            f"protocol v{version} is not supported (the integration speaks v{PROTOCOL_VERSION})"
        )
    reported = body.get("role")
    if reported != role.value:
        raise StatusError(f"it runs the {reported} script, expected the {role.value} script")
    state = body.get("state")
    age = body.get("heartbeat_age_s")
    uptime = body.get("uptime_s")
    params = body.get("params")
    if not isinstance(state, str) or not _whole(age) or not _whole(uptime):
        raise StatusError("the status lacks state, heartbeat_age_s or uptime_s")
    if not isinstance(params, Mapping):
        raise StatusError("the status lacks params")
    return ShellyStatus(role, state, age, uptime, params)


@dataclass(frozen=True)
class ExpectedParams:
    """Script parameters HA expects (*config*, D-73); None = not checked."""

    heartbeat_timeout_s: int = 18000  # HeartbeatTimeout, 5 h (§4, D-60)
    check_interval_s: int | None = None


def param_differences(params: Mapping[str, Any], expected: ExpectedParams) -> list[str]:
    """Human-readable differences, e.g. "heartbeat_timeout_s is 120, expected 18000"."""
    differences: list[str] = []
    for key in ("heartbeat_timeout_s", "check_interval_s"):
        want = getattr(expected, key)
        if want is None:
            continue
        have = params.get(key)
        if have is None:
            differences.append(f"{key} is missing, expected {want}")
        elif have != want or not _whole(have):
            differences.append(f"{key} is {have}, expected {want}")
    return differences


@dataclass(frozen=True)
class HeartbeatTracking:
    """Per Shelly: failed calls in a row and which alerts were sent (persisted)."""

    fail_count: int = 0
    alerted: bool = False  # the "not answering" alert was sent
    failure: FailureKind | None = None  # cause of the last failed call
    params_alerted: bool = False  # the parameter difference was notified

    def to_dict(self) -> dict[str, Any]:
        return {
            "fail_count": self.fail_count,
            "alerted": self.alerted,
            "failure": None if self.failure is None else self.failure.value,
            "params_alerted": self.params_alerted,
        }

    @classmethod
    def from_dict(cls, data: object) -> HeartbeatTracking:
        """Parse `to_dict` output; unusable fields get their defaults."""
        if not isinstance(data, Mapping):
            return cls()
        count = data.get("fail_count")
        failure = data.get("failure")
        return cls(
            fail_count=count if _whole(count) and count >= 0 else 0,
            alerted=data.get("alerted") is True,
            failure=FailureKind(failure) if failure in set(FailureKind) else None,
            params_alerted=data.get("params_alerted") is True,
        )


def _failure_message(name: str, kind: FailureKind, detail: str, count: int) -> str:
    calls = f"{count} heartbeat{'s' if count != 1 else ''} in a row failed"
    match kind:
        case FailureKind.UNREACHABLE:
            return (
                f"Shelly {name} is unreachable ({detail}); {calls}. Without heartbeats its "
                "watchdog puts its outputs into the safe state after its timeout."
            )
        case FailureKind.SCRIPT_NOT_RUNNING:
            return (
                f"The watchdog script is not running on Shelly {name} ({detail}: script "
                f"stopped, or wrong script_id); {calls}. Without the script the device has "
                "no failsafe if Home Assistant stops."
            )
        case FailureKind.AUTH_FAILED:
            return f"Shelly {name} rejects the heartbeat ({detail}): check the password; {calls}."
    return f"Shelly {name} does not answer as expected ({detail}); {calls}."  # BAD_ANSWER


def record_failure(
    tracking: HeartbeatTracking, name: str, kind: FailureKind, detail: str, limit: int
) -> tuple[HeartbeatTracking, list[Event]]:
    """A failed call; alert once when `limit` calls in a row have failed (D-61)."""
    count = min(tracking.fail_count + 1, limit)
    alert = count >= limit and not tracking.alerted
    new = HeartbeatTracking(count, tracking.alerted or alert, kind, tracking.params_alerted)
    if not alert:
        return new, []
    event = Event(
        kind=EventKind.WATCHDOG_FAILED,
        message=_failure_message(name, kind, detail, count),
        data={"shelly": name, "failure": kind.value},
    )
    return new, [event]


def record_success(
    tracking: HeartbeatTracking, name: str, differences: list[str]
) -> tuple[HeartbeatTracking, list[Event]]:
    """A good answer: recovery if the alert was sent; the parameter check (D-73)."""
    events: list[Event] = []
    if tracking.alerted:
        events.append(
            Event(
                kind=EventKind.WATCHDOG_RECOVERED,
                message=f"Shelly {name} answers again; its watchdog script is running.",
                data={"shelly": name},
            )
        )
    if differences and not tracking.params_alerted:
        events.append(
            Event(
                kind=EventKind.WATCHDOG_PARAMS_MISMATCH,
                message=(
                    f"Shelly {name}: the watchdog script parameters differ from the integration's "
                    f"expected values: {'; '.join(differences)}. Change the script's CONFIG "
                    "block or the integration's expected values."
                ),
                data={"shelly": name},
            )
        )
    return HeartbeatTracking(params_alerted=bool(differences)), events


_ALERT_TEXTS = {
    FailureKind.UNREACHABLE: "Shelly {} is unreachable.",
    FailureKind.SCRIPT_NOT_RUNNING: "The watchdog script is not running on Shelly {}.",
    FailureKind.AUTH_FAILED: "Shelly {} rejects the heartbeat (authentication).",
    FailureKind.BAD_ANSWER: "Shelly {} does not answer as expected.",
}


def heartbeat_alerts(name: str, tracking: HeartbeatTracking) -> list[Event]:
    """The heartbeat alerts of one Shelly that are active now."""
    alerts: list[Event] = []
    if tracking.alerted:
        failure = tracking.failure or FailureKind.UNREACHABLE
        alerts.append(
            Event(
                kind=EventKind.WATCHDOG_FAILED,
                message=_ALERT_TEXTS[failure].format(name),
                data={"shelly": name, "failure": failure.value},
            )
        )
    if tracking.params_alerted:
        alerts.append(
            Event(
                kind=EventKind.WATCHDOG_PARAMS_MISMATCH,
                message=f"Shelly {name}: watchdog script parameters differ.",
                data={"shelly": name},
            )
        )
    return alerts
