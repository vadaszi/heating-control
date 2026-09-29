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

from .conftest import World, make_conf, no_watchdog_for_all, zone_conf


def _conf(**changes: Any) -> dict[str, Any]:
    conf = make_conf(2)
    conf[DOMAIN].update(changes)
    if "no_watchdog" not in changes and "shellys" not in changes:
        no_watchdog_for_all(conf[DOMAIN])
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
    no_watchdog_for_all(conf[DOMAIN])
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
    assert world.reason(2) == "no_reading_yet"


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


# ---------------------------------------------------------------- Shellys (D-118, D-120)

_VALVES = {"host": "192.0.2.11", "script_id": 1, "switches": ["switch.valve_1", "switch.valve_2"]}
_HEAT = {"name": "Heat", "host": "192.0.2.12", "script_id": 1, "switches": ["switch.heat_source"]}


def _shellys(*shellys: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return make_conf(2, shellys=list(shellys), **extra)


async def test_shelly_wiring(world: World) -> None:
    world.setup_entities()
    conf = _shellys(
        {**_VALVES, "password": "pw"},
        heartbeat_interval=120,
        heartbeat_fail_alert=2,
        heartbeat_timeout=3600,
        heartbeat_check_interval=30,
        no_watchdog=["switch.heat_source"],
    )
    with patch("custom_components.floorheat.heartbeat.HeartbeatClient.async_start"):
        assert await world.setup(conf, live=False)  # no calls: only the wiring is tested
    config = world.controller.config
    [shelly] = config.shellys
    assert shelly.name == "192.0.2.11"  # defaults to the host
    assert shelly.role.value == "valve"
    assert shelly.switches == ("switch.valve_1", "switch.valve_2")
    assert shelly.password == "pw"
    assert shelly.url == "http://192.0.2.11/script/1/heartbeat"
    assert config.heartbeat_interval.total_seconds() == 120
    assert config.heartbeat_fail_alert == 2
    assert (
        config.expected_params.heartbeat_timeout_s,
        config.expected_params.check_interval_s,
    ) == (
        3600,
        30,
    )


async def test_heat_source_shelly_role(world: World) -> None:
    world.setup_entities()
    with patch("custom_components.floorheat.heartbeat.HeartbeatClient.async_start"):
        assert await world.setup(_shellys(_VALVES, _HEAT), live=False)
    roles = {s.name: s.role.value for s in world.controller.config.shellys}
    assert roles == {"192.0.2.11": "valve", "Heat": "heat_source"}
    assert world.controller.config.expected_params.check_interval_s is None


@pytest.mark.parametrize(
    ("conf", "message"),
    [
        (
            make_conf(2, no_watchdog=["switch.heat_source"]),
            "switch switch.valve_1 has no watchdog: add it to the switches of its Shelly",
        ),
        (
            make_conf(
                2,
                no_watchdog=["switch.heat_source", "switch.valve_1", "switch.valve_2", "switch.x"],
            ),
            "no_watchdog: switch.x is not a mapped switch",
        ),
        (
            _shellys(
                {**_VALVES, "switches": ["switch.valve_1", "switch.x"]},
                _HEAT,
                no_watchdog=["switch.valve_2"],
            ),
            "shelly 192.0.2.11: switch.x is not a mapped switch",
        ),
        (
            _shellys(
                _VALVES,
                {**_HEAT, "switches": ["switch.valve_1"]},
                no_watchdog=["switch.heat_source"],
            ),
            "switch switch.valve_1 is listed on more than one Shelly",
        ),
        (
            _shellys(_VALVES, _HEAT, no_watchdog=["switch.valve_2"]),
            "switch switch.valve_2 is on Shelly 192.0.2.11 and in no_watchdog",
        ),
        (
            _shellys(
                {**_HEAT, "switches": ["switch.heat_source", "switch.valve_1", "switch.valve_2"]}
            ),
            "shelly Heat: the heat source switch needs a Shelly of its own",
        ),
        (
            _shellys(_VALVES, {**_HEAT, "host": "192.0.2.11"}),
            "shellys: 192.0.2.11 script 1 is listed more than once",
        ),
        (
            _shellys({**_VALVES, "name": "heat "}, _HEAT),
            "shellys: the name 'heat' is used more than once",
        ),
        (
            _shellys(_VALVES, _HEAT, heartbeat_interval=600, heartbeat_timeout=600),
            "heartbeat_interval must be shorter than heartbeat_timeout",
        ),
        (_shellys({**_VALVES, "host": "http://192.0.2.11"}, _HEAT), "expected a host name"),
        (_shellys({**_VALVES, "script_id": 0}, _HEAT), "value must be at least 1"),
        (_shellys(_VALVES, _HEAT, heartbeat_interval=30), "value must be at least 60"),
        (_shellys(_VALVES, _HEAT, heartbeat_fail_alert=0), "value must be at least 1"),
    ],
)
async def test_invalid_shelly_config_is_rejected(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, conf: dict[str, Any], message: str
) -> None:
    assert not await async_setup_component(hass, DOMAIN, conf)
    assert message in caplog.text
