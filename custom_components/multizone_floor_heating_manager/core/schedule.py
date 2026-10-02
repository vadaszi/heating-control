"""Auto and manual schedules and holiday.

Schedules and the holiday end are owned by the adapter and reach `step` through `Inputs`. This
module is pure: it resolves them into each zone's target for a given moment, checks a new schedule
before it is stored, and (de)serialises schedules.

Windows:
- local wall-clock times in HA's time zone, half-open `[start, end)`; an end before the
  start crosses midnight; start == end is rejected;
- a recurring window belongs to the weekday it starts on, a one-shot window to its date;
- DST: a local time is resolved with `fold=0`. A time in the repeated autumn
  hour is its first occurrence; a time in the spring gap is shifted by the gap length
  (02:30 -> 03:30 CEST), as for the sensor fault reminder. A window whose start ends up
  at or after its end is empty that day.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from enum import StrEnum
from typing import Any

from .config import ConfigError, CoreConfig, ParamSpec, ParamUnit, ZoneParams

# Auto schedule temperature: the BaseSetPoint range.
SCHEDULE_TEMPERATURE_SPEC = ParamSpec("temperature", 22.0, 10, 30, 0.1, ParamUnit.CELSIUS)

WEEKDAYS = frozenset(range(7))  # Monday = 0, as `date.weekday()`; "daily" = all seven

_DAY = timedelta(days=1)
# `until` of a manual window chain is searched this far ahead at most: two recurring
# windows that meet end to end (e.g. 00:00-12:00 and 12:00-00:00) force a zone forever.
_UNION_HORIZON = timedelta(days=8)


class ScheduleKind(StrEnum):
    AUTO = "auto"  # overrides the SetPoint
    MANUAL = "manual"  # forces the zone


@dataclass(frozen=True)
class Schedule:
    """One auto or manual schedule.

    - `zone_ids`: the zones it covers; None means all zones, including zones added to
      the YAML later.
    - One-shot: `on_date` set, `weekdays` empty. Recurring: `weekdays` set, no date.
    - `temperature`: °C, auto schedules only.
    """

    id: str
    kind: ScheduleKind
    start: time
    end: time
    zone_ids: tuple[str, ...] | None = None
    on_date: date | None = None
    weekdays: frozenset[int] = frozenset()
    temperature: float | None = None

    def __post_init__(self) -> None:
        if errors := self._errors():
            label = self.id if isinstance(self.id, str) and self.id else "new schedule"
            raise ConfigError([f"schedule {label!r}: {error}" for error in errors])

    def _errors(self) -> list[str]:
        errors: list[str] = []
        schedule_id: object = self.id
        if not isinstance(schedule_id, str) or not schedule_id.strip():
            errors.append(f"id must be a non-empty string, got {schedule_id!r}")
        kind: object = self.kind
        if not isinstance(kind, ScheduleKind):
            errors.append(f"kind must be auto or manual, got {kind!r}")
        errors += _window_errors(self.start, self.end)
        errors += _zone_errors(self.zone_ids)
        errors += self._day_errors()
        temperature: object = self.temperature
        if kind is ScheduleKind.AUTO:
            if temperature is None:
                errors.append("an auto schedule needs a temperature")
            elif error := SCHEDULE_TEMPERATURE_SPEC.check(temperature):
                errors.append(error)
        elif kind is ScheduleKind.MANUAL and temperature is not None:
            errors.append("a manual schedule has no temperature")
        return errors

    def _day_errors(self) -> list[str]:
        on_date: object = self.on_date
        weekdays: object = self.weekdays
        if not isinstance(weekdays, frozenset) or not all(
            isinstance(day, int) and not isinstance(day, bool) and day in WEEKDAYS
            for day in weekdays
        ):
            return [f"weekdays must be a set of 0 (Monday) to 6 (Sunday), got {weekdays!r}"]
        if on_date is None:
            return [] if weekdays else ["needs a date (one-shot) or weekdays (recurring)"]
        if not isinstance(on_date, date) or isinstance(on_date, datetime):
            return [f"date must be a date, got {on_date!r}"]
        return ["has both a date and weekdays"] if weekdays else []

    @property
    def one_shot(self) -> bool:
        return self.on_date is not None

    def covers(self, zone_id: str) -> bool:
        return self.zone_ids is None or zone_id in self.zone_ids

    def applies_on(self, day: date) -> bool:
        """Whether a window starts on this local date."""
        return day == self.on_date if self.on_date is not None else day.weekday() in self.weekdays

    def window_on(self, day: date, time_zone: tzinfo) -> tuple[datetime, datetime]:
        """The window starting on `day` as UTC instants; empty (start == end) if DST
        leaves nothing of it. The caller checks `applies_on`."""
        start = local_instant(day, self.start, time_zone)
        end = local_instant(day + _DAY if self.end < self.start else day, self.end, time_zone)
        return start, max(start, end)

    def active_window(self, now: datetime, time_zone: tzinfo) -> tuple[datetime, datetime] | None:
        """The window covering `now`, if any."""
        today = now.astimezone(time_zone).date()
        for day in (today - _DAY, today):  # a window is shorter than a day
            if self.applies_on(day):
                start, end = self.window_on(day, time_zone)
                if start <= now < end:
                    return start, end
        return None

    def has_ended(self, now: datetime, time_zone: tzinfo) -> bool:
        """A one-shot schedule whose window is over (deleted by the adapter)."""
        if self.on_date is None:
            return False
        return now >= self.window_on(self.on_date, time_zone)[1]

    # ------------------------------------------------------------ persistence

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable form; the adapter stores it."""
        return {
            "id": self.id,
            "kind": self.kind.value,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "zone_ids": None if self.zone_ids is None else list(self.zone_ids),
            "date": None if self.on_date is None else self.on_date.isoformat(),
            "weekdays": sorted(self.weekdays),
            "temperature": self.temperature,
        }

    @classmethod
    def from_dict(cls, data: object) -> Schedule:
        """Parse `to_dict` output; raises `ConfigError` if it cannot be used."""
        if not isinstance(data, Mapping):
            raise ConfigError([f"schedule: expected a mapping, got {data!r}"])
        try:
            zone_ids = data.get("zone_ids")
            on_date = data.get("date")
            weekdays = data.get("weekdays") or []
            temperature = data.get("temperature")
            return cls(
                id=data.get("id"),  # type: ignore[arg-type]  # checked by the model
                kind=ScheduleKind(data["kind"]),
                start=time.fromisoformat(data["start"]),
                end=time.fromisoformat(data["end"]),
                zone_ids=None if zone_ids is None else tuple(zone_ids),
                on_date=None if on_date is None else date.fromisoformat(on_date),
                weekdays=frozenset(weekdays),
                temperature=None if temperature is None else float(temperature),
            )
        except ConfigError:
            raise
        except (KeyError, TypeError, ValueError) as err:
            raise ConfigError([f"schedule {data.get('id')!r}: unusable ({err})"]) from None


def local_instant(day: date, at: time, time_zone: tzinfo) -> datetime:
    """A local wall-clock time as a UTC instant (fold=0): a time in the repeated
    autumn hour is its first occurrence, one in the spring gap is shifted by the gap.
    Also used by the adapter for the holiday end."""
    return datetime.combine(day, at.replace(fold=0), tzinfo=time_zone).astimezone(UTC)


def _window_errors(start: object, end: object) -> list[str]:
    errors = [
        f"{name} must be a local time of day, got {value!r}"
        for name, value in (("start", start), ("end", end))
        if not isinstance(value, time) or value.tzinfo is not None
    ]
    if not errors and start == end:
        errors.append("start and end must differ")
    return errors


def _zone_errors(zone_ids: object) -> list[str]:
    if zone_ids is None:
        return []
    if not isinstance(zone_ids, tuple) or not all(isinstance(z, str) for z in zone_ids):
        return [f"zone_ids must be a tuple of zone ids or None (all zones), got {zone_ids!r}"]
    if not zone_ids:
        return ["needs at least one zone"]
    if len(set(zone_ids)) != len(zone_ids):
        return [f"zone listed twice in {list(zone_ids)!r}"]
    return []


# ---------------------------------------------------------------- targets


@dataclass(frozen=True)
class ZoneTarget:
    """What schedules and holiday make of one zone at one moment (precedence: holiday > manual >
    auto > base)."""

    setpoint: float  # effective SetPoint, °C
    forced: bool = False  # a manual window is running (the engine does not force a faulty zone)
    forced_until: datetime | None = None  # end of the running manual windows


def holiday_active(on: bool, until: datetime | None, now: datetime) -> bool:
    """Holiday runs from activation until its end, or until it is switched
    off if it has no end."""
    return on and (until is None or now < until)


def zone_target(
    zone_id: str,
    params: ZoneParams,
    schedules: Sequence[Schedule],
    holiday: bool,
    now: datetime,
    time_zone: tzinfo,
) -> ZoneTarget:
    """Holiday > manual > auto > BaseSetPoint; `holiday`: holiday is active.

    - Holiday: the zone's own holiday temperature; schedules are suspended.
    - Manual: the zone is forced; its SetPoint is the one below (auto or base).
      Overlapping manual windows form their union.
    - Auto: at most one covers a zone at a time; should stored data hold more,
      the first in list order wins.
    """
    if holiday:
        return ZoneTarget(params.holiday_temp)
    mine = [s for s in schedules if s.covers(zone_id)]
    setpoint = params.base_setpoint
    for schedule in mine:
        if schedule.kind is ScheduleKind.AUTO and schedule.active_window(now, time_zone):
            assert schedule.temperature is not None  # checked by the model
            setpoint = schedule.temperature
            break
    manual = [s for s in mine if s.kind is ScheduleKind.MANUAL]
    until = _union_end(manual, now, time_zone)
    return ZoneTarget(setpoint, forced=until is not None, forced_until=until)


def _union_end(manual: Sequence[Schedule], now: datetime, time_zone: tzinfo) -> datetime | None:
    """End of the union of manual windows running at `now`, following windows that
    start exactly where (or before) another ends."""
    until: datetime | None = None
    moment = now
    while moment - now < _UNION_HORIZON:
        ends = [w[1] for s in manual if (w := s.active_window(moment, time_zone))]
        if not ends:
            break
        until = moment = max(ends)
    return until


def ended_schedules(
    schedules: Iterable[Schedule], now: datetime, time_zone: tzinfo
) -> tuple[str, ...]:
    """Ids of one-shot schedules whose window is over; the adapter deletes them."""
    return tuple(s.id for s in schedules if s.has_ended(now, time_zone))


# ---------------------------------------------------------------- creation check


def check_new_schedule(
    new: Schedule,
    existing: Sequence[Schedule],
    config: CoreConfig,
    now: datetime,
    time_zone: tzinfo,
) -> list[str]:
    """Problems that reject `new` before it is stored; empty if none."""
    errors: list[str] = []
    if any(s.id == new.id for s in existing):
        errors.append(f"a schedule with id {new.id!r} already exists")
    if new.zone_ids is not None:
        unknown = [z for z in new.zone_ids if z not in config.zone_ids]
        if unknown:
            errors.append(f"unknown zones: {', '.join(unknown)}")
    if new.has_ended(now, time_zone):
        errors.append("its window is already over")
    if new.kind is ScheduleKind.AUTO:
        for other in existing:
            if other.kind is not ScheduleKind.AUTO:
                continue
            shared = _shared_zones(new, other, config)
            if shared and overlaps(new, other):
                errors.append(
                    f"overlaps auto schedule {other.id!r} for {', '.join(shared)}; "
                    "auto schedules for the same zone may not overlap"
                )
    return errors


def _shared_zones(a: Schedule, b: Schedule, config: CoreConfig) -> list[str]:
    return [z for z in config.zone_ids if a.covers(z) and b.covers(z)]


# Any Monday: recurring windows repeat weekly, so eight days from a Monday contain every
# pair of occurrences that can meet (a window is shorter than a day).
_REFERENCE_MONDAY = date(2024, 1, 1)


def overlaps(a: Schedule, b: Schedule) -> bool:
    """Whether the windows of `a` and `b` share any local wall-clock minute.

    Zones are not considered; touching windows (one ends when the other starts) do not
    overlap.
    """
    one_shot_days = [s.on_date for s in (a, b) if s.on_date is not None]
    if one_shot_days:
        days = sorted({d + k * _DAY for d in one_shot_days for k in (-1, 0, 1)})
    else:
        days = [_REFERENCE_MONDAY + k * _DAY for k in range(8)]
    windows_a = _wall_windows(a, days)
    return any(
        start_a < end_b and start_b < end_a
        for start_a, end_a in windows_a
        for start_b, end_b in _wall_windows(b, days)
    )


def _wall_windows(schedule: Schedule, days: Iterable[date]) -> list[tuple[datetime, datetime]]:
    """Naive local windows starting on `days` (wall-clock, no DST)."""
    windows = []
    for day in days:
        if schedule.applies_on(day):
            end_day = day + _DAY if schedule.end < schedule.start else day
            windows.append(
                (datetime.combine(day, schedule.start), datetime.combine(end_day, schedule.end))
            )
    return windows


# ---------------------------------------------------------------- persistence


def load_schedules(data: object, config: CoreConfig) -> tuple[tuple[Schedule, ...], list[str]]:
    """Restore stored schedules for `config`; returns them and warnings to log.

    Unusable entries are dropped. A zone no longer configured is removed from every
    schedule, and a schedule left without zones is dropped. "All zones"
    schedules are kept as they are.
    """
    if data is None:
        return (), []
    if not isinstance(data, list):
        return (), [f"Stored schedules are unusable ({data!r}); starting without schedules."]
    schedules: list[Schedule] = []
    warnings: list[str] = []
    for item in data:
        try:
            schedule = Schedule.from_dict(item)
        except ConfigError as err:
            warnings.append(f"Discarded an unusable stored schedule: {'; '.join(err.errors)}")
            continue
        if any(s.id == schedule.id for s in schedules):
            warnings.append(f"Discarded a second stored schedule with id {schedule.id!r}")
            continue
        if schedule.zone_ids is not None:
            kept = tuple(z for z in schedule.zone_ids if z in config.zone_ids)
            removed = [z for z in schedule.zone_ids if z not in kept]
            if not kept:
                warnings.append(
                    f"Discarded schedule {schedule.id!r}: its zones are no longer configured "
                    f"({', '.join(removed)})"
                )
                continue
            if removed:
                warnings.append(
                    f"Removed zones no longer configured from schedule {schedule.id!r}: "
                    f"{', '.join(removed)}"
                )
                schedule = dataclasses.replace(schedule, zone_ids=kept)
        schedules.append(schedule)
    return tuple(schedules), warnings


def schedules_to_list(schedules: Iterable[Schedule]) -> list[dict[str, Any]]:
    return [schedule.to_dict() for schedule in schedules]
