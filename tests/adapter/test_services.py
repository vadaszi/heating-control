"""Schedule services."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.setup import async_setup_component
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.multizone_floor_heating_manager.const import DOMAIN

from .conftest import World

AUTO_ZONE_1 = {
    "kind": "auto",
    "zones": ["zone_1"],
    "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
    "start": "06:00",
    "end": "08:00",
    "temperature": 23,
}


async def _call(hass: HomeAssistant, service: str, **data: Any) -> Any:
    return await hass.services.async_call(
        DOMAIN,
        service,
        data,
        blocking=True,
        return_response=service != "delete_schedule",
    )


async def test_add_schedule_returns_it_and_it_takes_effect(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    response = await _call(world.hass, "add_schedule", **AUTO_ZONE_1)
    assert response["schedule"]["id"] == "1"
    assert response["schedule"]["label"].startswith("#1 Auto · Zone 1 · Every day 06:00")
    await world.hass.async_block_till_done()
    assert world.state("sensor.zone_1_floor_heating_effective_target_temperature") == "23.0"


async def test_a12_overlapping_auto_schedule_is_rejected(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    await _call(world.hass, "add_schedule", **AUTO_ZONE_1)
    overlapping = AUTO_ZONE_1 | {"weekdays": ["mon"], "start": "07:30", "end": "09:00"}
    with pytest.raises(ServiceValidationError, match="overlaps auto schedule '1' for zone_1"):
        await _call(world.hass, "add_schedule", **overlapping)
    assert [s.id for s in world.controller.settings.schedules] == ["1"]


@pytest.mark.parametrize(
    ("changes", "error"),
    [
        ({"zones": ["zone_1", "nowhere"]}, "unknown zones: nowhere"),
        ({"weekdays": []}, "needs a date \\(one-shot\\) or weekdays \\(recurring\\)"),
        ({"weekdays": None, "date": "2026-01-11"}, "its window is already over"),
        ({"temperature": None}, "an auto schedule needs a temperature"),
        ({"temperature": 31}, "out of range"),
        ({"kind": "manual"}, "a manual schedule has no temperature"),
        ({"end": "06:00"}, "start and end must differ"),
    ],
)
async def test_invalid_schedule_is_rejected(
    world: World, changes: dict[str, Any], error: str
) -> None:
    world.setup_entities()
    assert await world.setup()
    data = {k: v for k, v in (AUTO_ZONE_1 | changes).items() if v is not None}
    with pytest.raises(ServiceValidationError, match=error):
        await _call(world.hass, "add_schedule", **data)
    assert world.controller.settings.schedules == ()


async def test_date_and_weekdays_together_are_invalid(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    # HA's schema error (probatio stands in for voluptuous, so match the text)
    with pytest.raises(Exception, match="two or more values in the same group of exclusion"):
        await _call(world.hass, "add_schedule", **AUTO_ZONE_1, date="2026-01-13")


async def test_all_zones_one_shot_manual(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    response = await _call(
        world.hass,
        "add_schedule",
        kind="manual",
        zones="all",
        date="2026-01-13",
        start="22:00",
        end="02:00",
    )
    schedule = world.controller.settings.schedules[0]
    assert (schedule.zone_ids, schedule.on_date.isoformat() if schedule.on_date else None) == (
        None,
        "2026-01-13",
    )
    assert response["schedule"]["zones"] == "all"


async def test_temperature_in_fahrenheit(world: World, hass: HomeAssistant) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    world.setup_entities()
    assert await world.setup()
    response = await _call(hass, "add_schedule", **AUTO_ZONE_1 | {"temperature": 73.4})
    assert world.controller.settings.schedules[0].temperature == pytest.approx(23.0)
    assert response["schedule"]["temperature"] == 73.4


async def test_delete_and_list(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    await _call(world.hass, "add_schedule", **AUTO_ZONE_1)
    await _call(world.hass, "add_schedule", **AUTO_ZONE_1 | {"zones": ["zone_2"]})
    await _call(world.hass, "delete_schedule", schedule_id="#1")
    with pytest.raises(ServiceValidationError, match="there is no schedule #1"):
        await _call(world.hass, "delete_schedule", schedule_id=1)
    listed = await _call(world.hass, "list_schedules")
    assert [s["id"] for s in listed["schedules"]] == ["2"]
    assert listed["schedules"][0]["zones"] == ["zone_2"]


async def test_services_need_the_loaded_integration(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    with pytest.raises(ServiceValidationError, match="not loaded"):
        await _call(hass, "list_schedules")
