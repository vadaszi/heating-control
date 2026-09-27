"""Notifications and the alerts sensor (docs/design.md §3.6, §3.9; D-98, D-117)."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.floorheat.const import DOMAIN

from .conftest import World, make_conf, valve


def _conf(*targets: str, **extra: Any) -> dict[str, Any]:
    conf = make_conf(2, **extra)
    conf[DOMAIN]["notify"] = list(targets)
    return conf


async def _fault_zone_1(world: World) -> None:
    """Zone 1's sensor goes silent: SENSOR_FAULT after 60 min."""
    world.hass.states.async_set("sensor.zone_1_temperature", "unavailable")
    await world.advance(61)


async def test_sensor_fault_is_notified(world: World, hass: HomeAssistant) -> None:
    phone = async_mock_service(hass, "notify", "mobile_app_phone")
    email = async_mock_service(hass, "notify", "email")
    world.setup_entities()
    assert await world.setup(_conf("notify.mobile_app_phone", "notify.email"), live=False)
    await _fault_zone_1(world)  # shadow mode notifies sensor faults too (D-98)
    [call] = phone
    assert call.data["title"] == "floorheat: sensor fault"
    assert call.data["message"].startswith("Sensor fault in Zone 1")
    assert len(email) == 1

    world.temp(1, 22.0)
    await hass.async_block_till_done()
    assert phone[-1].data["title"] == "floorheat: sensor recovered"


async def test_notify_entity_target(world: World, hass: HomeAssistant) -> None:
    sent = async_mock_service(hass, "notify", "send_message")
    hass.states.async_set("notify.family_chat", "unknown")
    world.setup_entities()
    assert await world.setup(_conf("notify.family_chat"), live=False)
    await _fault_zone_1(world)
    [call] = sent
    assert call.data["entity_id"] == "notify.family_chat"
    assert call.data["title"] == "floorheat: sensor fault"


async def test_output_mismatch_is_notified_once_with_recovery(
    world: World, hass: HomeAssistant
) -> None:
    phone = async_mock_service(hass, "notify", "phone")
    world.setup_entities()
    assert await world.setup(_conf("notify.phone"))
    world.switch(valve(1), "unavailable")
    await world.advance(10)
    assert [c.data["title"] for c in phone] == ["floorheat: output not following command"]
    assert phone[0].data["message"].startswith("The valve of Zone 1 does not follow")
    world.switch(valve(1), "off")
    await world.advance(1)
    assert phone[-1].data["title"] == "floorheat: output recovered"


async def test_missing_target_is_reported_and_skipped(
    world: World, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    world.setup_entities()
    assert await world.setup(_conf("notify.nowhere"), live=False)
    assert "Notify targets not found: notify.nowhere" in caplog.text
    await _fault_zone_1(world)
    world.temp(1, 22.0)
    await hass.async_block_till_done()
    assert caplog.text.count("Notify target notify.nowhere not found") == 1
    assert world.mode(1) == "idle"  # control went on


async def test_failing_notify_service_is_logged(
    world: World, hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    async def fail(call: ServiceCall) -> None:
        raise HomeAssistantError("SMTP down")

    hass.services.async_register("notify", "email", fail)
    world.setup_entities()
    assert await world.setup(_conf("notify.email"), live=False)
    await _fault_zone_1(world)
    assert "Notification to notify.email failed: SMTP down" in caplog.text
    assert world.mode(1) == "sensor_fault"


async def test_without_targets_events_are_logged_only(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    world.setup_entities()
    assert await world.setup(live=False)
    await _fault_zone_1(world)
    assert "Sensor fault in Zone 1" in caplog.text


async def test_alerts_sensor(world: World, hass: HomeAssistant) -> None:
    world.setup_entities()
    assert await world.setup()
    await _fault_zone_1(world)
    world.switch(valve(2), "unavailable")
    await world.advance(3)
    alerts = hass.states.get("sensor.floorheat_alerts")
    assert alerts is not None
    assert alerts.state == "2"
    assert alerts.attributes["alerts"] == [
        {"kind": "sensor_fault_started", "zone_id": "zone_1", "message": "Sensor fault in Zone 1."},
        {
            "kind": "output_mismatch",
            "zone_id": "zone_2",
            "message": "The valve of Zone 2 does not follow its command.",
        },
    ]
    world.temp(1, 22.0)
    world.switch(valve(2), "off")
    await world.advance(1)
    alerts = hass.states.get("sensor.floorheat_alerts")
    assert alerts is not None
    assert alerts.state == "0"
    assert alerts.attributes["alerts"] == []


@pytest.mark.parametrize("target", ["mobile_app_phone", "notify.Phone", "light.x", "notify."])
async def test_invalid_notify_target(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture, target: str
) -> None:
    assert not await async_setup_component(hass, DOMAIN, _conf(target))
    assert "expected a notify target like 'notify.mobile_app_phone'" in caplog.text
