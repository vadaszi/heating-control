"""Heartbeat client for the Shelly watchdog scripts (docs/design.md §5.4, D-120 to D-122).

Every HeartbeatInterval each Shelly listed under `shellys` gets a protocol v1 heartbeat
(docs/heartbeat-protocol.md): `POST http://<host>/script/<id>/heartbeat` with `{"v": 1}`,
or `{"v": 1, "season": <heating season>}` for the heat source script. Also in shadow mode
(D-56). The answer is the script's status; `core.heartbeat` decides what it means:
- a failed call (no connection, timeout, HTTP error, no usable status, wrong role or
  protocol version) counts; after HeartbeatFailAlert failures in a row one alert names
  the cause, and the next good answer is a recovery (D-61);
- the script parameters are compared with the expected values (D-73).

Only logged, never notified (owner, 2026-09-29): a watchdog that had timed out or was
running the failsafe operation, and a Shelly that restarted. The POST answer always shows
the state after the heartbeat, so on the first call after HA starts and after a failed
call the status is read with `GET` first (it does not count as a heartbeat).

Heartbeats go out only while the reconcile loop works: the last completed run is at most
3 ReconcileIntervals old (D-122). A broken loop therefore leads to the Shelly failsafe.

The heat source Shelly gets a heartbeat at once when the heating season changes, so the
season flag on the device follows without waiting for the next interval. A change while a
call to it is still running is sent as soon as that call has finished.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from aiohttp import ClientError, DigestAuthMiddleware
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, HEARTBEAT_CALL_TIMEOUT, SHELLY_USERNAME
from .controller import FloorheatController
from .core.heartbeat import (
    PROTOCOL_VERSION,
    FailureKind,
    ShellyRole,
    ShellyStatus,
    StatusError,
    param_differences,
    parse_status,
    record_failure,
    record_success,
)
from .schema import ShellyWiring

_LOGGER = logging.getLogger(__name__)


class _CallFailed(Exception):
    def __init__(self, kind: FailureKind, detail: str) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


def _duration(seconds: int) -> str:
    hours, minutes = divmod(seconds // 60, 60)
    return f"{hours} h {minutes} min" if hours else f"{minutes} min"


class HeartbeatClient:
    """Sends the heartbeats and hands the results to the controller."""

    def __init__(self, hass: HomeAssistant, controller: FloorheatController) -> None:
        self._hass = hass
        self._controller = controller
        self._config = controller.config
        self._busy: set[str] = set()  # Shellys with a call in progress
        self._check_first = {shelly.key for shelly in self._config.shellys}  # GET first
        self._uptime: dict[str, int] = {}
        # One digest auth middleware per Shelly with a password, so it can reuse the
        # device's nonce instead of a 401 round trip on every call.
        self._auth = {
            shelly.key: DigestAuthMiddleware(SHELLY_USERNAME, shelly.password)
            for shelly in self._config.shellys
            if shelly.password is not None
        }
        self._season_requested: bool | None = None  # last season sent to the heat source
        self._stalled = False
        self._stopped = False
        self._unsubs: list[CALLBACK_TYPE] = []
        self._tasks: set[asyncio.Task[None]] = set()

    @callback
    def async_start(self) -> None:
        """Send the first heartbeats and start the timer (once HA has started)."""
        if self._stopped or not self._config.shellys:
            return
        self._unsubs.append(
            async_track_time_interval(
                self._hass,
                self._on_tick,
                self._config.heartbeat_interval,
                name=f"{DOMAIN} heartbeat",
                cancel_on_shutdown=True,
            )
        )
        self._unsubs.append(self._controller.async_add_listener(self._on_update))
        self._send(self._config.shellys)

    async def async_stop(self) -> None:
        self._stopped = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # ------------------------------------------------------------ triggers

    @callback
    def _on_tick(self, _now: datetime) -> None:
        self._send(self._config.shellys)

    @callback
    def _on_update(self) -> None:
        """After every run: a changed heating season goes to the heat source at once."""
        season = self._controller.settings.heating_season
        if self._season_requested is not None and season != self._season_requested:
            self._send([s for s in self._config.shellys if s.role is ShellyRole.HEAT_SOURCE])

    def _alive(self) -> bool:
        """The reconcile loop has completed a run recently (D-122)."""
        alive = self._controller.loop_alive()
        last = self._controller.last_run_ok_at
        if not alive and not self._stalled:
            _LOGGER.warning(
                "No heartbeat sent to the Shellys: the reconcile loop has not completed a "
                "run since %s. The Shelly watchdogs act if this lasts for their timeout",
                "startup" if last is None else last.isoformat(),
            )
        elif alive and self._stalled:
            _LOGGER.info("The reconcile loop runs again; heartbeats resume")
        self._stalled = not alive
        return alive

    @callback
    def _send(self, shellys: list[ShellyWiring] | tuple[ShellyWiring, ...]) -> None:
        if self._stopped or not shellys or not self._alive():
            return
        for shelly in shellys:
            if shelly.key in self._busy:
                continue  # the previous call has not finished yet
            self._busy.add(shelly.key)
            if shelly.role is ShellyRole.HEAT_SOURCE:
                self._season_requested = self._controller.settings.heating_season
            task = self._hass.async_create_task(
                self._async_heartbeat(shelly, self._season_requested),
                f"{DOMAIN} heartbeat {shelly.name}",
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    # ------------------------------------------------------------ one heartbeat

    async def _async_heartbeat(self, shelly: ShellyWiring, season: bool | None) -> None:
        try:
            if shelly.key in self._check_first:
                await self._async_log_prior_state(shelly)
            body: dict[str, Any] = {"v": PROTOCOL_VERSION}
            if shelly.role is ShellyRole.HEAT_SOURCE:
                body["season"] = season
            tracking = self._controller.heartbeat[shelly.key]
            limit = self._config.heartbeat_fail_alert
            try:
                status = await self._async_call(shelly, "POST", body)
            except _CallFailed as err:
                _LOGGER.debug("Heartbeat to Shelly %s failed: %s", shelly.name, err.detail)
                self._check_first.add(shelly.key)
                tracking, events = record_failure(
                    tracking, shelly.name, err.kind, err.detail, limit
                )
            else:
                self._check_first.discard(shelly.key)
                self._log_answer(shelly, status)
                differences = param_differences(status.params, self._config.expected_params)
                tracking, events = record_success(tracking, shelly.name, differences)
            self._controller.async_update_heartbeat(shelly.key, tracking, events)
        finally:
            self._busy.discard(shelly.key)
            # A season change while this call ran was skipped by `_send`: send it now.
            if (
                shelly.role is ShellyRole.HEAT_SOURCE
                and self._controller.settings.heating_season != season
            ):
                self._send([shelly])

    async def _async_log_prior_state(self, shelly: ShellyWiring) -> None:
        """Read the status before the heartbeat resets it; log a timed-out watchdog."""
        try:
            status = await self._async_call(shelly, "GET")
        except _CallFailed:
            return  # the heartbeat that follows reports the failure
        if status.state == "timed_out":
            _LOGGER.warning(
                "Shelly %s: its watchdog had timed out (no heartbeat for %s) and put its "
                "outputs into the safe state; the heartbeat resumes now",
                shelly.name,
                _duration(status.heartbeat_age_s),
            )
        elif status.state == "failsafe":
            _LOGGER.warning(
                "Shelly %s: its watchdog was running the failsafe operation (no heartbeat "
                "for %s); the heartbeat resumes now and the integration takes over",
                shelly.name,
                _duration(status.heartbeat_age_s),
            )

    def _log_answer(self, shelly: ShellyWiring, status: ShellyStatus) -> None:
        previous = self._uptime.get(shelly.key)
        self._uptime[shelly.key] = status.uptime_s
        if previous is None:
            _LOGGER.info(
                "Shelly %s answers: %s watchdog, state %s, uptime %s",
                shelly.name,
                status.role.value,
                status.state,
                _duration(status.uptime_s),
            )
        elif status.uptime_s < previous:
            _LOGGER.warning(
                "Shelly %s has restarted (uptime %s)", shelly.name, _duration(status.uptime_s)
            )

    async def _async_call(
        self, shelly: ShellyWiring, method: str, body: dict[str, Any] | None = None
    ) -> ShellyStatus:
        """One call; raises `_CallFailed` with the cause."""
        session = async_get_clientsession(self._hass)
        kwargs: dict[str, Any] = {}
        if body is not None:
            kwargs["json"] = body
        if shelly.key in self._auth:
            kwargs["middlewares"] = (self._auth[shelly.key],)
        try:
            async with (
                asyncio.timeout(HEARTBEAT_CALL_TIMEOUT),
                session.request(method, shelly.url, **kwargs) as response,
            ):
                if response.status == 404:
                    raise _CallFailed(FailureKind.SCRIPT_NOT_RUNNING, "HTTP 404")
                if response.status == 401:
                    raise _CallFailed(FailureKind.AUTH_FAILED, "HTTP 401")
                if response.status != 200:
                    raise _CallFailed(FailureKind.BAD_ANSWER, f"HTTP {response.status}")
                try:
                    data = await response.json(content_type=None)
                except ValueError:
                    raise _CallFailed(FailureKind.BAD_ANSWER, "the answer is not JSON") from None
        except TimeoutError:
            raise _CallFailed(
                FailureKind.UNREACHABLE, f"no answer within {HEARTBEAT_CALL_TIMEOUT} s"
            ) from None
        except ClientError as err:
            raise _CallFailed(FailureKind.UNREACHABLE, str(err) or "connection error") from None
        try:
            return parse_status(data, shelly.role)
        except StatusError as err:
            raise _CallFailed(FailureKind.BAD_ANSWER, str(err)) from None
