"""Scenario harness for core tests: simulated time, inputs and a perfect reconcile loop.

A `Scenario` owns the config, the core state and the simulated world (sensor readings,
actual switch states). `step()` runs `step` at the current time as a reconcile tick.
Like the reconcile loop, it makes the actual switches follow the commanded state and,
when the heat source changed, steps again at the same time, so transitions are seen
without delay. Like the adapter, it deletes one-shot schedules the core reports
as ended and clears the holiday end once holiday is over. Every call checks that `step`
is idempotent: running it again on its own result changes nothing and emits no further
events. A second `step()` at the same time (after changing the world) is not a new
reconcile tick for the mismatch counter.

Times are given as local wall-clock times in the scenario's time zone (UTC by default).
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta, tzinfo

from custom_components.multizone_floor_heating_manager.core.config import (
    CoreConfig,
    GlobalParams,
    ZoneConfig,
    ZoneParams,
)
from custom_components.multizone_floor_heating_manager.core.engine import step
from custom_components.multizone_floor_heating_manager.core.io import (
    Event,
    EventKind,
    Inputs,
    Mode,
    Outputs,
    OutputState,
    ZoneInput,
)
from custom_components.multizone_floor_heating_manager.core.schedule import (
    Schedule,
    ScheduleKind,
)
from custom_components.multizone_floor_heating_manager.core.state import (
    CoreState,
    ZoneMode,
    load_state,
)

DAY = date(2026, 1, 12)  # a Monday in the heating season

ZoneRef = int | str


def zone_id(ref: ZoneRef) -> str:
    """Zones are named `zone_1`, `zone_2`, … in YAML order; tests may use the number."""
    return f"zone_{ref}" if isinstance(ref, int) else ref


def make_config(zones: int = 2, *, unvalved: Sequence[int] = ()) -> CoreConfig:
    return CoreConfig(
        zones=tuple(
            ZoneConfig(id=zone_id(i), name=f"Zone {i}", has_valve=i not in unvalved)
            for i in range(1, zones + 1)
        )
    )


def at(hhmm: str, day: date = DAY, tz: tzinfo = UTC) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm), tzinfo=tz)


class Scenario:
    def __init__(
        self,
        zones: int | CoreConfig = 2,
        *,
        unvalved: Sequence[int] = (),
        temps: float | Mapping[ZoneRef, float | None] = 22.0,
        start: str = "06:00",
        day: date = DAY,
        tz: tzinfo = UTC,
        hp_on: bool = False,
        params: GlobalParams | None = None,
        zone_params: ZoneParams | None = None,
        state: CoreState | None = None,
    ) -> None:
        self.config = (
            zones if isinstance(zones, CoreConfig) else make_config(zones, unvalved=unvalved)
        )
        ids = self.config.zone_ids
        self.tz = tz
        self.day = day
        self.now = at(start, day, tz).astimezone(UTC)  # UTC: minute steps across DST
        self.state = state if state is not None else CoreState.initial(self.config)
        self.global_params = params or GlobalParams()
        self.zone_params = {z: zone_params or ZoneParams() for z in ids}
        given = {zone_id(k): v for k, v in temps.items()} if isinstance(temps, Mapping) else {}
        default = 22.0 if isinstance(temps, Mapping) else temps
        self.readings: dict[str, float | None] = {z: given.get(z, default) for z in ids}
        self.last_reported: dict[str, datetime | None] = dict.fromkeys(ids)
        self.silent: set[str] = set()
        self.hp_actual = OutputState.ON if hp_on else OutputState.OFF
        self.hp_follows = True
        self.valves_actual = {z.id: OutputState.OFF for z in self.config.zones if z.has_valve}
        self.valves_follow = dict.fromkeys(self.valves_actual, True)
        self.heating_season = True
        self.control_active = True
        self.schedules: list[Schedule] = []
        self.holiday_on = False
        self.holiday_until: datetime | None = None
        self._next_schedule = 1
        self.outputs: Outputs | None = None
        self.events: list[Event] = []

    # ------------------------------------------------------------ world

    def temp(self, zone: ZoneRef, value: float | None) -> None:
        """Set a zone's sensor reading; the sensor reports again from now on."""
        self.readings[zone_id(zone)] = value
        self.silent.discard(zone_id(zone))

    def silence(self, zone: ZoneRef) -> None:
        """The sensor stops reporting; HA keeps the old value and `last_reported`."""
        self.silent.add(zone_id(zone))

    def set_setpoint(self, zone: ZoneRef, value: float) -> None:
        z = zone_id(zone)
        self.zone_params[z] = dataclasses.replace(self.zone_params[z], base_setpoint=value)

    def set_hp_actual(self, value: OutputState, *, follows: bool = False) -> None:
        """Override the actual heat source switch state (e.g. unavailable)."""
        self.hp_actual = value
        self.hp_follows = follows

    def set_valve_actual(self, zone: ZoneRef, value: OutputState, *, follows: bool = False) -> None:
        """Override a valve's actual state (e.g. unavailable, or ignoring commands)."""
        self.valves_actual[zone_id(zone)] = value
        self.valves_follow[zone_id(zone)] = follows

    def set_season(self, on: bool) -> None:
        self.heating_season = on

    def add_auto(
        self,
        zones: Sequence[ZoneRef] | None,
        start: str,
        end: str,
        temperature: float,
        *,
        on: date | None = None,
        weekdays: Iterable[int] | None = None,
    ) -> Schedule:
        """Add an auto schedule; `zones` None = all zones. One-shot on the start day
        unless `on` or `weekdays` is given."""
        return self._add(ScheduleKind.AUTO, zones, start, end, temperature, on, weekdays)

    def add_manual(
        self,
        zones: Sequence[ZoneRef] | None,
        start: str,
        end: str,
        *,
        on: date | None = None,
        weekdays: Iterable[int] | None = None,
    ) -> Schedule:
        return self._add(ScheduleKind.MANUAL, zones, start, end, None, on, weekdays)

    def _add(
        self,
        kind: ScheduleKind,
        zones: Sequence[ZoneRef] | None,
        start: str,
        end: str,
        temperature: float | None,
        on: date | None,
        weekdays: Iterable[int] | None,
    ) -> Schedule:
        schedule = Schedule(
            id=f"s{self._next_schedule}",
            kind=kind,
            start=time.fromisoformat(start),
            end=time.fromisoformat(end),
            zone_ids=None if zones is None else tuple(zone_id(z) for z in zones),
            on_date=None if weekdays is not None else (on or self.day),
            weekdays=frozenset(weekdays or ()),
            temperature=temperature,
        )
        self._next_schedule += 1
        self.schedules.append(schedule)
        return schedule

    def holiday(self, until: str | None, day: date | None = None) -> None:
        """Activate holiday until a local time (on the start day unless `day` is given),
        or stop it with None."""
        self.holiday_on = until is not None
        self.holiday_until = None if until is None else at(until, day or self.day, self.tz)

    def holiday_without_end(self) -> None:
        """Activate holiday with no end."""
        self.holiday_on, self.holiday_until = True, None

    def restart(self, downtime: int = 0) -> None:
        """HA restart: persist, reload through JSON, and continue after `downtime` min."""
        data = json.loads(json.dumps(self.state.to_dict()))
        self.state, warnings = load_state(data, self.config)
        assert warnings == []
        self.now += timedelta(minutes=downtime)

    # ------------------------------------------------------------ time

    def inputs(self) -> Inputs:
        for z in self.config.zone_ids:
            if z not in self.silent and self.readings[z] is not None:
                self.last_reported[z] = self.now
        return Inputs(
            zones={
                z.id: ZoneInput(
                    reading=self.readings[z.id],
                    last_reported=self.last_reported[z.id],
                    valve=self.valves_actual.get(z.id),
                )
                for z in self.config.zones
            },
            heat_source=self.hp_actual,
            zone_params=dict(self.zone_params),
            global_params=self.global_params,
            heating_season=self.heating_season,
            control_active=self.control_active,
            time_zone=self.tz,
            reconcile_tick=True,
            schedules=tuple(self.schedules),
            holiday_on=self.holiday_on,
            holiday_until=self.holiday_until,
        )

    def _step_once(self) -> Outputs:
        inputs = self.inputs()
        outputs, new_state, events = step(self.config, self.state, inputs, self.now)
        again = step(self.config, new_state, inputs, self.now)
        assert again == (outputs, new_state, []), f"step is not idempotent at {self.now}"
        self.state, self.outputs = new_state, outputs
        self.events.extend(events)
        # The adapter's bookkeeping.
        self.schedules = [s for s in self.schedules if s.id not in outputs.ended_schedules]
        if self.holiday_on and not outputs.holiday_active:
            self.holiday_on, self.holiday_until = False, None  # the end clears
        return outputs

    def step(self) -> Outputs:
        """Run the reconcile loop once at the current time."""
        for _ in range(3):
            outputs = self._step_once()
            for z, on in outputs.valves.items():
                if self.valves_follow[z]:
                    self.valves_actual[z] = OutputState.ON if on else OutputState.OFF
            desired = OutputState.ON if outputs.heat_source_on else OutputState.OFF
            if not self.hp_follows or self.hp_actual is desired:
                return outputs
            self.hp_actual = desired
        raise AssertionError(f"reconcile did not settle at {self.now}")

    def advance(self, minutes: int) -> None:
        """Step once per minute for `minutes` minutes."""
        for _ in range(minutes):
            self.now += timedelta(minutes=1)
            self.step()

    def advance_to(self, hhmm: str, day: date | None = None) -> None:
        """Advance to a local wall-clock time (on the start day unless `day` is given)."""
        target = at(hhmm, day or self.day, self.tz)
        assert target >= self.now, f"{target} is before {self.now}"
        self.advance(int((target - self.now) / timedelta(minutes=1)))

    # ------------------------------------------------------------ observations

    def _out(self) -> Outputs:
        assert self.outputs is not None, "call step() first"
        return self.outputs

    def mode(self, zone: ZoneRef) -> ZoneMode:
        return self.state.zones[zone_id(zone)].mode

    def modes(self) -> dict[str, ZoneMode]:
        return {z: s.mode for z, s in self.state.zones.items()}

    @property
    def hp(self) -> bool:
        """Commanded heat pump request."""
        return self._out().heat_source_on

    @property
    def source_status(self) -> str:
        """What the heat source does and why."""
        return self._out().heat_source_status

    @property
    def source_until(self) -> datetime | None:
        return self._out().heat_source_until

    def valve(self, zone: ZoneRef) -> bool | None:
        """Commanded valve state; None for a zone without a valve (no output)."""
        return self._out().valves.get(zone_id(zone))

    def open_valves(self) -> set[str]:
        return {z for z, on in self._out().valves.items() if on}

    def reason(self, zone: ZoneRef) -> str:
        return self._out().zones[zone_id(zone)].reason

    def until(self, zone: ZoneRef) -> datetime | None:
        """End of the timer named by the zone's reason."""
        return self._out().zones[zone_id(zone)].until

    def setpoint(self, zone: ZoneRef) -> float:
        """Effective SetPoint."""
        return self._out().zones[zone_id(zone)].setpoint

    def system_mode(self) -> Mode:
        """The mode sensor: normal / holiday / failsafe."""
        return self._out().mode

    @property
    def valve_exercise(self) -> str | None:
        """The zone whose valve is being exercised."""
        return self._out().valve_exercise

    @property
    def holiday_active(self) -> bool:
        return self._out().holiday_active

    def capped(self, zone: ZoneRef) -> bool:
        return self.state.zones[zone_id(zone)].forced_capped

    def room_temp(self, zone: ZoneRef) -> float | None:
        return self._out().zones[zone_id(zone)].room_temp

    def events_of(self, kind: EventKind) -> list[Event]:
        return [event for event in self.events if event.kind is kind]

    def local_now(self) -> datetime:
        return self.now.astimezone(self.tz)

    @property
    def calling_zone(self) -> str | None:
        return self.state.calling_zone

    @property
    def sync_fired(self) -> bool:
        return self.state.sync_fired
