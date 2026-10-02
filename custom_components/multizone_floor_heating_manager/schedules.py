"""How schedules are shown and entered in HA.

- `schedule_label`: the generated text of a schedule (no name field), e.g.
  `#3 Auto · Living room · Every day 13:00-17:00 · 23.0 °C`, with an en dash;
- `schedule_view`: the dict of one schedule in the Schedules sensor's attribute and the
  `list_schedules` response;
- `DAY_OPTIONS`: the schedule form's "Days" choices.

Temperatures are shown in HA's unit system.
"""

from __future__ import annotations

from typing import Any

from .core.config import CoreConfig
from .core.schedule import WEEKDAYS, Schedule
from .core.units import TemperatureUnit, from_celsius

WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")  # Monday = 0
_WEEKDAY_NAMES = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEEKDAY_OPTIONS = tuple(name.lower() for name in _WEEKDAY_NAMES)  # select options, Monday = 0

_DASH = "\u2013"  # en dash between the times and in "Monday-Friday"

WORKDAYS = frozenset(range(5))
WEEKEND = frozenset({5, 6})

# The form's "Days" select: option key -> weekdays; "once" uses the form's date.
ONCE = "once"
DAY_OPTIONS: dict[str, frozenset[int]] = {
    ONCE: frozenset(),
    "every_day": WEEKDAYS,
    "monday_to_friday": WORKDAYS,
    "saturday_and_sunday": WEEKEND,
    **{name: frozenset({day}) for day, name in enumerate(WEEKDAY_OPTIONS)},
}


def days_text(schedule: Schedule) -> str:
    if schedule.on_date is not None:
        return schedule.on_date.isoformat()
    days = schedule.weekdays
    if days == WEEKDAYS:
        return "Every day"
    if days == WORKDAYS:
        return f"Monday{_DASH}Friday"
    if days == WEEKEND:
        return f"Saturday{_DASH}Sunday"
    if len(days) == 1:
        return _WEEKDAY_NAMES[next(iter(days))]
    return ", ".join(_WEEKDAY_NAMES[day][:3] for day in sorted(days))


def zones_text(schedule: Schedule, config: CoreConfig) -> str:
    if schedule.zone_ids is None:
        return "All zones"
    names = {zone.id: zone.name for zone in config.zones}
    return ", ".join(names.get(zone_id, zone_id) for zone_id in schedule.zone_ids)


def shown_temperature(schedule: Schedule, unit: TemperatureUnit) -> float | None:
    if schedule.temperature is None:
        return None
    return round(from_celsius(schedule.temperature, unit), 1)


def schedule_label(schedule: Schedule, config: CoreConfig, unit: TemperatureUnit) -> str:
    window = f"{schedule.start:%H:%M}{_DASH}{schedule.end:%H:%M}"
    parts = [
        f"#{schedule.id} {schedule.kind.value.capitalize()}",
        zones_text(schedule, config),
        f"{days_text(schedule)} {window}",
    ]
    temperature = shown_temperature(schedule, unit)
    if temperature is not None:
        parts.append(f"{temperature:.1f} {unit.value}")
    return " · ".join(parts)


def schedule_view(schedule: Schedule, config: CoreConfig, unit: TemperatureUnit) -> dict[str, Any]:
    return {
        "id": schedule.id,
        "label": schedule_label(schedule, config, unit),
        "kind": schedule.kind.value,
        "zones": "all" if schedule.zone_ids is None else list(schedule.zone_ids),
        "date": None if schedule.on_date is None else schedule.on_date.isoformat(),
        "weekdays": [WEEKDAY_KEYS[day] for day in sorted(schedule.weekdays)],
        "start": f"{schedule.start:%H:%M}",
        "end": f"{schedule.end:%H:%M}",
        "temperature": shown_temperature(schedule, unit),
    }
