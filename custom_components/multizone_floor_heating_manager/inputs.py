"""Reading HA states into core inputs (docs/design.md §5.3, D-66, D-77, D-107).

- Sensors: the numeric state converted from the sensor's own unit to °C, and the state's
  `last_reported` (it moves when a value repeats; `last_updated` does not).
- Switches: `on` / `off`; anything else (unavailable, unknown, missing) is UNAVAILABLE,
  which the core counts as OFF.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime

from homeassistant.const import ATTR_UNIT_OF_MEASUREMENT, STATE_OFF, STATE_ON
from homeassistant.core import State

from .core.io import OutputState
from .core.units import TemperatureUnit, to_celsius

_LOGGER = logging.getLogger(__name__)

_UNITS = {unit.value: unit for unit in TemperatureUnit}


class SensorReader:
    """Reads temperature sensors; warns once per sensor without a temperature unit."""

    def __init__(self) -> None:
        self._warned: set[str] = set()

    def read(self, entity_id: str, state: State | None) -> tuple[float | None, datetime | None]:
        """Raw reading in °C (None if not a number) and `last_reported`."""
        if state is None:
            return None, None
        try:
            value = float(state.state)
        except ValueError:
            return None, state.last_reported
        if not math.isfinite(value):
            return None, state.last_reported
        unit = _UNITS.get(str(state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)))
        if unit is None:
            if entity_id not in self._warned:
                self._warned.add(entity_id)
                _LOGGER.warning(
                    "%s has no temperature unit (°C, °F or K); its readings are ignored",
                    entity_id,
                )
            return None, state.last_reported
        self._warned.discard(entity_id)
        return to_celsius(value, unit), state.last_reported


def read_switch(state: State | None) -> OutputState:
    """Actual switch state; unavailable, unknown or missing is UNAVAILABLE (D-66)."""
    if state is not None and state.state == STATE_ON:
        return OutputState.ON
    if state is not None and state.state == STATE_OFF:
        return OutputState.OFF
    return OutputState.UNAVAILABLE
