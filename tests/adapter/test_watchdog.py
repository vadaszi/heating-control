"""External watchdog ping."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.multizone_floor_heating_manager.const import DOMAIN

from .conftest import World, make_conf

URL = "https://hc-ping.example/0000-placeholder"


def conf(**extra: Any) -> dict[str, Any]:
    return make_conf(2, watchdog_ping_url=URL, **extra)


def pings(mock: AiohttpClientMocker) -> int:
    return sum(1 for method, url, _data, _headers in mock.mock_calls if str(url) == URL)


def answer(mock: AiohttpClientMocker, **kwargs: Any) -> None:
    """Register the answer of the ping URL; keeps the call log."""
    calls = list(mock.mock_calls)
    mock.clear_requests()
    mock.mock_calls.extend(calls)
    mock.get(URL, **kwargs)


async def test_ping_on_schedule_also_in_shadow_mode(
    world: World, aioclient_mock: AiohttpClientMocker
) -> None:
    answer(aioclient_mock)
    world.setup_entities()
    assert await world.setup(conf(), live=False)  # shadow mode
    assert pings(aioclient_mock) == 1  # at start
    assert aioclient_mock.mock_calls[0][0].upper() == "GET"
    await world.advance(4)
    assert pings(aioclient_mock) == 1
    await world.advance(1)  # WatchdogPingInterval 5 min
    assert pings(aioclient_mock) == 2
    await world.advance(10)
    assert pings(aioclient_mock) == 4


async def test_configured_interval(world: World, aioclient_mock: AiohttpClientMocker) -> None:
    answer(aioclient_mock)
    world.setup_entities()
    assert await world.setup(conf(watchdog_ping_interval=60))
    await world.advance(3)
    assert pings(aioclient_mock) == 4


async def test_no_url_no_ping(world: World, aioclient_mock: AiohttpClientMocker) -> None:
    world.setup_entities()
    assert await world.setup()
    await world.advance(10)
    assert aioclient_mock.call_count == 0


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ({"watchdog_ping_url": "hc-ping.example/uuid"}, "expected an http:// or https:// URL"),
        ({"watchdog_ping_url": URL, "watchdog_ping_interval": 59}, "watchdog_ping_interval"),
        ({"watchdog_ping_url": URL, "watchdog_ping_interval": 3601}, "watchdog_ping_interval"),
    ],
)
async def test_invalid_keys_fail_the_setup(
    hass: HomeAssistant, extra: dict[str, Any], message: str, caplog: pytest.LogCaptureFixture
) -> None:
    assert not await async_setup_component(hass, DOMAIN, make_conf(2, **extra))
    assert message in caplog.text


async def test_failure_is_logged_once_without_the_url_and_recovery(
    world: World, aioclient_mock: AiohttpClientMocker, caplog: pytest.LogCaptureFixture
) -> None:
    answer(aioclient_mock, status=404)
    world.setup_entities()
    assert await world.setup(conf())
    await world.advance(10)
    assert pings(aioclient_mock) == 3
    assert caplog.text.count("Watchdog ping failed: HTTP 404") == 1
    answer(aioclient_mock, exc=ClientError(f"Cannot connect to host {URL}"))
    await world.advance(5)
    assert "Watchdog ping failed" in caplog.text
    assert caplog.text.count("Watchdog ping failed") == 1  # still failing: not repeated
    answer(aioclient_mock)
    await world.advance(5)
    assert "Watchdog ping works again" in caplog.text
    answer(aioclient_mock, exc=ClientError(f"Cannot connect to host {URL}"))
    await world.advance(5)
    assert "Watchdog ping failed: ClientError" in caplog.text
    assert URL not in caplog.text  # a secret
    assert "hc-ping" not in caplog.text


async def test_a_hanging_ping_times_out_without_blocking(
    world: World,
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
) -> None:
    called = asyncio.Event()

    async def hang(*_args: Any) -> None:
        called.set()
        await asyncio.Event().wait()

    answer(aioclient_mock, side_effect=hang)
    world.setup_entities()
    await hass.async_block_till_done()
    assert await async_setup_component(hass, DOMAIN, conf())
    await called.wait()
    assert world.controller.last_run_ok_at is not None  # the reconcile loop ran meanwhile
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()
    assert "Watchdog ping failed: no answer within 10 s" in caplog.text


async def test_no_ping_while_the_reconcile_loop_is_broken(
    world: World, aioclient_mock: AiohttpClientMocker, caplog: pytest.LogCaptureFixture
) -> None:
    answer(aioclient_mock)
    world.setup_entities()
    assert await world.setup(conf())
    with patch.object(world.controller, "_run"):  # runs no longer complete
        await world.advance(10)
    # only the one at the start: at 06:05 the last completed run (06:00) is too old
    assert pings(aioclient_mock) == 1
    assert caplog.text.count("No watchdog ping sent") == 1
    await world.advance(5)
    assert "watchdog pings resume" in caplog.text
    assert pings(aioclient_mock) == 2


async def test_unload_stops_the_ping(
    world: World, hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    answer(aioclient_mock)
    world.setup_entities()
    assert await world.setup(conf())
    [entry] = hass.config_entries.async_entries(DOMAIN)
    assert await hass.config_entries.async_unload(entry.entry_id)
    await world.advance(10)
    assert pings(aioclient_mock) == 1
