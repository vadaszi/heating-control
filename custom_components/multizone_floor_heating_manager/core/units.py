"""Temperature unit conversion for the adapter.

The core computes in °C. The adapter converts sensor readings and parameter values
from HA's unit system on the way in, and back for display.
"""

from __future__ import annotations

from enum import StrEnum

_KELVIN_OFFSET = 273.15


class TemperatureUnit(StrEnum):
    """Same strings as HA's `UnitOfTemperature`."""

    CELSIUS = "°C"
    FAHRENHEIT = "°F"
    KELVIN = "K"


def to_celsius(value: float, unit: TemperatureUnit) -> float:
    """Convert a temperature to °C."""
    if unit is TemperatureUnit.FAHRENHEIT:
        return (value - 32.0) * 5.0 / 9.0
    if unit is TemperatureUnit.KELVIN:
        return value - _KELVIN_OFFSET
    return value


def from_celsius(value: float, unit: TemperatureUnit) -> float:
    """Convert a temperature from °C."""
    if unit is TemperatureUnit.FAHRENHEIT:
        return value * 9.0 / 5.0 + 32.0
    if unit is TemperatureUnit.KELVIN:
        return value + _KELVIN_OFFSET
    return value


def delta_to_celsius(value: float, unit: TemperatureUnit) -> float:
    """Convert a temperature difference (hysteresis, offset, …) to °C."""
    return value * 5.0 / 9.0 if unit is TemperatureUnit.FAHRENHEIT else value


def delta_from_celsius(value: float, unit: TemperatureUnit) -> float:
    """Convert a temperature difference from °C."""
    return value * 9.0 / 5.0 if unit is TemperatureUnit.FAHRENHEIT else value
