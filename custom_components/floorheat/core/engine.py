"""The control step (docs/design.md §3.2, §3.3, §3.5, §3.6, §5.3).

`step(config, state, inputs, now) -> (outputs, new_state, events)` is pure and
deterministic. One call reaches a fixed point: calling it again with its own new state,
the same inputs and the same `now` returns the same result, so the reconcile loop may
run it as often as it likes.

Order within a step:
1. heat source transitions from the actual switch state (D-66, D-78, D-91);
2. readings and sensor fault (§3.6, D-88, D-93);
3. zone transitions (§3.3 rules 1 to 4 and 6);
4. sync rule (rule 5), request with min ON/OFF (§3.5), calling zone (D-65, D-92);
5. valves and reason texts (D-20, D-27, D-64, D-71, D-89).

Season OFF and notification events follow in P3, schedules and holiday in P9.
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from .config import CoreConfig, GlobalParams, ZoneConfig, ZoneParams
from .io import Event, Inputs, Outputs, ZoneInput, ZoneReport
from .state import CoreState, ZoneMode, ZoneState

# Absorbs float noise in comparisons such as "RoomTemp at or below StartTemp"
# (readings have 0.01 °C resolution at best).
_EPS = 1e-6
_ZERO = timedelta(0)

_IDLE = ZoneMode.IDLE
_WAITING = ZoneMode.WAITING
_HEATING = ZoneMode.HEATING
_FAULT = ZoneMode.SENSOR_FAULT


@dataclass(frozen=True)
class _Zone:
    """One zone's temperatures during a step."""

    config: ZoneConfig
    params: ZoneParams
    setpoint: float  # effective SetPoint (§3.4; only BaseSetPoint until P9)
    room: float | None  # RoomTemp; None while there is no valid reading

    @property
    def start_temp(self) -> float:
        return self.setpoint - self.params.hysteresis

    @property
    def stop_temp(self) -> float:
        return self.setpoint + self.params.hysteresis

    def needs_heat(self) -> bool:
        """RoomTemp at or below StartTemp."""
        return self.room is not None and self.room <= self.start_temp + _EPS

    def satisfied(self) -> bool:
        """RoomTemp at or above StopTemp."""
        return self.room is not None and self.room >= self.stop_temp - _EPS


@dataclass(frozen=True)
class _Request:
    on: bool
    spreading: bool  # all zones satisfied before HpMinOnTime (D-20)
    held: bool  # demand held back by HpMinOffTime (D-64)


def step(
    config: CoreConfig, state: CoreState, inputs: Inputs, now: datetime
) -> tuple[Outputs, CoreState, list[Event]]:
    """Compute the desired outputs and the next state (§5.3)."""
    _check(config, inputs, now)
    params = inputs.global_params
    running = inputs.heat_source.is_on
    last_on, last_off = _heat_source_times(state, running, now)
    min_on_left = last_on + params.hp_min_on_time - now if running and last_on else _ZERO
    min_off_left = last_off + params.hp_min_off_time - now if not running and last_off else _ZERO

    zones: dict[str, _Zone] = {}
    zone_states: dict[str, ZoneState] = {}
    for zone_config in config.zones:
        zone_params = inputs.zone_params[zone_config.id]
        zone_state, room, timed_out = _read_sensor(
            config,
            zone_config,
            state.zones.get(zone_config.id, ZoneState()),
            inputs.zones[zone_config.id],
            params,
            now,
        )
        zone = _Zone(zone_config, zone_params, zone_params.base_setpoint, room)
        zones[zone_config.id] = zone
        zone_states[zone_config.id] = _transition(zone_state, zone, timed_out, running, now)

    # A calling zone only exists while the request is ON, so it continues the cycle.
    calling = state.calling_zone if state.calling_zone in zones else None
    zone_states, sync_fired = _sync(calling, state.sync_fired, zones, zone_states)
    request = _request(zone_states, running, min_on_left, min_off_left)
    if not request.on:
        calling, sync_fired = None, False  # the cycle is over (or held by min OFF)
    elif calling is None:
        calling = _choose_calling_zone(zones, zone_states)
        zone_states, sync_fired = _sync(calling, sync_fired, zones, zone_states)
        request = _request(zone_states, running, min_on_left, min_off_left)

    valves = {
        zone_id: _valve(zone_states[zone_id], zone, running, request, params)
        for zone_id, zone in zones.items()
    }
    reports = {
        zone_id: ZoneReport(
            reason=_reason(
                zone_id,
                zone_states[zone_id],
                zone,
                valves[zone_id],
                calling,
                request,
                min_on_left,
                min_off_left,
                now,
            ),
            room_temp=zone.room,
            setpoint=zone.setpoint,
        )
        for zone_id, zone in zones.items()
    }
    outputs = Outputs(
        heat_source_on=request.on,
        valves={z.id: valves[z.id] for z in config.zones if z.has_valve},  # rule 8
        zones=reports,
    )
    new_state = dataclasses.replace(
        state,
        zones=zone_states,
        hp_actual_on=running,
        hp_last_on_at=last_on,
        hp_last_off_at=last_off,
        calling_zone=calling,
        sync_fired=sync_fired,
    )
    return outputs, new_state, []


def _check(config: CoreConfig, inputs: Inputs, now: datetime) -> None:
    if now.utcoffset() is None:
        raise ValueError("now must carry a time zone")
    missing = [
        zone_id
        for zone_id in config.zone_ids
        if zone_id not in inputs.zones or zone_id not in inputs.zone_params
    ]
    if missing:
        raise ValueError(f"inputs lack zones: {', '.join(missing)}")
    for zone_id, zone_input in inputs.zones.items():
        reported = zone_input.last_reported
        if reported is not None and reported.utcoffset() is None:
            raise ValueError(f"{zone_id}: last_reported must carry a time zone")


def _heat_source_times(
    state: CoreState, running: bool, now: datetime
) -> tuple[datetime | None, datetime | None]:
    """Last actual ON/OFF transitions (D-66).

    A switch seen ON for the first time (first start, D-91) counts as switched ON now;
    one seen OFF for the first time leaves the OFF time unknown, so no min OFF (D-78).
    """
    last_on, last_off = state.hp_last_on_at, state.hp_last_off_at
    if running and not (state.hp_actual_on and last_on):
        last_on = now
    if not running and state.hp_actual_on:
        last_off = now
    return last_on, last_off


def _read_sensor(
    config: CoreConfig,
    zone_config: ZoneConfig,
    zone_state: ZoneState,
    zone_input: ZoneInput,
    params: GlobalParams,
    now: datetime,
) -> tuple[ZoneState, float | None, bool]:
    """Update the last valid reading; return the state, RoomTemp and whether the
    sensor has timed out (§3.6)."""
    reading, reported = zone_input.reading, zone_input.last_reported
    if (
        reading is not None
        and reported is not None
        and config.plausible_min <= reading <= config.plausible_max  # raw value (D-88)
    ):
        reported = min(reported, now)
        if zone_state.last_valid_at is None or reported >= zone_state.last_valid_at:
            zone_state = dataclasses.replace(
                zone_state, last_valid_value=reading, last_valid_at=reported
            )
    last_at, last_value = zone_state.last_valid_at, zone_state.last_valid_value
    if last_at is None or last_value is None:
        since = zone_state.awaiting_reading_since or now  # D-93
        zone_state = dataclasses.replace(zone_state, awaiting_reading_since=since)
        return zone_state, None, now - since > params.sensor_fault_timeout
    zone_state = dataclasses.replace(zone_state, awaiting_reading_since=None)
    if now - last_at > params.sensor_fault_timeout:
        return zone_state, None, True
    return zone_state, last_value + zone_config.sensor_offset, False


def _set_mode(zone_state: ZoneState, mode: ZoneMode) -> ZoneState:
    return dataclasses.replace(zone_state, mode=mode, wait_started_at=None)


def _transition(
    zone_state: ZoneState, zone: _Zone, timed_out: bool, running: bool, now: datetime
) -> ZoneState:
    """§3.3 rules 1 to 4 and 6 (and D-94) for one zone, plus the sensor fault (§3.6)."""
    previous = zone_state.last_setpoint
    raised = previous is not None and zone.setpoint > previous + _EPS
    lowered = previous is not None and zone.setpoint < previous - _EPS
    zone_state = dataclasses.replace(zone_state, last_setpoint=zone.setpoint)
    if timed_out:
        if zone_state.mode is _FAULT:
            return zone_state
        return dataclasses.replace(_set_mode(zone_state, _FAULT), fault_since=now)
    if zone_state.mode not in (_IDLE, _WAITING, _HEATING):
        # Back from a fault. FORCED only comes with schedules (P9) and is IDLE until then.
        zone_state = dataclasses.replace(zone_state, mode=_IDLE, fault_since=None)
    if zone.room is None:  # no reading yet (D-93): no demand
        return _set_mode(zone_state, _IDLE)
    if zone_state.mode is _HEATING:
        return _set_mode(zone_state, _IDLE) if zone.satisfied() else zone_state  # rule 6
    if zone.needs_heat() and (running or raised):  # rules 3 and 4: no WaitTime
        return _set_mode(zone_state, _HEATING)
    if zone_state.mode is _WAITING and lowered and not zone.needs_heat():
        return _set_mode(zone_state, _IDLE)  # SetPoint lowered: the wait ends (D-94)
    if zone_state.mode is _IDLE:
        if not zone.needs_heat():
            return zone_state
        zone_state = dataclasses.replace(zone_state, mode=_WAITING, wait_started_at=now)  # rule 1
    started = zone_state.wait_started_at or now
    zone_state = dataclasses.replace(zone_state, wait_started_at=started)
    if now - started < zone.params.wait_time:
        return zone_state
    return _set_mode(zone_state, _HEATING if zone.needs_heat() else _IDLE)  # rule 2 (D-05)


def _sync(
    calling: str | None,
    sync_fired: bool,
    zones: Mapping[str, _Zone],
    zone_states: dict[str, ZoneState],
) -> tuple[dict[str, ZoneState], bool]:
    """Rule 5: when the calling zone reaches SetPoint, the other zones top up (once)."""
    if calling is None or sync_fired:
        return zone_states, sync_fired
    caller = zones[calling]
    reached = (
        zone_states[calling].mode is _FAULT  # a faulty calling zone counts as reached (D-28)
        or caller.room is None
        or caller.room >= caller.setpoint - _EPS
    )
    if not reached:
        return zone_states, False
    joined = dict(zone_states)
    for zone_id, zone in zones.items():
        zone_state = zone_states[zone_id]
        if (
            zone_state.mode in (_IDLE, _WAITING)
            and zone.room is not None
            and zone.room < zone.stop_temp - _EPS
        ):
            joined[zone_id] = _set_mode(zone_state, _HEATING)
    return joined, True


def _request(
    zone_states: Mapping[str, ZoneState],
    running: bool,
    min_on_left: timedelta,
    min_off_left: timedelta,
) -> _Request:
    """Rule 7 with the heat pump protection of §3.5."""
    demand = any(zone_state.mode is _HEATING for zone_state in zone_states.values())
    if running:
        if demand:
            return _Request(on=True, spreading=False, held=False)
        spreading = min_on_left > _ZERO  # D-20
        return _Request(on=spreading, spreading=spreading, held=False)
    held = demand and min_off_left > _ZERO  # D-64, D-39
    return _Request(on=demand and not held, spreading=False, held=held)


def _choose_calling_zone(
    zones: Mapping[str, _Zone], zone_states: Mapping[str, ZoneState]
) -> str | None:
    """The heating zone with the largest StartTemp - RoomTemp; ties by YAML order (D-65).

    Also used when the request is ON without a calling zone (D-92).
    """
    best: str | None = None
    best_deficit = -math.inf
    for zone_id, zone in zones.items():  # YAML order
        if zone_states[zone_id].mode is not _HEATING or zone.room is None:
            continue
        deficit = round(zone.start_temp - zone.room, 6)
        if deficit > best_deficit:
            best, best_deficit = zone_id, deficit
    return best


def _valve(
    zone_state: ZoneState, zone: _Zone, running: bool, request: _Request, params: GlobalParams
) -> bool:
    if zone_state.mode is _HEATING:
        return True  # also while held by min OFF (D-64)
    if zone_state.mode is _FAULT:
        return running  # follows the house (D-27)
    return (
        request.spreading
        and zone.room is not None
        and zone.room < params.manual_max_temp - _EPS  # D-71
    )


def _minutes(left: timedelta) -> int:
    return max(0, math.ceil(left / timedelta(minutes=1)))


def _reason(
    zone_id: str,
    zone_state: ZoneState,
    zone: _Zone,
    valve: bool,
    calling: str | None,
    request: _Request,
    min_on_left: timedelta,
    min_off_left: timedelta,
    now: datetime,
) -> str:
    """Reason text for the zone's reason sensor (D-89)."""
    if zone_state.mode is _FAULT:
        return "Sensor fault, following the heat pump"
    if zone.room is None:
        return "Waiting for a sensor reading"
    if zone_state.mode is _HEATING:
        if request.held:
            return f"Held by min OFF, {_minutes(min_off_left)} min left"
        return "Calling zone" if zone_id == calling else "Heating"
    if request.spreading:
        # Without a valve, water flows through the zone whenever the HP runs.
        if valve or not zone.config.has_valve:
            return f"Spreading heat (min ON), {_minutes(min_on_left)} min left"
        return "Idle, at or above ManualMaxTemp"
    if zone_state.mode is _WAITING and zone_state.wait_started_at is not None:
        left = zone_state.wait_started_at + zone.params.wait_time - now
        return f"Waiting, {_minutes(left)} min left"
    return "Idle"
