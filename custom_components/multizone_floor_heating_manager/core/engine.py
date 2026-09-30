"""The control step (docs/design.md §3.2, §3.3, §3.5, §3.6, §5.3).

`step(config, state, inputs, now) -> (outputs, new_state, events)` is pure and
deterministic. One call reaches a fixed point: calling it again with its own new state,
the same inputs and the same `now` returns the same outputs and state and no events, so
the reconcile loop may run it as often as it likes and no notification is repeated.

Order within a step:
1. heat source transitions from the actual switch state (D-66, D-78, D-91, D-95);
2. readings and sensor fault (§3.6, D-88, D-93); the effective SetPoint and manual
   windows from holiday and schedules (§3.4, D-16, `schedule.zone_target`);
3. zone transitions (§3.3 rules 1 to 4 and 6, FORCED with its ManualMaxTemp cap, D-38,
   D-70, D-130); outside the heating season every zone without a fault is IDLE (D-24,
   D-97);
4. sync rule (rule 5), request with min ON/OFF (§3.5), calling zone (D-65, D-92);
   outside the season the request is OFF at once, overriding min ON (D-68);
5. valves and reason texts (D-20, D-27, D-64, D-71, D-89, D-135);
6. notification events and output mismatch tracking (`alerts`: D-67, D-75, D-98, D-99).

A forced zone creates demand but is never the calling zone (D-44, D-131). The adapter
deletes ended one-shot schedules and switches holiday off (`Outputs`, D-136).
"""

from __future__ import annotations

import dataclasses
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

from .alerts import fault_events, track_outputs
from .config import CoreConfig, GlobalParams, ZoneConfig, ZoneParams
from .io import Event, Inputs, Outputs, OutputState, Reason, ZoneInput, ZoneReport
from .schedule import ended_schedules, holiday_active, zone_target
from .state import CoreState, ZoneMode, ZoneState

# Absorbs float noise in comparisons such as "RoomTemp at or below StartTemp"
# (readings have 0.01 °C resolution at best).
_EPS = 1e-6
_ZERO = timedelta(0)

_IDLE = ZoneMode.IDLE
_WAITING = ZoneMode.WAITING
_HEATING = ZoneMode.HEATING
_FORCED = ZoneMode.FORCED
_FAULT = ZoneMode.SENSOR_FAULT


@dataclass(frozen=True)
class _Zone:
    """One zone's temperatures during a step."""

    config: ZoneConfig
    params: ZoneParams
    setpoint: float  # effective SetPoint (§3.4, D-16)
    room: float | None  # RoomTemp; None while there is no valid reading
    forced: bool = False  # a manual window runs for the zone (§3.4)
    forced_until: datetime | None = None  # end of the running manual windows (D-135)

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
class _HeatSource:
    """The heat source as seen in this step (D-66, D-95)."""

    running: bool  # actually ON; unavailable counts as OFF
    available: bool
    known_on: bool | None  # last known actual state, kept while unavailable
    last_on: datetime | None
    last_off: datetime | None
    unavailable_since: datetime | None
    off_since: datetime | None  # OFF time used for min OFF in this step
    stopped: bool  # an actual ON -> OFF transition was seen in this step


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
    season = inputs.heating_season
    source = _heat_source(state, inputs.heat_source, now)
    running, last_on, off_since = source.running, source.last_on, source.off_since
    min_on_end = last_on + params.hp_min_on_time if running and last_on else None
    min_off_end = off_since + params.hp_min_off_time if not running and off_since else None
    min_on_left = min_on_end - now if min_on_end else _ZERO
    min_off_left = min_off_end - now if min_off_end else _ZERO

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
        target = zone_target(
            zone_config.id,
            zone_params,
            inputs.schedules,
            inputs.holiday_until,
            now,
            inputs.time_zone,
        )
        zone = _Zone(
            zone_config, zone_params, target.setpoint, room, target.forced, target.forced_until
        )
        zones[zone_config.id] = zone
        zone_state = _transition(zone_state, zone, timed_out, running, params, now)
        if not season and zone_state.mode is not _FAULT:
            zone_state = _set_mode(zone_state, _IDLE)  # no demand at all (D-24, D-97)
        zone_states[zone_config.id] = zone_state

    if season:
        zone_states, request, calling, sync_fired = _cycle(
            state, source, zones, zone_states, min_on_left, min_off_left
        )
    else:  # the request goes OFF at once, even within min ON (D-68)
        request, calling, sync_fired = _Request(on=False, spreading=False, held=False), None, False

    valves = {
        zone_id: season and _valve(zone_states[zone_id], zone, running, request, params)
        for zone_id, zone in zones.items()
    }
    reports: dict[str, ZoneReport] = {}
    for zone_id, zone in zones.items():
        reason, until = _reason(
            zone_id,
            zone_states[zone_id],
            zone,
            valves[zone_id],
            calling,
            request,
            season,
            source.available,
            min_on_end,
            min_off_end,
        )
        reports[zone_id] = ZoneReport(
            reason=reason, room_temp=zone.room, setpoint=zone.setpoint, until=until
        )
    outputs = Outputs(
        heat_source_on=request.on,
        valves={z.id: valves[z.id] for z in config.zones if z.has_valve},  # rule 8
        zones=reports,
        holiday_active=holiday_active(inputs.holiday_until, now),
        ended_schedules=ended_schedules(inputs.schedules, now, inputs.time_zone),
    )
    events, reminder_on = fault_events(
        config,
        state.zones,
        zone_states,
        params,
        season,
        state.last_fault_reminder_on,
        now,
        inputs.time_zone,
    )
    heat_source_output, valve_outputs, tick_at, output_events = track_outputs(
        config, state, inputs, outputs, now
    )
    zone_states = {
        zone_id: dataclasses.replace(zone_state, valve_output=valve_outputs[zone_id])
        if zone_id in valve_outputs
        else zone_state
        for zone_id, zone_state in zone_states.items()
    }
    new_state = dataclasses.replace(
        state,
        zones=zone_states,
        hp_actual_on=source.known_on,
        hp_last_on_at=last_on,
        hp_last_off_at=source.last_off,
        hp_unavailable_since=source.unavailable_since,
        calling_zone=calling,
        sync_fired=sync_fired,
        heat_source_output=heat_source_output,
        last_fault_reminder_on=reminder_on,
        reconcile_tick_at=tick_at,
    )
    return outputs, new_state, events + output_events


def _cycle(
    state: CoreState,
    source: _HeatSource,
    zones: Mapping[str, _Zone],
    zone_states: dict[str, ZoneState],
    min_on_left: timedelta,
    min_off_left: timedelta,
) -> tuple[dict[str, ZoneState], _Request, str | None, bool]:
    """Sync rule, request and calling zone in the heating season (§3.3, §3.5)."""
    running = source.running
    # A calling zone only exists while the request is ON, so it continues the cycle.
    calling = state.calling_zone if state.calling_zone in zones else None
    if calling is not None and zone_states[calling].mode is _FORCED:
        calling = None  # a forced zone never calls; the role passes on (D-44, D-131)
    sync_fired = state.sync_fired
    if source.stopped:
        calling, sync_fired = None, False  # the heat pump stopped: the cycle is over
    if source.available:
        zone_states, sync_fired = _sync(calling, sync_fired, zones, zone_states)
    request = _request(zone_states, running, min_on_left, min_off_left)
    # While the switch is unavailable it is unknown whether the heat pump still runs:
    # the cycle stays as it is until the switch reports again (D-95).
    if source.available and not request.on:
        calling, sync_fired = None, False  # the cycle is over (or held by min OFF)
    elif source.available and calling is None:
        calling = _choose_calling_zone(zones, zone_states)
        zone_states, sync_fired = _sync(calling, sync_fired, zones, zone_states)
        request = _request(zone_states, running, min_on_left, min_off_left)
    return zone_states, request, calling, sync_fired


def _check(config: CoreConfig, inputs: Inputs, now: datetime) -> None:
    if now.utcoffset() is None:
        raise ValueError("now must carry a time zone")
    if not isinstance(inputs.time_zone, tzinfo):
        raise ValueError(f"time_zone must be a tzinfo, got {inputs.time_zone!r}")  # D-96
    missing = [
        zone_id
        for zone_id in config.zone_ids
        if zone_id not in inputs.zones or zone_id not in inputs.zone_params
    ]
    if missing:
        raise ValueError(f"inputs lack zones: {', '.join(missing)}")
    holiday_until = inputs.holiday_until
    if holiday_until is not None and holiday_until.utcoffset() is None:
        raise ValueError("holiday_until must carry a time zone")
    for zone_id, zone_input in inputs.zones.items():
        reported = zone_input.last_reported
        if reported is not None and reported.utcoffset() is None:
            raise ValueError(f"{zone_id}: last_reported must carry a time zone")
    for zone in config.zones:
        if zone.has_valve and inputs.zones[zone.id].valve is None:
            raise ValueError(f"{zone.id}: the valve state is missing")  # D-99


def _heat_source(state: CoreState, actual: OutputState, now: datetime) -> _HeatSource:
    """Actual ON/OFF transitions of the heat source (D-66, D-95).

    - Seen ON for the first time (first start, D-91): switched ON now. Seen OFF for the
      first time: the OFF time stays unknown, so no min OFF (D-78).
    - Unavailable: counts as OFF, and min OFF counts from when it became unavailable if
      it was ON. The last known state and the stored times are kept, because the switch
      may still be ON.
    - Back ON after being ON: it never stopped (a Shelly that lost power restarts OFF),
      so nothing changes. Back OFF after being ON: it stopped when it became unavailable.
    """
    known = state.hp_actual_on
    last_on, last_off = state.hp_last_on_at, state.hp_last_off_at
    if actual is OutputState.UNAVAILABLE:
        since = state.hp_unavailable_since or now
        off_since = since if known else last_off
        return _HeatSource(False, False, known, last_on, last_off, since, off_since, False)
    running = actual.is_on
    stopped = not running and known is True
    if running and not (known and last_on):
        last_on = now
    if stopped:
        last_off = state.hp_unavailable_since or now
    return _HeatSource(running, True, running, last_on, last_off, None, last_off, stopped)


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
    return dataclasses.replace(zone_state, mode=mode, wait_started_at=None, forced_capped=False)


def _transition(
    zone_state: ZoneState,
    zone: _Zone,
    timed_out: bool,
    running: bool,
    params: GlobalParams,
    now: datetime,
) -> ZoneState:
    """§3.3 rules 1 to 4 and 6 (and D-94) for one zone, the sensor fault (§3.6) and the
    manual schedule (§3.4)."""
    previous = zone_state.last_setpoint
    raised = previous is not None and zone.setpoint > previous + _EPS
    lowered = previous is not None and zone.setpoint < previous - _EPS
    zone_state = dataclasses.replace(zone_state, last_setpoint=zone.setpoint)
    if timed_out:  # a faulty zone is never forced (D-70)
        if zone_state.mode is _FAULT:
            return zone_state
        return dataclasses.replace(_set_mode(zone_state, _FAULT), fault_since=now)
    if zone_state.mode is _FAULT:  # back from a fault: IDLE, evaluated below
        zone_state = dataclasses.replace(_set_mode(zone_state, _IDLE), fault_since=None)
    if zone.room is None:  # no reading yet (D-93): no demand, not forced (D-130)
        return _set_mode(zone_state, _IDLE)
    if zone.forced:
        return _forced(zone_state, zone.room, params)
    if zone_state.mode is _FORCED:  # the manual window ended: IDLE, evaluated below (D-130)
        zone_state = _set_mode(zone_state, _IDLE)
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


def _forced(zone_state: ZoneState, room: float, params: GlobalParams) -> ZoneState:
    """FORCED with the ManualMaxTemp cap: closed and no demand at or above it, resumed
    only below ManualMaxTemp - ManualResumeDelta (§3.4, D-38)."""
    capped = zone_state.mode is _FORCED and zone_state.forced_capped
    if room >= params.manual_max_temp - _EPS:
        capped = True
    elif room < params.manual_max_temp - params.manual_resume_delta - _EPS:
        capped = False
    return dataclasses.replace(_set_mode(zone_state, _FORCED), forced_capped=capped)


def _forced_demand(zone_state: ZoneState) -> bool:
    return zone_state.mode is _FORCED and not zone_state.forced_capped


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
    """Rule 7 with the heat pump protection of §3.5; a forced zone below its cap is
    demand too (§3.4)."""
    demand = any(
        zone_state.mode is _HEATING or _forced_demand(zone_state)
        for zone_state in zone_states.values()
    )
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
    if zone_state.mode is _FORCED:
        return not zone_state.forced_capped  # capped: closed, also when spreading (D-130)
    if zone_state.mode is _FAULT:
        return running  # follows the house (D-27)
    return (
        request.spreading
        and zone.room is not None
        and zone.room < params.manual_max_temp - _EPS  # D-71
    )


def _reason(
    zone_id: str,
    zone_state: ZoneState,
    zone: _Zone,
    valve: bool,
    calling: str | None,
    request: _Request,
    season: bool,
    source_available: bool,
    min_on_end: datetime | None,
    min_off_end: datetime | None,
) -> tuple[Reason, datetime | None]:
    """Reason for the zone's reason sensor (D-89, D-126) and the end of the timer it
    names, if any (D-123). The key is fixed: it never counts down."""
    if zone_state.mode is _FAULT:
        if not season:
            return Reason.SENSOR_FAULT_SEASON_OFF, None
        return Reason.SENSOR_FAULT, None
    if not season:
        return Reason.SEASON_OFF, None
    if zone.room is None:
        return Reason.NO_READING_YET, None
    if zone_state.mode is _HEATING:
        if not source_available:
            return Reason.HEAT_SOURCE_UNAVAILABLE, None
        if request.held:
            return Reason.HELD_BY_MIN_OFF, min_off_end
        return (Reason.CALLING_ZONE if zone_id == calling else Reason.HEATING), None
    if zone_state.mode is _FORCED:
        if zone_state.forced_capped:
            return Reason.FORCED_TOO_WARM, None
        if not source_available:
            return Reason.HEAT_SOURCE_UNAVAILABLE, None
        if request.held:
            return Reason.HELD_BY_MIN_OFF, min_off_end
        return Reason.FORCED, zone.forced_until
    if request.spreading:
        # Without a valve, water flows through the zone whenever the HP runs.
        if valve or not zone.config.has_valve:
            return Reason.SPREADING_HEAT, min_on_end
        return Reason.TOO_WARM_FOR_SPREADING, None
    if zone_state.mode is _WAITING and zone_state.wait_started_at is not None:
        return Reason.WAITING, zone_state.wait_started_at + zone.params.wait_time
    return Reason.IDLE, None
