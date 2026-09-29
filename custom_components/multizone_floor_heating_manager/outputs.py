"""Output commands with backoff (docs/design.md §5.3, D-67, D-108).

The reconcile loop calls `apply` after every `step`, with the desired states computed
from the current actual states, so no stale command is ever sent (review E, D-95).
Per switch:
- actual equals desired: nothing is sent and the retry count resets;
- unavailable: nothing is sent or queued; the first command after it returns goes out
  at once (the core counts the unavailability for the alert, D-67);
- a new desired state: the command goes out at once;
- still not following: retries after 1, 2, 4 and 8 min, then every 15 min.

Commands run as tasks with a timeout, so a slow device never holds the reconcile lock
or blocks the event loop.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from homeassistant.const import ATTR_ENTITY_ID, SERVICE_TURN_OFF, SERVICE_TURN_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import COMMAND_BACKOFF, COMMAND_BACKOFF_REPEAT, COMMAND_TIMEOUT, DOMAIN
from .core.io import OutputState

_LOGGER = logging.getLogger(__name__)

SWITCH_DOMAIN = "switch"


@dataclass
class _Track:
    desired: bool | None = None
    sent: int = 0  # commands sent for `desired` while it did not follow
    next_at: datetime | None = None
    in_flight: bool = False


class OutputCommander:
    """Sends `switch.turn_on` / `switch.turn_off` with backoff."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass
        self._tracks: dict[str, _Track] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    def reset(self) -> None:
        """Forget retry state (e.g. when Control active changes)."""
        for track in self._tracks.values():
            track.desired, track.sent, track.next_at = None, 0, None

    def apply(
        self,
        desired: Mapping[str, bool],
        actual: Mapping[str, OutputState],
        now: datetime,
    ) -> None:
        """Command every switch in `desired` that differs from its actual state."""
        for entity_id, on in desired.items():
            track = self._tracks.setdefault(entity_id, _Track())
            state = actual[entity_id]
            if state is OutputState.UNAVAILABLE or state.is_on == on:
                track.desired, track.sent, track.next_at = None, 0, None
                continue
            if track.desired != on:
                track.desired, track.sent, track.next_at = on, 0, None
            if track.in_flight or (track.next_at is not None and now < track.next_at):
                continue
            delay = (
                COMMAND_BACKOFF[track.sent]
                if track.sent < len(COMMAND_BACKOFF)
                else COMMAND_BACKOFF_REPEAT
            )
            if track.sent:
                _LOGGER.info("%s does not follow; sending %s again", entity_id, _word(on))
            track.sent += 1
            track.next_at = now + delay
            self._send(entity_id, on, track)

    def _send(self, entity_id: str, on: bool, track: _Track) -> None:
        track.in_flight = True
        task = self._hass.async_create_task(
            self._async_call(entity_id, on, track), f"{DOMAIN} command {entity_id}"
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _async_call(self, entity_id: str, on: bool, track: _Track) -> None:
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT):
                await self._hass.services.async_call(
                    SWITCH_DOMAIN,
                    SERVICE_TURN_ON if on else SERVICE_TURN_OFF,
                    {ATTR_ENTITY_ID: entity_id},
                    blocking=True,
                )
        except (HomeAssistantError, TimeoutError) as err:
            _LOGGER.warning("Switching %s %s failed: %s", entity_id, _word(on), err)
        finally:
            track.in_flight = False

    async def async_cancel(self) -> None:
        """Cancel commands still running (on stop)."""
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)


def _word(on: bool) -> str:
    return "ON" if on else "OFF"
