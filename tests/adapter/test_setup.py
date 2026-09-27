"""YAML configuration and startup checks (docs/design.md §5.6; D-80, D-84, D-85, D-107, D-111)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM

from custom_components.floorheat.const import DATA_CONTROLLER, DOMAIN

from .conftest import World, make_conf, zone_conf


def _conf(**changes: Any) -> dict[str, Any]:
    conf = make_conf(2)
    conf[DOMAIN].update(changes)
    return conf


def _zones(*zones: dict[str, Any]) -> dict[str, Any]:
    return _conf(zones=list(zones))


async def test_valid_config_sets_up(world: World, caplog: pytest.LogCaptureFixture) -> None:
    world.setup_entities()
    assert await world.setup(make_conf(2, unvalved=(2,)), live=False)
    config = world.controller.config
    assert config.core.zone_ids == ("zone_1", "zone_2")
    assert config.valves == {"zone_1": "switch.valve_1"}
    assert config.zones[1].valve is None
    assert "Every zone has a valve" not in caplog.text
    assert "Unknown entities" not in caplog.text


async def test_all_valved_warning(world: World, caplog: pytest.LogCaptureFixture) -> None:
    world.setup_entities()
    assert await world.setup(make_conf(2), live=False)
    assert "Every zone has a valve" in caplog.text  # D-80


async def test_optional_keys(world: World) -> None:
    world.setup_entities()
    conf = _zones(
        zone_conf(1, sensor_offset=-0.4, power_sensor="sensor.valve_1_power"),
        zone_conf(2, valve=False),
    )
    conf[DOMAIN].update(
        plausible_min=5, plausible_max=35, reconcile_interval=30, output_mismatch_alert=5
    )
    assert await world.setup(conf, live=False)
    config = world.controller.config
    assert config.core.zones[0].sensor_offset == -0.4
    assert config.zones[0].power_sensor == "sensor.valve_1_power"
    assert (config.core.plausible_min, config.core.plausible_max) == (5, 35)
    assert config.reconcile_interval.total_seconds() == 30
    assert config.core.output_mismatch_alert == 5


@pytest.mark.parametrize(
    ("conf", "message"),
    [
        (_zones(zone_conf(1), zone_conf(1)), "duplicate zone id 'zone_1'"),
        (
            _zones(zone_conf(1), {**zone_conf(2), "name": " zone 1 "}),
            "duplicate zone name ' zone 1 '",
        ),
        (_zones({**zone_conf(1), "id": "Living Room"}), "(e.g. 'living_room')"),
        (_zones({**zone_conf(1), "valve": "light.valve"}), "expected a switch entity or 'none'"),
        (
            _zones({**zone_conf(1), "sensor": "switch.x"}),
            "'switch.x' does not belong to domain 'sensor'",
        ),
        (
            _zones({k: v for k, v in zone_conf(1).items() if k != "valve"}),
            "required key 'valve' not provided",
        ),
        (_zones({**zone_conf(1), "valve": "switch.heat_source"}), "mapped more than once"),
        (
            _zones(zone_conf(1), {**zone_conf(2), "valve": "switch.valve_1"}),
            "mapped more than once",
        ),
        (_conf(zones=[]), "length of value must be at least 1"),
        (_conf(reconcile_interval=5), "value must be at least 10"),
        (_conf(output_mismatch_alert=0), "value must be at least 1"),
        (_conf(watchdog_ping_url="x"), "'watchdog_ping_url' is an invalid option"),
        (_zones({**zone_conf(1), "sensor_offset": 6}), "sensor_offset: 6 °C is out of range"),
        (_conf(plausible_min=40, plausible_max=0), "plausible range"),
    ],
)
async def test_invalid_config_is_rejected_with_a_clear_error(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, conf: dict[str, Any], message: str
) -> None:
    assert not await async_setup_component(hass, DOMAIN, conf)
    assert message in caplog.text
    assert DATA_CONTROLLER not in hass.data


async def test_every_zone_error_is_reported(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    conf = _zones({**zone_conf(1), "id": "Bad 1"}, {**zone_conf(2), "id": "2bad"})
    assert not await async_setup_component(hass, DOMAIN, conf)
    assert "'Bad 1'" in caplog.text
    assert "'2bad'" in caplog.text


async def test_fahrenheit_config_is_converted(world: World, hass: HomeAssistant) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    world.setup_entities()
    conf = _zones(zone_conf(1, sensor_offset=-9), zone_conf(2))
    conf[DOMAIN].update(plausible_min=32, plausible_max=104)
    assert await world.setup(conf, live=False)
    core = world.controller.config.core
    assert core.zones[0].sensor_offset == pytest.approx(-5.0)
    assert core.plausible_min == pytest.approx(0.0)
    assert core.plausible_max == pytest.approx(40.0)


async def test_fahrenheit_offset_range(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    hass.config.units = US_CUSTOMARY_SYSTEM
    assert not await async_setup_component(hass, DOMAIN, _zones(zone_conf(1, sensor_offset=10)))
    assert "sensor_offset" in caplog.text


async def test_no_floorheat_key(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    assert DATA_CONTROLLER not in hass.data


async def test_unknown_entities_are_reported_but_control_runs(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    world.temp(1, 22.0)
    world.switch("switch.heat_source", "off")
    world.switch("switch.valve_1", "off")
    with patch(
        "custom_components.floorheat.controller.persistent_notification.async_create"
    ) as notify:
        assert await world.setup(make_conf(2), live=False)
    assert "Unknown entities in the floorheat configuration" in caplog.text
    [call] = notify.call_args_list
    message = call.args[1]
    assert "switch.valve_2" in message
    assert "sensor.zone_2_temperature" in message
    assert "sensor.zone_1_temperature" not in message
    assert call.kwargs["notification_id"] == f"{DOMAIN}_missing_entities"
    assert world.controller.outputs is not None  # the loop runs anyway
    assert world.mode(2) == "idle"
    assert world.reason(2) == "Waiting for a sensor reading"


async def test_registered_entity_without_state_is_known(world: World, hass: HomeAssistant) -> None:
    """An entity of an integration that has not loaded yet is not unknown (D-107)."""
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "sensor", "bthome", "abc", suggested_object_id="zone_2_temperature"
    )
    world.temp(1, 22.0)
    world.switch("switch.heat_source", "off")
    with patch(
        "custom_components.floorheat.controller.persistent_notification.async_create"
    ) as notify:
        assert await world.setup(make_conf(2, unvalved=(1, 2)), live=False)
    notify.assert_not_called()
