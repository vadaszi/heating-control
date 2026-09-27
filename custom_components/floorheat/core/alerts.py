"""Notification events of the step function (docs/design.md §3.6, §3.9).

- Sensor fault started / recovered: on a mode change between the incoming and the new
  state, in and outside the heating season and in shadow mode (D-75, D-98). A fault
  already in the stored state is therefore not notified again after a restart.
- Daily reminder: one event per local day while a zone is faulty since an earlier local
  day, from SensorFaultReminder until midnight, only in the heating season (D-75, D-98).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import date, datetime, timedelta, tzinfo

from .config import CoreConfig, GlobalParams
from .io import Event, EventKind
from .state import ZoneMode, ZoneState

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
                        f"{minutes} min. The zone follows the heat pump and creates no demand."
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


def _local_date(value: datetime | None, time_zone: tzinfo, today: date) -> date:
    """Local date of `value`; unknown counts as an earlier day."""
    if value is None:
        return today - timedelta(days=1)
    return value.astimezone(time_zone).date()


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()
