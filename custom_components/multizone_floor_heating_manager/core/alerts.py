"""Notification events of the step function (docs/design.md §3.6, §3.9).

- Sensor fault started / recovered: on a mode change between the incoming and the new
  state, in and outside the heating season and in shadow mode (D-75, D-98). A fault
  already in the stored state is therefore not notified again after a restart.
- Daily reminder: one event per local day while a zone is faulty since an earlier local
  day, from SensorFaultReminder until midnight, only in the heating season (D-75, D-98).
- Output mismatch: counted on reconcile ticks only, at most once per `now`; alert once
  after `output_mismatch_alert` ticks, then a recovery event; inactive in shadow mode
  (D-67, D-99).
- Failsafe started / ended: on the change of `failsafe_active`, so a failsafe already in
  the stored state is not notified again after a restart; also in shadow mode (D-148).
- Long run alarm: once when the heat source has been ON for longer than LongRunAlarm,
  then once when it is OFF again; an unavailable switch ends nothing (D-95, D-150).
- Active alerts (the alerts sensor): derived from the state, not stored (`active_alerts`).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime, timedelta, tzinfo

from .config import CoreConfig, GlobalParams
from .io import Event, EventKind, Inputs, Outputs, OutputState
from .state import CoreState, OutputTracking, ZoneMode, ZoneState

_FAULT = ZoneMode.SENSOR_FAULT


def fault_events(
    config: CoreConfig,
    old_zones: Mapping[str, ZoneState],
    new_zones: Mapping[str, ZoneState],
    params: GlobalParams,
    season: bool,
    last_reminder_on: date | None,
    now: datetime,
    time_zone: tzinfo,
) -> tuple[list[Event], date | None]:
    """Fault start / recovery events and the daily reminder.

    Returns the events and the local date of the last reminder sent.
    """
    events: list[Event] = []
    for zone in config.zones:
        old = old_zones.get(zone.id, ZoneState())
        new = new_zones[zone.id]
        if new.mode is _FAULT and old.mode is not _FAULT:
            minutes = math.ceil(params.sensor_fault_timeout / timedelta(minutes=1))
            events.append(
                Event(
                    kind=EventKind.SENSOR_FAULT_STARTED,
                    message=(
                        f"Sensor fault in {zone.name}: no valid reading for more than "
                        f"{minutes} min. The zone follows the heat source and creates no demand."
                    ),
                    zone_id=zone.id,
                    data={"last_valid_at": _iso(new.last_valid_at)},
                )
            )
        elif old.mode is _FAULT and new.mode is not _FAULT:
            events.append(
                Event(
                    kind=EventKind.SENSOR_FAULT_RECOVERED,
                    message=(
                        f"Sensor in {zone.name} reports again; the zone is back to normal control."
                    ),
                    zone_id=zone.id,
                )
            )

    today = now.astimezone(time_zone).date()
    due = now >= datetime.combine(today, params.sensor_fault_reminder, tzinfo=time_zone)
    if not season or not due or last_reminder_on == today:
        return events, last_reminder_on
    reminded = [
        zone
        for zone in config.zones
        if new_zones[zone.id].mode is _FAULT
        and _local_date(new_zones[zone.id].fault_since, time_zone, today) < today
    ]
    if not reminded:
        return events, last_reminder_on
    events.append(
        Event(
            kind=EventKind.SENSOR_FAULT_REMINDER,
            message=f"Sensor fault still active in: {', '.join(z.name for z in reminded)}.",
            data={"zone_ids": ", ".join(z.id for z in reminded)},
        )
    )
    return events, today


def failsafe_events(
    was_active: bool,
    active: bool,
    season: bool,
    params: GlobalParams,
) -> list[Event]:
    """Failsafe started / ended (D-148)."""
    if active == was_active:
        return []
    if active:
        hours = math.ceil(params.failsafe_trigger / timedelta(hours=1))
        start, end = params.failsafe_window_start, params.failsafe_window_end
        window = f"{start:%H:%M}\u2013{end:%H:%M}"  # en dash
        return [
            Event(
                kind=EventKind.FAILSAFE_STARTED,
                message=(
                    f"Failsafe started: no sensor has sent a valid reading for more than "
                    f"{hours} h. Every valve opens and the heat source runs daily {window} "
                    "until a sensor reports again."
                ),
            )
        ]
    if not season:
        message = "Failsafe ended: the heating season was switched off."
    else:
        message = "Failsafe ended: a sensor reports again; normal control resumes."
    return [Event(kind=EventKind.FAILSAFE_ENDED, message=message)]


def long_run_events(
    alerted: bool,
    running: bool,
    available: bool,
    last_on: datetime | None,
    params: GlobalParams,
    now: datetime,
    time_zone: tzinfo,
) -> tuple[bool, list[Event]]:
    """The long run alarm (D-150); returns whether it is alerted and the events."""
    if running and last_on is not None and not alerted and now - last_on > params.long_run_alarm:
        hours = math.ceil(params.long_run_alarm / timedelta(hours=1))
        since = last_on.astimezone(time_zone)
        return True, [
            Event(
                kind=EventKind.LONG_RUN,
                message=(
                    f"The heat source has been running for more than {hours} h "
                    f"(since {since:%a %H:%M})."
                ),
                data={"on_since": last_on.isoformat()},
            )
        ]
    if alerted and available and not running:
        return False, [
            Event(
                kind=EventKind.LONG_RUN_ENDED,
                message="The heat source is OFF again after its long run.",
            )
        ]
    return alerted, []


def active_alerts(config: CoreConfig, state: CoreState) -> list[Event]:
    """Alerts active in `state`: the failsafe, the heat source, then per zone in YAML
    order."""
    alerts: list[Event] = []
    if state.failsafe_active:
        alerts.append(
            Event(
                kind=EventKind.FAILSAFE_STARTED,
                message="Failsafe: no sensor sends a valid reading.",
            )
        )
    if state.long_run_alerted:
        alerts.append(
            Event(
                kind=EventKind.LONG_RUN,
                message="The heat source runs longer than the long run alarm.",
                data={"on_since": _iso(state.hp_last_on_at)},
            )
        )
    if state.heat_source_output.alerted:
        alerts.append(
            Event(
                kind=EventKind.OUTPUT_MISMATCH,
                message="The heat source switch does not follow its command.",
                data={"output": "heat_source"},
            )
        )
    for zone in config.zones:
        zone_state = state.zones.get(zone.id)
        if zone_state is None:
            continue
        if zone_state.mode is _FAULT:
            alerts.append(
                Event(
                    kind=EventKind.SENSOR_FAULT_STARTED,
                    message=f"Sensor fault in {zone.name}.",
                    zone_id=zone.id,
                    data={"since": _iso(zone_state.fault_since)},
                )
            )
        if zone_state.valve_output.alerted:
            alerts.append(
                Event(
                    kind=EventKind.OUTPUT_MISMATCH,
                    message=f"The valve of {zone.name} does not follow its command.",
                    zone_id=zone.id,
                    data={"output": "valve"},
                )
            )
    return alerts


def track_outputs(
    config: CoreConfig, state: CoreState, inputs: Inputs, outputs: Outputs, now: datetime
) -> tuple[OutputTracking, dict[str, OutputTracking], datetime | None, list[Event]]:
    """Mismatch tracking of the heat source and every valve (D-67, D-99).

    Returns the heat source tracking, the valve tracking per valved zone, the time of
    the last counted tick and the events.
    """
    valved = [zone for zone in config.zones if zone.has_valve]
    old_valves = {zone.id: state.zones.get(zone.id, ZoneState()).valve_output for zone in valved}
    if not inputs.control_active:  # shadow mode: inactive, reset without events
        return OutputTracking(), dict.fromkeys(old_valves, OutputTracking()), None, []
    last_tick = state.reconcile_tick_at
    if not inputs.reconcile_tick or (last_tick is not None and now <= last_tick):
        return state.heat_source_output, old_valves, last_tick, []

    limit = config.output_mismatch_alert
    events: list[Event] = []
    heat_source, kind = _track(
        state.heat_source_output, outputs.heat_source_on, inputs.heat_source, limit
    )
    if kind is not None:
        events.append(
            _output_event(
                kind,
                "The heat source switch",
                "heat_source",
                None,
                heat_source,
                inputs.heat_source,
                limit,
            )
        )
    valves: dict[str, OutputTracking] = {}
    for zone in valved:
        actual = inputs.zones[zone.id].valve
        assert actual is not None  # checked by `step`
        valves[zone.id], kind = _track(old_valves[zone.id], outputs.valves[zone.id], actual, limit)
        if kind is not None:
            events.append(
                _output_event(
                    kind,
                    f"The valve of {zone.name}",
                    "valve",
                    zone.id,
                    valves[zone.id],
                    actual,
                    limit,
                )
            )
    return heat_source, valves, now, events


def _track(
    tracking: OutputTracking, desired: bool, actual: OutputState, limit: int
) -> tuple[OutputTracking, EventKind | None]:
    """One reconcile tick for one output."""
    if actual is not OutputState.UNAVAILABLE and actual.is_on == desired:
        kind = EventKind.OUTPUT_MISMATCH_RECOVERED if tracking.alerted else None
        return OutputTracking(last_desired=desired), kind
    if actual is OutputState.UNAVAILABLE or tracking.last_desired == desired:
        count = min(tracking.mismatch_count + 1, limit)
    else:
        count = 0  # a new command: it has not had a full interval yet
    alert = count >= limit and not tracking.alerted
    tracking = OutputTracking(count, tracking.alerted or alert, desired)
    return tracking, EventKind.OUTPUT_MISMATCH if alert else None


def _output_event(
    kind: EventKind,
    name: str,
    output: str,
    zone_id: str | None,
    tracking: OutputTracking,
    actual: OutputState,
    limit: int,
) -> Event:
    if kind is EventKind.OUTPUT_MISMATCH_RECOVERED:
        return Event(
            kind=kind,
            message=f"{name} follows its command again.",
            zone_id=zone_id,
            data={"output": output},
        )
    desired = bool(tracking.last_desired)
    shown = "unavailable" if actual is OutputState.UNAVAILABLE else actual.value.upper()
    return Event(
        kind=kind,
        message=(
            f"{name} does not follow its command: it should be {'ON' if desired else 'OFF'} "
            f"but is {shown} ({limit} reconcile intervals)."
        ),
        zone_id=zone_id,
        data={"output": output, "desired": desired, "actual": actual.value},
    )


def _local_date(value: datetime | None, time_zone: tzinfo, today: date) -> date:
    """Local date of `value`; unknown counts as an earlier day."""
    if value is None:
        return today - timedelta(days=1)
    return value.astimezone(time_zone).date()


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()
