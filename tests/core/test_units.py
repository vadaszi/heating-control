"""Temperature unit helpers for the adapter (docs/design.md §0.2, D-77)."""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from custom_components.multizone_floor_heating_manager.core.units import (
    TemperatureUnit,
    delta_from_celsius,
    delta_to_celsius,
    from_celsius,
    to_celsius,
)

C, F, K = TemperatureUnit.CELSIUS, TemperatureUnit.FAHRENHEIT, TemperatureUnit.KELVIN


def test_unit_strings_match_home_assistant() -> None:
    """Same strings as HA's UnitOfTemperature, so the adapter can map them directly."""
    assert [u.value for u in TemperatureUnit] == ["°C", "°F", "K"]


@pytest.mark.parametrize(
    ("value", "unit", "celsius"),
    [
        (21.5, C, 21.5),
        (32.0, F, 0.0),
        (212.0, F, 100.0),
        (71.6, F, 22.0),
        (273.15, K, 0.0),
        (295.15, K, 22.0),
    ],
)
def test_known_points(value: float, unit: TemperatureUnit, celsius: float) -> None:
    assert to_celsius(value, unit) == pytest.approx(celsius)
    assert from_celsius(celsius, unit) == pytest.approx(value)


@pytest.mark.parametrize(
    ("value", "unit", "celsius"),
    [(0.2, C, 0.2), (0.36, F, 0.2), (1.8, F, 1.0), (0.2, K, 0.2)],
)
def test_deltas_scale_without_offset(value: float, unit: TemperatureUnit, celsius: float) -> None:
    """Hysteresis, sensor offset and ManualResumeDelta are differences, not temperatures."""
    assert delta_to_celsius(value, unit) == pytest.approx(celsius)
    assert delta_from_celsius(celsius, unit) == pytest.approx(value)


@given(st.floats(min_value=-500, max_value=500), st.sampled_from(TemperatureUnit))
def test_round_trip(celsius: float, unit: TemperatureUnit) -> None:
    assert math.isclose(to_celsius(from_celsius(celsius, unit), unit), celsius, abs_tol=1e-9)
    assert math.isclose(
        delta_to_celsius(delta_from_celsius(celsius, unit), unit), celsius, abs_tol=1e-9
    )
