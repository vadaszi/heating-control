"""Upgrades: entities a version no longer provides are removed; how HA names new entities."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.multizone_floor_heating_manager.const import DOMAIN, NAME

from .conftest import World, make_conf


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title=NAME, data={})
    entry.add_to_hass(hass)
    return entry


def _own_entities(hass: HomeAssistant, entry: ConfigEntry) -> list[er.RegistryEntry]:
    return er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)


@pytest.mark.parametrize("unvalved", [(), (2,)])
async def test_every_current_entity_is_kept(
    world: World, hass: HomeAssistant, unvalved: tuple[int, ...]
) -> None:
    world.setup_entities()
    assert await world.setup(make_conf(2, unvalved=unvalved), live=False)
    registered = _own_entities(hass, world.entry)
    assert len(registered) == len(world.controller.entities)
    assert all(hass.states.get(item.entity_id) is not None for item in registered)


async def test_an_entity_no_longer_provided_is_removed(
    world: World, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """E.g. the global holiday temperature of older versions, now one per zone."""
    entry = _entry(hass)
    entities = er.async_get(hass)
    old = entities.async_get_or_create(
        "number", DOMAIN, "holiday_temp", config_entry=entry, suggested_object_id="old_holiday"
    )
    other_entry = MockConfigEntry(domain="other")
    other_entry.add_to_hass(hass)
    other = entities.async_get_or_create(
        "number", "other", "holiday_temp", config_entry=other_entry
    )
    world.setup_entities()
    assert await world.setup(live=False)
    assert entities.async_get(old.entity_id) is None
    assert f"Removing {old.entity_id}" in caplog.text
    assert entities.async_get(other.entity_id) is not None  # not ours: untouched


async def test_renamed_and_disabled_entities_are_kept(world: World, hass: HomeAssistant) -> None:
    entry = _entry(hass)
    entities = er.async_get(hass)
    renamed = entities.async_get_or_create(
        "sensor", DOMAIN, "zone_1_reason", config_entry=entry, suggested_object_id="my_reason"
    )
    disabled = entities.async_get_or_create(
        "sensor",
        DOMAIN,
        "zone_2_reason",
        config_entry=entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    world.setup_entities()
    assert await world.setup(live=False)
    assert entities.async_get(renamed.entity_id) is not None
    assert hass.states.get("sensor.my_reason") is not None
    kept = entities.async_get(disabled.entity_id)
    assert kept is not None
    assert kept.disabled_by is er.RegistryEntryDisabler.USER


async def test_nothing_is_removed_when_a_platform_fails(
    world: World, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    entry = _entry(hass)
    entities = er.async_get(hass)
    old = entities.async_get_or_create("number", DOMAIN, "holiday_temp", config_entry=entry)
    world.setup_entities()
    with patch(
        "custom_components.multizone_floor_heating_manager.switch.async_setup_entry",
        side_effect=RuntimeError("broken platform"),
    ):
        assert await world.setup(live=False)
    assert entities.async_get(old.entity_id) is not None
    assert "old entities are not removed" in caplog.text


async def test_a_new_entity_on_a_device_with_an_area_gets_the_area_in_its_id(
    world: World, hass: HomeAssistant
) -> None:
    """HA builds a new entity's id from area + device + entity name. An entity that a new
    version adds to a zone device already placed in an area of the same name therefore gets
    the name twice (e.g. `number.bedroom_bedroom_floor_heating_holiday_temperature`).
    Entities registered before the area was set keep their ids."""
    world.setup_entities()
    assert await world.setup(live=False)
    entities = er.async_get(hass)
    devices = dr.async_get(hass)
    device = devices.async_get_device_by_identifier((DOMAIN, "zone_1"), world.entry.entry_id)
    assert device is not None
    area = ar.async_get(hass).async_create("Zone 1")
    devices.async_update_device(device.id, area_id=area.id)
    new_entity = entities.async_get_entity_id("number", DOMAIN, "zone_1_holiday_temp")
    assert new_entity == "number.zone_1_floor_heating_holiday_temperature"
    # As if this version had just added it: no registry entry, none remembered.
    entities.async_remove(new_entity)
    entities.deleted_entities.pop(("number", DOMAIN, "zone_1_holiday_temp"))
    assert await hass.config_entries.async_reload(world.entry.entry_id)
    await hass.async_block_till_done()
    assert (
        entities.async_get_entity_id("number", DOMAIN, "zone_1_holiday_temp")
        == "number.zone_1_zone_1_floor_heating_holiday_temperature"
    )
    assert entities.async_get_entity_id("climate", DOMAIN, "zone_1") == (
        "climate.zone_1_floor_heating"
    )
