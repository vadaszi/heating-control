"""External watchdog ping, e.g. healthchecks.io (docs/design.md §5.3, §5.11, D-62, D-155).

Every WatchdogPingInterval the integration calls the configured URL with `GET`. The
external service alerts when the pings stop: HA or the integration is dead. Like the
Shelly heartbeat, a ping goes out only while the reconcile loop works (D-122), so a
broken integration triggers the alert too; also in shadow mode (D-56).

A failed ping is only logged (owner, 2026-10-02): a warning at the first failure and an
info line when it works again; the external service alerts by itself when pings stop.
The URL is a secret and never appears in a log line.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from aiohttp import ClientError
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, WATCHDOG_PING_TIMEOUT
from .controller import FloorheatController

_LOGGER = logging.getLogger(__name__)


class WatchdogPing:
    """Pings the external watchdog URL while the integration works."""

    def __init__(self, hass: HomeAssistant, controller: FloorheatController) -> None:
        self._hass = hass
        self._controller = controller
        self._url = controller.config.watchdog_ping_url
        self._interval = controller.config.watchdog_ping_interval
        self._busy = False
        self._failing = False
        self._stalled = False
        self._stopped = False
        self._unsub: CALLBACK_TYPE | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @callback
    def async_start(self) -> None:
        """Send the first ping and start the timer (once HA has started)."""
        if self._stopped or self._url is None:
            return
        self._unsub = async_track_time_interval(
            self._hass,
            self._on_tick,
            self._interval,
            name=f"{DOMAIN} watchdog ping",
            cancel_on_shutdown=True,
        )
        self._send()

    async def async_stop(self) -> None:
        self._stopped = True
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    @callback
    def _on_tick(self, _now: datetime) -> None:
        self._send()

    def _alive(self) -> bool:
        alive = self._controller.loop_alive()
        if not alive and not self._stalled:
            _LOGGER.warning(
                "No watchdog ping sent: the reconcile loop has stopped completing runs. "
                "The external watchdog alerts if this lasts"
            )
        elif alive and self._stalled:
            _LOGGER.info("The reconcile loop runs again; watchdog pings resume")
        self._stalled = not alive
        return alive

    @callback
    def _send(self) -> None:
        if self._stopped or self._busy or not self._alive():
            return
        self._busy = True
        task = self._hass.async_create_task(self._async_ping(), f"{DOMAIN} watchdog ping")
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _async_ping(self) -> None:
        try:
            error = await self._async_call()
        finally:
            self._busy = False
        if error is not None and not self._failing:
            _LOGGER.warning("Watchdog ping failed: %s", error)
        elif error is None and self._failing:
            _LOGGER.info("Watchdog ping works again")
        self._failing = error is not None

    async def _async_call(self) -> str | None:
        """One ping; the cause of a failure (without the URL), or None."""
        assert self._url is not None
        session = async_get_clientsession(self._hass)
        try:
            async with (
                asyncio.timeout(WATCHDOG_PING_TIMEOUT),
                session.get(self._url) as response,
            ):
                if not 200 <= response.status < 300:
                    return f"HTTP {response.status}"
        except TimeoutError:
            return f"no answer within {WATCHDOG_PING_TIMEOUT} s"
        except ClientError as err:
            return type(err).__name__  # the message may contain the URL
        return None
