"""Failsafe case 1: HA alive, every sensor dead.

- Active in the heating season while no zone has a RoomTemp (each is in sensor fault or
  has had no reading yet) and the newest valid reading of any sensor is older than
  FailsafeTrigger. A zone without any reading counts from when it started waiting for
  one. The first valid reading ends it.
- Inside the daily FailsafeWindow (local time, may cross midnight, DST like the schedules) every
  zone has demand, so HpMinOffTime / HpMinOnTime apply as usual; every valve
  follows the heat source.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, tzinfo

from .config import GlobalParams
from .schedule import local_instant
from .state import ZoneState

_DAY = timedelta(days=1)


@dataclass(frozen=True)
class Failsafe:
    """The failsafe in this step."""

    in_window: bool
    until: datetime  # the window end while inside it, otherwise the next window start


def newest_reading(zone_states: Iterable[ZoneState]) -> datetime | None:
    """The newest valid reading of any zone; a zone without any reading counts from when
    it started waiting for one."""
    instants = [
        instant
        for zone_state in zone_states
        if (instant := zone_state.last_valid_at or zone_state.awaiting_reading_since) is not None
    ]
    return max(instants, default=None)


def failsafe(
    zone_states: Iterable[ZoneState],
    any_room_temp: bool,
    season: bool,
    params: GlobalParams,
    now: datetime,
    time_zone: tzinfo,
) -> Failsafe | None:
    """The failsafe, or None when it is not active."""
    if not season or any_room_temp:
        return None
    newest = newest_reading(zone_states)
    if newest is None or now - newest <= params.failsafe_trigger:
        return None
    return _window(params, now, time_zone)


def _window(params: GlobalParams, now: datetime, time_zone: tzinfo) -> Failsafe:
    start_at, end_at = params.failsafe_window_start, params.failsafe_window_end
    today = now.astimezone(time_zone).date()
    starts: list[datetime] = []
    for day in (today - _DAY, today, today + _DAY):
        start = local_instant(day, start_at, time_zone)
        end = local_instant(day + _DAY if end_at < start_at else day, end_at, time_zone)
        if start <= now < end:
            return Failsafe(in_window=True, until=end)
        if start > now and end > start:  # a window that DST leaves empty is skipped
            starts.append(start)
    return Failsafe(in_window=False, until=min(starts))
