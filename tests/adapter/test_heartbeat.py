"""Heartbeat client (docs/design.md §5.4; D-61, D-73, D-120 to D-122; S6, S7, A21)."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from http import HTTPStatus
from typing import Any
from unittest.mock import patch

import pytest
from aiohttp import ClientError
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed,
    async_mock_service,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.floorheat.const import DOMAIN

from .conftest import HEAT_SOURCE, World, make_conf, valve
from .test_restart import restarted

VALVES = {"name": "Valves", "host": "192.0.2.11", "script_id": 1, "switches": [valve(1), valve(2)]}
HEAT = {"host": "192.0.2.12", "script_id": 2, "switches": [HEAT_SOURCE]}
VALVE_URL = "http://192.0.2.11/script/1/heartbeat"
HEAT_URL = "http://192.0.2.12/script/2/heartbeat"


def conf(**extra: Any) -> dict[str, Any]:
    return make_conf(2, shellys=[VALVES, HEAT], notify=["notify.phone"], **extra)


def status(role: str = "valve", **changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "v": 1,
        "role": role,
        "script_version": "1.0.0",
        "running": True,
        "state": "normal",
        "heartbeat_seen": True,
        "heartbeat_age_s": 0,
        "uptime_s": 86400,
        "switches": [],
        "params": {"heartbeat_timeout_s": 18000, "check_interval_s": 60},
    }
    if role == "heat_source":
        body["season"] = True
    body.update(changes)
    return body


def serve(
    mock: AiohttpClientMocker,
    valves: dict[str, Any] | None = None,
    heat: dict[str, Any] | None = None,
    valves_get: dict[str, Any] | None = None,
) -> None:
    """Register the answers of both Shellys (`GET` and `POST`); keeps the call log."""
    calls = list(mock.mock_calls)
    mock.clear_requests()
    mock.mock_calls.extend(calls)
    valves = valves or {"json": status()}
    heat = heat or {"json": status("heat_source")}
    mock.get(VALVE_URL, **(valves_get or valves))
    mock.post(VALVE_URL, **valves)
    mock.get(HEAT_URL, **heat)
    mock.post(HEAT_URL, **heat)


def calls(mock: AiohttpClientMocker, url: str) -> list[tuple[str, Any]]:
    """(method, body) of every call to `url`."""
    return [(method.upper(), data) for method, u, data, _ in mock.mock_calls if str(u) == url]


@pytest.fixture
def phone(hass: HomeAssistant) -> list[ServiceCall]:
    sent: list[ServiceCall] = async_mock_service(hass, "notify", "phone")
    return sent


def titles(phone: list[ServiceCall]) -> list[str]:
    return [call.data["title"] for call in phone]


async def test_heartbeat_is_sent_in_shadow_mode(
    world: World, aioclient_mock: AiohttpClientMocker
) -> None:
    serve(aioclient_mock)
    world.setup_entities()
    assert await world.setup(conf(), live=False)  # shadow mode (D-56)
    # First call after the start: status (GET), then the heartbeat (POST).
    assert calls(aioclient_mock, VALVE_URL) == [("GET", None), ("POST", {"v": 1})]
    assert calls(aioclient_mock, HEAT_URL) == [("GET", None), ("POST", {"v": 1, "season": True})]

    await world.advance(4)
    assert len(calls(aioclient_mock, VALVE_URL)) == 2
    await world.advance(1)  # HeartbeatInterval 5 min; no GET while calls succeed
    assert calls(aioclient_mock, VALVE_URL)[2:] == [("POST", {"v": 1})]
    assert calls(aioclient_mock, HEAT_URL)[2:] == [("POST", {"v": 1, "season": True})]


async def test_no_heartbeat_without_shellys(
    world: World, aioclient_mock: AiohttpClientMocker
) -> None:
    world.setup_entities()
    assert await world.setup()
    await world.advance(10)
    assert aioclient_mock.call_count == 0


async def test_season_change_reaches_the_heat_source_at_once(
    world: World, aioclient_mock: AiohttpClientMocker
) -> None:
    serve(aioclient_mock)
    world.setup_entities()
    assert await world.setup(conf())
    await world.controller.async_set_heating_season(False)
    await world.hass.async_block_till_done()
    assert calls(aioclient_mock, HEAT_URL)[-1] == ("POST", {"v": 1, "season": False})
    assert len(calls(aioclient_mock, VALVE_URL)) == 2  # valves only on the timer
    await world.advance(1)
    assert len(calls(aioclient_mock, HEAT_URL)) == 3  # sent once per change


async def test_s07_alert_after_three_failures_and_recovery(
    world: World,
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    phone: list[ServiceCall],
) -> None:
    serve(aioclient_mock, valves={"exc": ClientError("connection refused")})
    world.setup_entities()
    assert await world.setup(conf())
    await world.advance(10)  # calls at 0, 5 and 10 min
    assert titles(phone) == ["floorheat: Shelly watchdog not answering"]
    assert (
        phone[0]
        .data["message"]
        .startswith(
            "Shelly Valves is unreachable (connection refused); 3 heartbeats in a row failed."
        )
    )
    # every call after a failure reads the status first
    assert [m for m, _ in calls(aioclient_mock, VALVE_URL)] == ["GET", "POST"] * 3
    alerts = hass.states.get("sensor.floorheat_alerts")
    assert alerts is not None
    assert alerts.state == "1"
    assert alerts.attributes["alerts"] == [
        {"kind": "watchdog_failed", "zone_id": None, "message": "Shelly Valves is unreachable."}
    ]

    await world.advance(5)
    assert len(phone) == 1  # once

    serve(aioclient_mock)
    await world.advance(5)
    assert titles(phone)[1:] == ["floorheat: Shelly watchdog answering again"]
    assert (
        phone[1].data["message"] == "Shelly Valves answers again; its watchdog script is running."
    )
    assert hass.states.get("sensor.floorheat_alerts").state == "0"  # type: ignore[union-attr]


async def test_two_failures_do_not_alert(
    world: World, aioclient_mock: AiohttpClientMocker, phone: list[ServiceCall]
) -> None:
    serve(aioclient_mock, valves={"exc": ClientError()})
    world.setup_entities()
    assert await world.setup(conf())
    await world.advance(5)
    serve(aioclient_mock)
    await world.advance(5)
    serve(aioclient_mock, valves={"exc": ClientError()})
    await world.advance(10)
    assert phone == []  # never 3 in a row


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (
            {"status": HTTPStatus.NOT_FOUND},
            "The watchdog script is not running on Shelly Valves (HTTP 404: script stopped, "
            "or wrong script_id)",
        ),
        (
            {"status": HTTPStatus.UNAUTHORIZED},
            "Shelly Valves rejects the heartbeat (HTTP 401): check the password",
        ),
        (
            {"status": HTTPStatus.BAD_REQUEST},
            "Shelly Valves does not answer as expected (HTTP 400)",
        ),
        ({"text": "<html>"}, "Shelly Valves does not answer as expected (the answer is not JSON)"),
        (
            {"json": status("heat_source")},
            "Shelly Valves does not answer as expected (it runs the heat_source script, "
            "expected the valve script)",
        ),
        (
            {"json": status(v=2)},
            "Shelly Valves does not answer as expected (protocol v2 is not supported",
        ),
        ({"exc": TimeoutError()}, "Shelly Valves is unreachable (no answer within 10 s)"),
        ({"exc": ClientError()}, "Shelly Valves is unreachable (connection error)"),
    ],
)
async def test_failure_messages_name_the_cause(
    world: World,
    aioclient_mock: AiohttpClientMocker,
    phone: list[ServiceCall],
    answer: dict[str, Any],
    message: str,
) -> None:
    serve(aioclient_mock, valves=answer)
    world.setup_entities()
    assert await world.setup(conf(heartbeat_fail_alert=1))
    [call] = phone
    assert call.data["message"].startswith(message)


async def test_hanging_shelly_times_out_without_blocking(
    world: World, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, phone: list[ServiceCall]
) -> None:
    called = asyncio.Event()

    async def hang(*_args: Any) -> None:
        called.set()
        await asyncio.Event().wait()

    serve(aioclient_mock, valves={"side_effect": hang}, valves_get={"json": status()})
    world.setup_entities()
    await hass.async_block_till_done()
    assert await async_setup_component(hass, DOMAIN, conf(heartbeat_fail_alert=1))
    await called.wait()  # the heartbeat to the valve Shelly hangs
    assert world.controller.last_run_ok_at is not None  # the reconcile loop ran meanwhile
    assert phone == []
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))  # call timeout
    await hass.async_block_till_done()
    [call] = phone
    assert call.data["message"].startswith("Shelly Valves is unreachable (no answer within 10 s)")


async def test_params_mismatch_is_alerted_once(
    world: World,
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    phone: list[ServiceCall],
) -> None:
    changed = status(params={"heartbeat_timeout_s": 120, "check_interval_s": 5})
    serve(aioclient_mock, valves={"json": changed})
    world.setup_entities()
    assert await world.setup(conf())  # check_interval not configured: not compared
    assert titles(phone) == ["floorheat: Shelly script parameters differ"]
    assert "heartbeat_timeout_s is 120, expected 18000." in phone[0].data["message"]
    assert "check_interval_s" not in phone[0].data["message"]
    await world.advance(10)
    assert len(phone) == 1
    assert hass.states.get("sensor.floorheat_alerts").state == "1"  # type: ignore[union-attr]

    serve(aioclient_mock)
    await world.advance(5)
    assert len(phone) == 1  # cleared without a notification
    assert hass.states.get("sensor.floorheat_alerts").state == "0"  # type: ignore[union-attr]


async def test_check_interval_is_compared_when_configured(
    world: World, aioclient_mock: AiohttpClientMocker, phone: list[ServiceCall]
) -> None:
    serve(aioclient_mock)  # check_interval_s 60
    world.setup_entities()
    assert await world.setup(conf(heartbeat_check_interval=30, heartbeat_timeout=7200))
    assert len(phone) == 2  # both Shellys
    assert (
        "heartbeat_timeout_s is 18000, expected 7200; check_interval_s is 60, expected 30"
        in phone[0].data["message"]
    )


async def test_timed_out_watchdog_and_restart_are_only_logged(
    world: World,
    aioclient_mock: AiohttpClientMocker,
    phone: list[ServiceCall],
    caplog: pytest.LogCaptureFixture,
) -> None:
    timed_out = status(state="timed_out", heartbeat_age_s=18060, heartbeat_seen=False)
    serve(aioclient_mock, valves={"json": status()}, valves_get={"json": timed_out})
    world.setup_entities()
    assert await world.setup(conf())
    assert "Shelly Valves: its watchdog had timed out (no heartbeat for 5 h 1 min)" in caplog.text
    assert "Shelly Valves answers: valve watchdog, state normal, uptime 24 h 0 min" in caplog.text

    serve(aioclient_mock, valves={"json": status(uptime_s=20)})
    await world.advance(5)
    assert "Shelly Valves has restarted (uptime 0 min)" in caplog.text
    assert phone == []  # owner decision: logged only


async def test_no_heartbeat_while_the_reconcile_loop_is_broken(
    world: World, aioclient_mock: AiohttpClientMocker, caplog: pytest.LogCaptureFixture
) -> None:
    serve(aioclient_mock)
    world.setup_entities()
    assert await world.setup(conf())
    sent = aioclient_mock.call_count
    with patch.object(world.controller, "_run"):  # runs no longer complete (D-122)
        await world.advance(10)
    # last completed run 06:00: at 06:05 it is more than 3 intervals old
    assert aioclient_mock.call_count == sent
    assert caplog.text.count("No heartbeat sent to the Shellys") == 1  # logged once
    await world.advance(5)
    assert "heartbeats resume" in caplog.text
    assert aioclient_mock.call_count == sent + 2


async def test_password_uses_digest_auth(world: World, aioclient_mock: AiohttpClientMocker) -> None:
    serve(aioclient_mock)
    world.setup_entities()
    with patch("custom_components.floorheat.heartbeat.DigestAuthMiddleware") as digest:
        assert await world.setup(make_conf(2, shellys=[{**VALVES, "password": "pw"}, HEAT]))
    digest.assert_called_once_with("admin", "pw")  # the valve Shelly only


async def test_alert_state_survives_a_restart(
    world: World, aioclient_mock: AiohttpClientMocker, phone: list[ServiceCall]
) -> None:
    serve(aioclient_mock, valves={"exc": ClientError()})
    world.setup_entities()
    assert await world.setup(conf())
    await world.advance(10)
    assert len(phone) == 1

    def prepare(new: World) -> None:
        new.setup_entities()

    async with restarted(world, prepare=prepare) as new:
        new_phone = async_mock_service(new.hass, "notify", "phone")
        assert await new.setup(conf(), live=False)
        await new.advance(10)
        assert new_phone == []  # still failing: not alerted again
        serve(aioclient_mock)
        await new.advance(5)
        assert [c.data["title"] for c in new_phone] == [
            "floorheat: Shelly watchdog answering again"
        ]
