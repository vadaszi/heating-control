"""Config entry from the YAML, devices and names, translations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from homeassistant.config_entries import SOURCE_IMPORT, SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.multizone_floor_heating_manager.const import DOMAIN, NAME, STORAGE_KEY
from custom_components.multizone_floor_heating_manager.core.io import HeatSourceStatus, Mode, Reason
from custom_components.multizone_floor_heating_manager.core.schedule import ScheduleKind
from custom_components.multizone_floor_heating_manager.core.state import ZoneMode
from custom_components.multizone_floor_heating_manager.number import GLOBAL_KEYS, ZONE_KEYS
from custom_components.multizone_floor_heating_manager.schedules import DAY_OPTIONS, WEEKDAY_KEYS

from .conftest import World, make_conf, valve

PACKAGE = Path(__file__).parents[2] / "custom_components" / DOMAIN


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, title=NAME, data={})
    entry.add_to_hass(hass)
    return entry


async def test_yaml_creates_one_entry(world: World, hass: HomeAssistant) -> None:
    world.setup_entities()
    assert await world.setup(live=False)
    [entry] = hass.config_entries.async_entries(DOMAIN)
    assert entry.title == "Multizone Floor Heating Manager"
    assert entry.source == SOURCE_IMPORT
    assert entry.data == {}  # the YAML stays in memory, no password in the entry
    assert entry.state is ConfigEntryState.LOADED


async def test_an_existing_entry_is_reused(world: World, hass: HomeAssistant) -> None:
    entry = _entry(hass)
    world.setup_entities()
    assert await world.setup(live=False)
    assert hass.config_entries.async_entries(DOMAIN) == [entry]
    assert entry.state is ConfigEntryState.LOADED


async def test_ui_setup_points_to_the_yaml(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "yaml_only"
    _entry(hass)
    for source in (SOURCE_USER, SOURCE_IMPORT):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": source}, data={}
        )
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "single_instance_allowed"


async def test_devices_per_zone_and_global(world: World, hass: HomeAssistant) -> None:
    world.setup_entities()
    assert await world.setup(live=False)
    registry = dr.async_get(hass)
    devices = dr.async_entries_for_config_entry(registry, world.entry.entry_id)
    assert {device.name for device in devices} == {
        "Zone 1 floor heating",
        "Zone 2 floor heating",
        "Floor heating",
    }
    assert all(device.entry_type is dr.DeviceEntryType.SERVICE for device in devices)
    entities = er.async_get(hass)
    climate = entities.async_get("climate.zone_1_floor_heating")
    request = entities.async_get("binary_sensor.floor_heating_heat_request")
    assert climate is not None
    assert request is not None
    zone_1 = registry.async_get(climate.device_id or "")
    assert zone_1 is not None
    assert zone_1.identifiers == {(DOMAIN, "zone_1")}
    global_device = registry.async_get(request.device_id or "")
    assert global_device is not None
    assert global_device.name == "Floor heating"


async def test_reload_restarts_the_control_and_keeps_settings(
    world: World, hass: HomeAssistant
) -> None:
    world.setup_entities()
    assert await world.setup()
    old = world.controller
    await old.async_set_zone_params(
        "zone_1", wait_time=old.settings.zone_params["zone_1"].wait_time
    )
    await old.async_set_heating_season(False)
    assert await hass.config_entries.async_reload(world.entry.entry_id)
    await hass.async_block_till_done()
    new = world.controller
    assert new is not old
    assert old._stopped  # nothing of the old instance runs on
    assert not new.settings.heating_season
    assert new.settings.control_active
    await world.advance(2)
    assert new.outputs is not None
    assert hass.states.get("switch.floor_heating_heating_season").state == "off"  # type: ignore[union-attr]


async def test_unload_stops_the_control(world: World, hass: HomeAssistant) -> None:
    world.setup_entities()
    assert await world.setup()
    controller = world.controller
    assert await hass.config_entries.async_unload(world.entry.entry_id)
    await hass.async_block_till_done()
    assert world.entry.state is ConfigEntryState.NOT_LOADED
    assert controller._stopped
    world.temp(1, 20.0)  # would make zone 1 wait, then heat
    world.calls.clear()
    await world.advance(90)
    assert world.calls == []  # no commands after the unload


async def test_removing_the_entry_keeps_the_stored_settings(
    world: World, hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    world.setup_entities()
    assert await world.setup()
    await world.controller.async_set_heating_season(False)
    assert await hass.config_entries.async_remove(world.entry.entry_id)
    await hass.async_block_till_done()
    assert not hass.config_entries.async_entries(DOMAIN)
    assert hass_storage[STORAGE_KEY]["data"]["settings"]["heating_season"] is False
    # The YAML is imported again (as at the next restart): the settings are back.
    await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_IMPORT}, data={})
    await hass.async_block_till_done()
    assert world.entry.state is ConfigEntryState.LOADED
    assert not world.controller.settings.heating_season
    assert world.controller.settings.control_active


async def test_missing_yaml_section_fails_the_entry_and_deletes_nothing(
    hass: HomeAssistant,
) -> None:
    entry = _entry(hass)
    assert await async_setup_component(hass, DOMAIN, {})
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert "No `multizone_floor_heating_manager:` section" in (entry.reason or "")
    assert hass.config_entries.async_entries(DOMAIN) == [entry]


async def test_a_removed_zone_loses_its_device(world: World, hass: HomeAssistant) -> None:
    entry = _entry(hass)
    registry = dr.async_get(hass)
    old = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "zone_3")},
        name="Zone 3 floor heating",
    )
    entities = er.async_get(hass)
    entities.async_get_or_create(
        "sensor", DOMAIN, "zone_3_reason", config_entry=entry, device_id=old.id
    )
    world.setup_entities()
    assert await world.setup(make_conf(2), live=False)
    assert registry.async_get(old.id) is None
    assert entities.async_get_entity_id("sensor", DOMAIN, "zone_3_reason") is None
    assert (
        registry.async_get_device_by_identifier((DOMAIN, "zone_1"), world.entry.entry_id)
        is not None
    )


async def test_renamed_zone_keeps_its_entity_ids(world: World, hass: HomeAssistant) -> None:
    """HA keeps an entity id once registered; the device name follows the YAML."""
    entry = _entry(hass)
    entities = er.async_get(hass)
    entities.async_get_or_create(
        "climate",
        DOMAIN,
        "zone_1",
        suggested_object_id="old_name_floor_heating",
        config_entry=entry,
    )
    world.setup_entities()
    assert await world.setup(live=False)
    assert hass.states.get("climate.old_name_floor_heating") is not None
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, "zone_1"), entry.entry_id)
    assert device is not None
    assert device.name == "Zone 1 floor heating"


def test_every_key_has_a_text() -> None:
    """Translations cover every state, reason and parameter (a guard for new keys)."""
    texts = json.loads((PACKAGE / "translations" / "en.json").read_text())
    entity = texts["entity"]
    assert set(entity["sensor"]["reason"]["state"]) == {reason.value for reason in Reason}
    assert set(entity["sensor"]["zone_state"]["state"]) == {mode.value for mode in ZoneMode}
    assert set(entity["sensor"]["mode"]["state"]) == {mode.value for mode in Mode}
    assert set(entity["sensor"]["heat_source"]["state"]) == {s.value for s in HeatSourceStatus}
    climate = entity["climate"]["zone"]["state_attributes"]
    assert climate["reason"]["state"] == entity["sensor"]["reason"]["state"]
    assert climate["zone_state"]["state"] == entity["sensor"]["zone_state"]["state"]
    assert set(entity["number"]) == set(GLOBAL_KEYS) | set(ZONE_KEYS) | {"schedule_temperature"}
    assert set(entity["select"]["schedule_days"]["state"]) == set(DAY_OPTIONS)
    assert set(entity["select"]["schedule_kind"]["state"]) == {k.value for k in ScheduleKind}
    assert set(texts["selector"]["weekday"]["options"]) == set(WEEKDAY_KEYS)
    assert texts["config"]["abort"]["yaml_only"]
    icons = json.loads((PACKAGE / "icons.json").read_text())
    for platform, keys in icons["entity"].items():
        assert set(keys) <= set(entity[platform]), platform


async def test_commands_still_reach_the_switches(world: World) -> None:
    """The entry-based setup controls the outputs like before."""
    world.setup_entities()
    world.temp(1, 21.8)
    assert await world.setup()
    await world.advance(31)
    assert (valve(1), "on") in world.calls
