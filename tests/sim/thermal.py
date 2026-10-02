"""A simple thermal model of a house for multi-day simulations of the core.

Each zone warms by `heat_gain` °C/h while it receives flow (HP running and valve open,
or no valve) and loses heat towards the outdoor temperature at `loss` per hour. This
is deliberately crude: it only has to exercise the control logic over many cycles.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from custom_components.multizone_floor_heating_manager.core.io import Event, OutputState
from custom_components.multizone_floor_heating_manager.core.state import ZoneMode

from ..core.harness import Scenario


@dataclass
class ZoneModel:
    temp: float  # true room temperature, °C
    heat_gain: float = 1.2  # °C/h while the zone receives flow
    loss: float = 0.02  # per hour, towards `outdoor`
    outdoor: float = 5.0

    def advance(self, minutes: float, flow: bool) -> None:
        rate = (self.heat_gain if flow else 0.0) - self.loss * (self.temp - self.outdoor)
        self.temp += rate * minutes / 60


@dataclass(frozen=True)
class Window:
    """A window opening: the reading dips by `dip` °C for `minutes` (the floor stays warm)."""

    zone: str
    start: int  # minutes from the start of the run
    minutes: int = 15
    dip: float = 1.0


@dataclass(frozen=True)
class Dropout:
    """A sensor stops reporting for `minutes`."""

    zone: str
    start: int
    minutes: int


@dataclass(frozen=True)
class SeasonOff:
    """The heating season is switched OFF for `minutes`."""

    start: int
    minutes: int

    def __contains__(self, minute: int) -> bool:
        return self.start <= minute < self.start + self.minutes


@dataclass(frozen=True)
class Sample:
    now: datetime
    hp_running: bool
    request: bool
    calling_zone: str | None
    sync_fired: bool
    modes: dict[str, ZoneMode]
    temps: dict[str, float]
    season: bool = True
    events: tuple[Event, ...] = ()  # emitted in this minute
    setpoints: dict[str, float] = field(default_factory=dict)  # effective SetPoints
    source_status: str = ""  # the heat source sensor (D-141)
    mode: str = ""  # the mode sensor: normal / holiday / failsafe (D-148)


@dataclass
class Trace:
    samples: list[Sample] = field(default_factory=list)


def random_windows(zones: list[str], minutes: int, count: int, seed: int) -> list[Window]:
    rng = random.Random(seed)
    return [Window(rng.choice(zones), rng.randrange(minutes)) for _ in range(count)]


def simulate(
    sc: Scenario,
    house: dict[str, ZoneModel],
    minutes: int,
    *,
    windows: Sequence[Window] = (),
    dropouts: Sequence[Dropout] = (),
    season_off: Sequence[SeasonOff] = (),
) -> Trace:
    """Run the core against `house` for `minutes`, one reconcile step per minute."""
    has_valve = {z.id: z.has_valve for z in sc.config.zones}
    trace = Trace()
    for minute in range(minutes):
        for zone, model in house.items():
            if any(d.zone == zone and d.start <= minute < d.start + d.minutes for d in dropouts):
                sc.silence(zone)
                continue
            dip = sum(
                w.dip for w in windows if w.zone == zone and w.start <= minute < w.start + w.minutes
            )
            sc.temp(zone, round(model.temp - dip, 2))
        sc.set_season(not any(minute in off for off in season_off))
        seen = len(sc.events)
        if minute == 0:
            sc.step()
        else:
            sc.advance(1)
        running = sc.hp_actual is OutputState.ON
        for zone, model in house.items():
            open_ = not has_valve[zone] or sc.valves_actual[zone] is OutputState.ON
            model.advance(1, running and open_)
        trace.samples.append(
            Sample(
                now=sc.now,
                hp_running=running,
                request=sc.hp,
                calling_zone=sc.calling_zone,
                sync_fired=sc.sync_fired,
                modes=sc.modes(),
                temps={z: m.temp for z, m in house.items()},
                season=sc.heating_season,
                events=tuple(sc.events[seen:]),
                setpoints={z: sc.setpoint(z) for z in house},
                source_status=sc.source_status,
                mode=sc.system_mode(),
            )
        )
    return trace


def periods(samples: list[Sample], key: str) -> list[tuple[bool, list[Sample]]]:
    """Split samples into consecutive runs of equal `key` (a bool attribute)."""
    runs: list[tuple[bool, list[Sample]]] = []
    for sample in samples:
        value = getattr(sample, key)
        if runs and runs[-1][0] == value:
            runs[-1][1].append(sample)
        else:
            runs.append((value, [sample]))
    return runs


def duration(run: list[Sample]) -> timedelta:
    return run[-1].now - run[0].now + timedelta(minutes=1)
