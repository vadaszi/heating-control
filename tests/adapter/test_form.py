"""Holiday and schedule form entities (docs/design.md §5.3, D-74, D-79, D-137, D-138)."""

from __future__ import annotations

from datetime import date, time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.multizone_floor_heating_manager.const import DOMAIN
from custom_components.multizone_floor_heating_manager.core.schedule import ScheduleKind

from .conftest import PKG, START, World
from .test_restart import restarted

HOLIDAY = "switch.floor_heating_holiday"
HOLIDAY_END = "datetime.floor_heating_holiday_end"
KIND = "select.floor_heating_schedule_type"
ZONE = "select.floor_heating_schedule_zone"
DAYS = "select.floor_heating_schedule_days"
EXISTING = "select.floor_heating_existing_schedule"
DATE = "date.floor_heating_schedule_date"
START_TIME = "time.floor_heating_schedule_start"
END_TIME = "time.floor_heating_schedule_end"
TEMPERATURE = "number.floor_heating_schedule_temperature"
ADD = "button.floor_heating_add_schedule"
DELETE = "button.floor_heating_delete_schedule"
MODE = "sensor.floor_heating_mode"
DASH = "\u2013"  # en dash in the labels

FORM_ENTITIES = {
    HOLIDAY: "holiday",
    HOLIDAY_END: "holiday_end",
    KIND: "schedule_kind",
    ZONE: "schedule_zone",
    DAYS: "schedule_days",
    EXISTING: "existing_schedule",
    DATE: "schedule_date",
    START_TIME: "schedule_start",
    END_TIME: "schedule_end",
    TEMPERATURE: "schedule_temperature",
    ADD: "add_schedule",
    DELETE: "delete_schedule",
}


async def _call(
    hass: HomeAssistant, domain: str, service: str, entity_id: str, **data: Any
) -> None:
    await hass.services.async_call(domain, service, {"entity_id": entity_id, **data}, blocking=True)
    await hass.async_block_till_done()


async def _select(hass: HomeAssistant, entity_id: str, option: str) -> None:
    await _call(hass, "select", "select_option", entity_id, option=option)


async def _press(hass: HomeAssistant, entity_id: str) -> None:
    await _call(hass, "button", "press", entity_id)


async def _fill(hass: HomeAssistant, **values: str) -> None:
    """Fill the form: kind, zone, days (select options), start, end, temperature, day."""
    for key, entity_id in (("kind", KIND), ("zone", ZONE), ("days", DAYS)):
        if key in values:
            await _select(hass, entity_id, values[key])
    for key, entity_id in (("start", START_TIME), ("end", END_TIME)):
        if key in values:
            await _call(hass, "time", "set_value", entity_id, time=values[key])
    if "temperature" in values:
        await _call(hass, "number", "set_value", TEMPERATURE, value=values["temperature"])
    if "day" in values:
        await _call(hass, "date", "set_value", DATE, date=values["day"])


async def test_form_entities_exist_on_the_global_device(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    registry = er.async_get(world.hass)
    for entity_id, unique_id in FORM_ENTITIES.items():
        entry = registry.async_get(entity_id)
        assert entry is not None, entity_id
        assert (entry.platform, entry.unique_id, entry.entity_category) == (
            DOMAIN,
            unique_id,
            None,
        )
        assert world.state(entity_id) != "unavailable", entity_id
    state = world.hass.states.get(ZONE)
    assert state is not None
    assert state.attributes["options"] == ["All zones", "Zone 1", "Zone 2"]
    assert (world.state(KIND), world.state(ZONE), world.state(DAYS)) == (
        "auto",
        "All zones",
        "every_day",
    )
    assert (world.state(START_TIME), world.state(END_TIME), world.state(TEMPERATURE)) == (
        "06:00:00",
        "08:00:00",
        "22.0",
    )
    assert world.state(DATE) == START.date().isoformat()
    assert world.state(EXISTING) == "unknown"  # no schedules yet
    assert world.state(HOLIDAY_END) == "unknown"  # empty until set


async def test_add_and_delete_through_the_form(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    hass = world.hass
    await _fill(
        hass, zone="Zone 1", days="monday_to_friday", start="13:00", end="17:00", temperature="23"
    )
    await _press(hass, ADD)
    [schedule] = world.controller.settings.schedules
    assert (schedule.kind, schedule.zone_ids, schedule.weekdays, schedule.temperature) == (
        ScheduleKind.AUTO,
        ("zone_1",),
        frozenset(range(5)),
        23.0,
    )
    assert (schedule.start, schedule.end, schedule.on_date) == (time(13), time(17), None)
    assert (
        world.state(EXISTING) == f"#1 Auto · Zone 1 · Monday{DASH}Friday 13:00{DASH}17:00 · 23.0 °C"
    )
    assert world.state(DAYS) == "monday_to_friday"  # the draft stays (D-138)

    await _fill(hass, kind="manual", zone="All zones", days="once", day="2026-01-13")
    await _press(hass, ADD)
    manual = world.controller.settings.schedules[1]
    assert (manual.kind, manual.zone_ids, manual.on_date, manual.temperature) == (
        ScheduleKind.MANUAL,
        None,
        date(2026, 1, 13),
        None,  # a manual schedule ignores the form's temperature
    )
    assert str(world.state(EXISTING)).startswith("#2 Manual · All zones · 2026-01-13")  # new one

    existing = hass.states.get(EXISTING)
    assert existing is not None
    await _select(hass, EXISTING, existing.attributes["options"][0])
    await _press(hass, DELETE)
    assert [s.id for s in world.controller.settings.schedules] == ["2"]
    assert str(world.state(EXISTING)).startswith("#2 ")
    await _press(hass, DELETE)
    assert world.controller.settings.schedules == ()
    assert world.state(EXISTING) == "unknown"


async def test_form_errors_become_a_persistent_notification(world: World) -> None:
    """D-74: an overlap (A12) from the form is a notification; nothing is stored."""
    world.setup_entities()
    assert await world.setup()
    hass = world.hass
    with (
        patch(f"{PKG}.form.persistent_notification.async_create") as create,
        patch(f"{PKG}.form.persistent_notification.async_dismiss") as dismiss,
    ):
        await _press(hass, DELETE)
        _assert_notified(create, "Floor heating: schedule not deleted", "no schedule to delete")
        await _press(hass, ADD)
        dismiss.assert_called_once()
        create.reset_mock()
        await _fill(hass, days="monday", start="07:00", end="09:00")
        await _press(hass, ADD)
        _assert_notified(create, "Floor heating: schedule not added", "overlaps auto schedule '1'")
    assert [s.id for s in world.controller.settings.schedules] == ["1"]


def _assert_notified(create: MagicMock, title: str, text: str) -> None:
    create.assert_called_once()
    _hass, message = create.call_args.args
    assert create.call_args.kwargs["title"] == title
    assert text in message


async def test_schedule_temperature_in_fahrenheit(world: World, hass: HomeAssistant) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    world.setup_entities()
    assert await world.setup()
    state = hass.states.get(TEMPERATURE)
    assert state is not None
    assert state.attributes["unit_of_measurement"] == "°F"
    assert float(state.state) == pytest.approx(71.6)
    await _fill(hass, temperature="73.4")
    assert world.controller.form.temperature == pytest.approx(23.0)


async def test_drafts_reset_after_a_restart(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    await _fill(world.hass, kind="manual", start="10:00")
    async with restarted(world, prepare=lambda new: new.setup_entities()) as new:
        assert await new.setup(live=False)
        assert (new.state(KIND), new.state(START_TIME)) == ("auto", "06:00:00")


async def test_holiday_through_the_entities(world: World) -> None:
    """D-137: on without an end; the end moves it; off clears the end."""
    world.setup_entities()
    assert await world.setup()
    hass = world.hass
    await _call(hass, "switch", "turn_on", HOLIDAY)
    assert (world.state(HOLIDAY), world.state(MODE)) == ("on", "holiday")
    await _call(hass, "datetime", "set_value", HOLIDAY_END, datetime="2026-01-14 12:00:00")
    assert world.state(HOLIDAY_END) == "2026-01-14T12:00:00+00:00"
    await _call(hass, "switch", "turn_off", HOLIDAY)
    assert (world.state(HOLIDAY), world.state(MODE)) == ("off", "normal")
    assert world.state(HOLIDAY_END) == "unknown"


async def test_holiday_with_a_past_end_is_refused(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    hass = world.hass
    await _call(hass, "datetime", "set_value", HOLIDAY_END, datetime="2026-01-12 05:00:00")
    with pytest.raises(ServiceValidationError, match="the holiday end is in the past"):
        await _call(hass, "switch", "turn_on", HOLIDAY)
    assert world.state(HOLIDAY) == "off"


async def test_holiday_switch_turns_off_at_the_end(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    hass = world.hass
    await _call(hass, "datetime", "set_value", HOLIDAY_END, datetime="2026-01-12 07:00:00")
    await _call(hass, "switch", "turn_on", HOLIDAY)
    await world.advance(60)
    assert (world.state(HOLIDAY), world.state(HOLIDAY_END), world.state(MODE)) == (
        "off",
        "unknown",
        "normal",
    )
