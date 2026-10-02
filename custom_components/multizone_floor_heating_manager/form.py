"""The schedule form on the dashboard.

The form's entities (selects, date, times, temperature, buttons) edit one draft held by
the controller. The draft is not stored: it keeps its values after "Add schedule", so a
similar schedule is quick to add, and starts from the defaults after a restart.
The buttons use the same validated controller methods as the services; a rejected
schedule is shown as a persistent notification instead of an error.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, time
from typing import TYPE_CHECKING

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .core.config import ConfigError
from .core.schedule import ScheduleKind
from .schedules import DAY_OPTIONS, ONCE

if TYPE_CHECKING:
    from .controller import FloorHeatingController

_LOGGER = logging.getLogger(__name__)

ALL_ZONES = "All zones"  # the zone select's first option
NOTIFICATION = f"{DOMAIN}_schedule_form"


@dataclass
class ScheduleForm:
    """The draft of the next schedule, and the schedule chosen for "Delete schedule"."""

    day: date = field(default_factory=lambda: dt_util.now().date())  # the "Once" date
    kind: ScheduleKind = ScheduleKind.AUTO
    zone: str = ALL_ZONES  # ALL_ZONES or a zone name
    days: str = "every_day"  # a key of DAY_OPTIONS
    start: time = time(6, 0)
    end: time = time(8, 0)
    temperature: float = 22.0  # °C, auto schedules only
    selected: str | None = None  # id of the schedule chosen in "Existing schedule"


async def async_add_from_form(hass: HomeAssistant, controller: FloorHeatingController) -> None:
    """The "Add schedule" button."""
    form = controller.form
    zone_ids = {zone.name: zone.id for zone in controller.config.core.zones}
    try:
        schedule = await controller.async_add_schedule(
            kind=form.kind,
            zone_ids=None if form.zone == ALL_ZONES else (zone_ids[form.zone],),
            start=form.start,
            end=form.end,
            on_date=form.day if form.days == ONCE else None,
            weekdays=DAY_OPTIONS[form.days],
            temperature=form.temperature if form.kind is ScheduleKind.AUTO else None,
        )
    except ConfigError as err:
        _notify(hass, "Floor heating: schedule not added", err.errors)
        return
    form.selected = schedule.id
    controller.async_update_listeners()
    persistent_notification.async_dismiss(hass, NOTIFICATION)


async def async_delete_from_form(hass: HomeAssistant, controller: FloorHeatingController) -> None:
    """The "Delete schedule" button: deletes the schedule chosen in "Existing schedule"."""
    schedule_id = selected_schedule(controller)
    if schedule_id is None:
        _notify(hass, "Floor heating: schedule not deleted", ["there is no schedule to delete"])
        return
    await controller.async_delete_schedule(schedule_id)  # exists: taken from the list
    controller.form.selected = None
    controller.async_update_listeners()
    persistent_notification.async_dismiss(hass, NOTIFICATION)


def selected_schedule(controller: FloorHeatingController) -> str | None:
    """The chosen schedule, or the first one if none (or a deleted one) is chosen."""
    ids = [schedule.id for schedule in controller.settings.schedules]
    if controller.form.selected in ids:
        return controller.form.selected
    return ids[0] if ids else None


def _notify(hass: HomeAssistant, title: str, errors: list[str]) -> None:
    message = "\n".join(f"- {error}" for error in errors)
    _LOGGER.warning("%s: %s", title, "; ".join(errors))
    persistent_notification.async_create(hass, message, title=title, notification_id=NOTIFICATION)
