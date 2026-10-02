"""Valve exercise outside the heating season (docs/design.md §3.7, D-149).

Weekly on the ValveExercise weekday and time (local, DST as D-134), the valves open one
after another in YAML order for the ValveExercise duration each; zones without a valve
are skipped and the heat source stays OFF. The slots follow from the due time alone:
a run missed while HA was down is skipped, and after a restart mid-run the remaining
valves continue. The heating season ON ends it (the caller only asks outside it).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

from .config import CoreConfig, GlobalParams
from .schedule import local_instant


@dataclass(frozen=True)
class ExerciseSlot:
    zone_id: str  # the zone whose valve is open
    until: datetime  # when it closes


def exercise_slot(
    config: CoreConfig, params: GlobalParams, now: datetime, time_zone: tzinfo
) -> ExerciseSlot | None:
    """The valve exercised at `now`, if any."""
    valved = [zone.id for zone in config.zones if zone.has_valve]
    duration = params.valve_exercise_duration
    if not valved:
        return None
    # The latest exercise day up to today; a run that started yesterday (e.g. Sunday
    # 23:50) may still go on after midnight.
    today = now.astimezone(time_zone).date()
    due_day = today - timedelta(days=(today.weekday() - params.valve_exercise_weekday) % 7)
    due = local_instant(due_day, params.valve_exercise_time, time_zone)
    if now < due:
        return None  # later today; last week's run is long over
    index = (now - due) // duration
    if index >= len(valved):
        return None
    return ExerciseSlot(valved[index], due + (index + 1) * duration)
