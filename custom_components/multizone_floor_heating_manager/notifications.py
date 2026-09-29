"""Core events → notifications (docs/design.md §3.6 table, D-23, D-98, D-117).

Each event goes to every configured target `notify.<name>`: the legacy notify service of
that name if it exists (e.g. the companion app, SMTP), otherwise the notify entity with
that id (`notify.send_message`). Calls run as tasks with a timeout; a failure is logged
and never stops the control loop. Which events exist in shadow mode is the core's
decision (sensor faults yes, output mismatch no).
"""

from __future__ import annotations

import asyncio
import logging

import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN, NOTIFY_TIMEOUT, SHORT_NAME
from .core.io import Event, EventKind

_LOGGER = logging.getLogger(__name__)

NOTIFY_DOMAIN = "notify"
MISSING_TARGETS_NOTIFICATION = f"{DOMAIN}_missing_notify_targets"

TITLES = {
    EventKind.SENSOR_FAULT_STARTED: "Floor heating: sensor fault",
    EventKind.SENSOR_FAULT_REMINDER: "Floor heating: sensor fault reminder",
    EventKind.SENSOR_FAULT_RECOVERED: "Floor heating: sensor recovered",
    EventKind.OUTPUT_MISMATCH: "Floor heating: switch not following command",
    EventKind.OUTPUT_MISMATCH_RECOVERED: "Floor heating: switch following again",
    EventKind.WATCHDOG_FAILED: "Floor heating: Shelly watchdog not answering",
    EventKind.WATCHDOG_RECOVERED: "Floor heating: Shelly watchdog answering again",
    EventKind.WATCHDOG_PARAMS_MISMATCH: "Floor heating: Shelly script parameters differ",
}


class Notifier:
    """Sends core events to the configured notify targets."""

    def __init__(self, hass: HomeAssistant, targets: tuple[str, ...]) -> None:
        self._hass = hass
        self._targets = targets
        self._warned: set[str] = set()

    def _exists(self, target: str) -> bool:
        name = target.split(".", 1)[1]
        return self._hass.services.has_service(NOTIFY_DOMAIN, name) or (
            self._hass.states.get(target) is not None
        )

    @callback
    def async_check_targets(self) -> None:
        """Report targets that do not exist (once HA has started, D-107)."""
        missing = [target for target in self._targets if not self._exists(target)]
        if not missing:
            persistent_notification.async_dismiss(self._hass, MISSING_TARGETS_NOTIFICATION)
            return
        listed = ", ".join(missing)
        _LOGGER.warning("Notify targets not found: %s", listed)
        persistent_notification.async_create(
            self._hass,
            "These notify targets in the Multizone Floor Heating Manager configuration do not "
            f"exist: {listed}. Floor heating notifications will not reach them.",
            title="Floor heating: unknown notify targets",
            notification_id=MISSING_TARGETS_NOTIFICATION,
        )

    @callback
    def async_handle(self, event: Event) -> None:
        """Send `event` to every target (a controller event handler)."""
        title = TITLES.get(event.kind, SHORT_NAME)
        for target in self._targets:
            self._hass.async_create_task(
                self._async_send(target, title, event.message), f"{DOMAIN} notify {target}"
            )

    async def _async_send(self, target: str, title: str, message: str) -> None:
        name = target.split(".", 1)[1]
        try:
            async with asyncio.timeout(NOTIFY_TIMEOUT):
                if self._hass.services.has_service(NOTIFY_DOMAIN, name):
                    await self._hass.services.async_call(
                        NOTIFY_DOMAIN, name, {"title": title, "message": message}, blocking=True
                    )
                elif self._hass.states.get(target) is not None:
                    await self._hass.services.async_call(
                        NOTIFY_DOMAIN,
                        "send_message",
                        {ATTR_ENTITY_ID: target, "title": title, "message": message},
                        blocking=True,
                    )
                elif target not in self._warned:
                    self._warned.add(target)
                    _LOGGER.warning("Notify target %s not found; notification dropped", target)
        except (HomeAssistantError, TimeoutError, vol.Invalid) as err:
            _LOGGER.warning("Notification to %s failed: %s", target, err)
