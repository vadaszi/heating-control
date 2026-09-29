"""Config and parameter validation (docs/design.md §4, §5.6, D-76, D-80, D-81, D-84…D-86)."""

from __future__ import annotations

import dataclasses
import math
from datetime import time, timedelta
from typing import Any

import pytest

from custom_components.multizone_floor_heating_manager.core.config import (
    GLOBAL_PARAM_SPECS,
    PARAM_SPECS,
    SENSOR_OFFSET_SPEC,
    ZONE_PARAM_SPECS,
    ConfigError,
    CoreConfig,
    GlobalParams,
    ParamSpec,
    ParamUnit,
    ZoneConfig,
    ZoneParams,
    config_warnings,
)


def _zone(zone_id: str = "living_room", name: str = "Living room", **kwargs: object) -> ZoneConfig:
    return ZoneConfig(id=zone_id, name=name, **kwargs)  # type: ignore[arg-type]


def _build(spec: ParamSpec, number: float) -> object:
    """Build the object that owns `spec` with `number` (in the spec's unit) set."""
    changes: dict[str, Any] = {spec.key: spec.from_number(number)}
    if spec.key in ZONE_PARAM_SPECS:
        return dataclasses.replace(ZoneParams(), **changes)
    if spec.key in GLOBAL_PARAM_SPECS:
        return dataclasses.replace(GlobalParams(), **changes)
    assert spec is SENSOR_OFFSET_SPEC
    return _zone(**changes)


# ---------------------------------------------------------------- §4 defaults


def test_defaults_match_spec_section_4() -> None:
    zone = ZoneParams()
    assert zone.base_setpoint == 22.0
    assert zone.hysteresis == 0.2
    assert zone.wait_time == timedelta(minutes=30)

    glob = GlobalParams()
    assert glob.hp_min_on_time == timedelta(minutes=60)
    assert glob.hp_min_off_time == timedelta(minutes=60)
    assert glob.sensor_fault_timeout == timedelta(minutes=60)
    assert glob.sensor_fault_reminder == time(8, 0)
    assert glob.manual_max_temp == 25.0
    assert glob.manual_resume_delta == 1.0
    assert glob.holiday_temp == 18.0
    assert glob.failsafe_trigger == timedelta(hours=24)
    assert glob.valve_exercise_duration == timedelta(minutes=15)
    assert glob.long_run_alarm == timedelta(hours=12)

    assert _zone().sensor_offset == 0.0
    assert _zone().has_valve is True

    config = CoreConfig(zones=(_zone(),))
    assert config.plausible_min == 0.0
    assert config.plausible_max == 40.0
    assert config.output_mismatch_alert == 3


@pytest.mark.parametrize(
    ("key", "minimum", "maximum", "step", "unit"),
    [
        ("base_setpoint", 10, 30, 0.1, ParamUnit.CELSIUS),
        ("hysteresis", 0.1, 1.0, 0.1, ParamUnit.CELSIUS_DELTA),
        ("wait_time", 0, 120, 5, ParamUnit.MINUTES),
        ("sensor_offset", -5, 5, 0.1, ParamUnit.CELSIUS_DELTA),
        ("hp_min_on_time", 30, 180, 5, ParamUnit.MINUTES),
        ("hp_min_off_time", 30, 180, 5, ParamUnit.MINUTES),
        ("sensor_fault_timeout", 15, 240, 5, ParamUnit.MINUTES),
        ("manual_max_temp", 18, 30, 0.5, ParamUnit.CELSIUS),
        ("manual_resume_delta", 0.2, 3.0, 0.1, ParamUnit.CELSIUS_DELTA),
        ("holiday_temp", 10, 25, 0.5, ParamUnit.CELSIUS),
        ("failsafe_trigger", 1, 72, 1, ParamUnit.HOURS),
        ("valve_exercise_duration", 5, 30, 5, ParamUnit.MINUTES),
        ("long_run_alarm", 2, 48, 1, ParamUnit.HOURS),
    ],
)
def test_spec_ranges_match_section_4(
    key: str, minimum: float, maximum: float, step: float, unit: ParamUnit
) -> None:
    spec = PARAM_SPECS[key]
    assert (spec.key, spec.minimum, spec.maximum, spec.step, spec.unit) == (
        key,
        minimum,
        maximum,
        step,
        unit,
    )
    assert minimum <= spec.default <= maximum


def test_model_defaults_equal_spec_defaults() -> None:
    for obj, specs in ((ZoneParams(), ZONE_PARAM_SPECS), (GlobalParams(), GLOBAL_PARAM_SPECS)):
        for key, spec in specs.items():
            assert getattr(obj, key) == spec.from_number(spec.default), key
    assert _zone().sensor_offset == SENSOR_OFFSET_SPEC.from_number(SENSOR_OFFSET_SPEC.default)


def test_param_specs_cover_every_ranged_field() -> None:
    zone_fields = {f.name for f in dataclasses.fields(ZoneParams)}
    global_fields = {f.name for f in dataclasses.fields(GlobalParams)}
    assert set(ZONE_PARAM_SPECS) == zone_fields
    assert set(GLOBAL_PARAM_SPECS) == global_fields - {"sensor_fault_reminder"}
    assert set(PARAM_SPECS) == set(ZONE_PARAM_SPECS) | set(GLOBAL_PARAM_SPECS) | {"sensor_offset"}


# ---------------------------------------------------------------- range boundaries (D-86)

_SPECS = sorted(PARAM_SPECS.values(), key=lambda s: s.key)


@pytest.mark.parametrize("spec", _SPECS, ids=lambda s: s.key)
def test_boundaries_accepted(spec: ParamSpec) -> None:
    for number in (spec.minimum, spec.maximum, spec.default):
        _build(spec, number)


@pytest.mark.parametrize("spec", _SPECS, ids=lambda s: s.key)
@pytest.mark.parametrize("side", ["below", "above"])
def test_one_step_outside_rejected(spec: ParamSpec, side: str) -> None:
    number = spec.minimum - spec.step if side == "below" else spec.maximum + spec.step
    with pytest.raises(ConfigError) as err:
        _build(spec, number)
    assert len(err.value.errors) == 1
    assert spec.key in err.value.errors[0]
    assert "out of range" in err.value.errors[0]


@pytest.mark.parametrize("spec", [s for s in _SPECS if s.unit.is_temperature], ids=lambda s: s.key)
@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_non_finite_temperatures_rejected(spec: ParamSpec, bad: float) -> None:
    with pytest.raises(ConfigError, match="finite"):
        _build(spec, bad)


@pytest.mark.parametrize("key", ["hp_min_on_time", "hp_min_off_time"])
def test_hp_min_times_never_below_30_minutes(key: str) -> None:
    """D-81: 29 min is rejected, 30 min is accepted."""
    with pytest.raises(ConfigError, match=key):
        GlobalParams(**{key: timedelta(minutes=29)})  # type: ignore[arg-type]
    assert getattr(GlobalParams(**{key: timedelta(minutes=30)}), key) == timedelta(minutes=30)  # type: ignore[arg-type]


def test_off_step_values_accepted() -> None:
    """D-86: the §4 step is UI granularity only; the core checks the range."""
    assert GlobalParams(hp_min_on_time=timedelta(minutes=32)).hp_min_on_time.seconds == 32 * 60
    assert ZoneParams(hysteresis=0.15).hysteresis == 0.15


def test_float_noise_at_boundary_accepted() -> None:
    """Unit conversion can leave tiny float errors at a boundary (e.g. 0.1 °C via °F)."""
    ZoneParams(hysteresis=0.1 - 1e-12, base_setpoint=30 + 1e-12)


def test_wrong_types_rejected() -> None:
    with pytest.raises(ConfigError, match="hp_min_on_time: expected a duration"):
        GlobalParams(hp_min_on_time=60)  # type: ignore[arg-type]
    with pytest.raises(ConfigError, match="base_setpoint: expected a number"):
        ZoneParams(base_setpoint="22")  # type: ignore[arg-type]
    with pytest.raises(ConfigError, match="base_setpoint: expected a number"):
        ZoneParams(base_setpoint=True)
    with pytest.raises(ConfigError, match="sensor_fault_reminder: expected a time of day"):
        GlobalParams(sensor_fault_reminder="08:00")  # type: ignore[arg-type]


def test_sensor_fault_reminder_must_be_local_wall_clock() -> None:
    from datetime import UTC

    with pytest.raises(ConfigError, match="sensor_fault_reminder: must not carry a time zone"):
        GlobalParams(sensor_fault_reminder=time(8, 0, tzinfo=UTC))


def test_all_param_errors_reported_together() -> None:
    with pytest.raises(ConfigError) as err:
        GlobalParams(
            hp_min_on_time=timedelta(minutes=29),
            hp_min_off_time=timedelta(minutes=200),
            holiday_temp=5.0,
        )
    assert len(err.value.errors) == 3
    message = str(err.value)
    for key in ("hp_min_on_time", "hp_min_off_time", "holiday_temp"):
        assert key in message


def test_error_message_is_readable() -> None:
    with pytest.raises(ConfigError) as err:
        GlobalParams(hp_min_on_time=timedelta(minutes=29))
    assert err.value.errors == ["hp_min_on_time: 29 min is out of range (30 to 180 min)"]


# ---------------------------------------------------------------- ParamSpec conversion


@pytest.mark.parametrize(
    ("key", "number", "value"),
    [
        ("wait_time", 30, timedelta(minutes=30)),
        ("failsafe_trigger", 24, timedelta(hours=24)),
        ("base_setpoint", 22.5, 22.5),
    ],
)
def test_spec_number_conversion(key: str, number: float, value: float | timedelta) -> None:
    spec = PARAM_SPECS[key]
    assert spec.from_number(number) == value
    assert spec.to_number(value) == number


# ---------------------------------------------------------------- zones (D-76, D-84, D-85)


@pytest.mark.parametrize("zone_id", ["a", "living_room", "zone_1", "z2_upstairs_"])
def test_valid_zone_ids(zone_id: str) -> None:
    assert _zone(zone_id).id == zone_id


@pytest.mark.parametrize(
    ("zone_id", "suggestion"),
    [
        ("Living Room", "living_room"),
        ("living-room", "living_room"),
        ("1st_floor", "zone_1st_floor"),
        ("_hall", "hall"),
        ("Fürdő", "furdo"),
    ],
)
def test_invalid_zone_id_suggests_slug(zone_id: str, suggestion: str) -> None:
    with pytest.raises(ConfigError) as err:
        _zone(zone_id)
    assert f"e.g. '{suggestion}'" in err.value.errors[0]


@pytest.mark.parametrize("zone_id", ["", "---", "  "])
def test_invalid_zone_id_without_suggestion(zone_id: str) -> None:
    with pytest.raises(ConfigError) as err:
        _zone(zone_id)
    assert "zone id" in err.value.errors[0]
    assert "e.g." not in err.value.errors[0]


@pytest.mark.parametrize("name", ["", "   "])
def test_empty_zone_name_rejected(name: str) -> None:
    with pytest.raises(ConfigError, match="zone 'living_room': name must not be empty"):
        _zone(name=name)


def test_zone_type_errors() -> None:
    with pytest.raises(ConfigError) as err:
        ZoneConfig(id=5, name=None, has_valve="yes")  # type: ignore[arg-type]
    assert len(err.value.errors) == 3


def test_zone_errors_are_prefixed_with_zone_id() -> None:
    with pytest.raises(ConfigError) as err:
        _zone("bath", "Bath", sensor_offset=6.0)
    assert err.value.errors == ["zone 'bath': sensor_offset: 6 °C is out of range (-5 to 5 °C)"]


def test_at_least_one_zone() -> None:
    with pytest.raises(ConfigError, match="at least one zone"):
        CoreConfig(zones=())


def test_zones_must_be_zone_configs() -> None:
    with pytest.raises(ConfigError, match="zones: expected ZoneConfig"):
        CoreConfig(zones=({"id": "a"},))  # type: ignore[arg-type]


def test_duplicate_zone_id_rejected() -> None:
    with pytest.raises(ConfigError) as err:
        CoreConfig(zones=(_zone("bath", "Bath"), _zone("bath", "Bathroom")))
    assert err.value.errors == ["duplicate zone id 'bath'"]


def test_duplicate_zone_name_is_case_insensitive_and_trimmed() -> None:
    with pytest.raises(ConfigError) as err:
        CoreConfig(zones=(_zone("bath", "Bath"), _zone("bath_2", " bath ")))
    assert err.value.errors == ["duplicate zone name ' bath ' (zones 'bath' and 'bath_2')"]


def test_zone_order_is_kept() -> None:
    """YAML order is the D-65 tie-break, so the config must keep it."""
    config = CoreConfig(zones=(_zone("b", "B"), _zone("a", "A"), _zone("c", "C")))
    assert config.zone_ids == ("b", "a", "c")


@pytest.mark.parametrize(("low", "high"), [(40.0, 0.0), (20.0, 20.0), (math.nan, 40.0)])
def test_plausible_range_must_be_increasing_and_finite(low: float, high: float) -> None:
    with pytest.raises(ConfigError, match="plausible"):
        CoreConfig(zones=(_zone(),), plausible_min=low, plausible_max=high)


@pytest.mark.parametrize("value", [0, -1, 2.5, True])
def test_output_mismatch_alert_must_be_positive_int(value: object) -> None:
    with pytest.raises(ConfigError, match="output_mismatch_alert"):
        CoreConfig(zones=(_zone(),), output_mismatch_alert=value)  # type: ignore[arg-type]


def test_all_config_errors_reported_together() -> None:
    with pytest.raises(ConfigError) as err:
        CoreConfig(
            zones=(_zone("a", "A"), _zone("a", "a")),
            plausible_min=50.0,
            output_mismatch_alert=0,
        )
    assert len(err.value.errors) == 4
    assert str(err.value).startswith("Invalid Multizone Floor Heating Manager configuration:\n- ")


# ---------------------------------------------------------------- D-80 warning


def test_warning_when_every_zone_has_a_valve() -> None:
    config = CoreConfig(zones=(_zone("a", "A"), _zone("b", "B")))
    warnings = config_warnings(config)
    assert len(warnings) == 1
    assert "flow path" in warnings[0]


def test_no_warning_with_an_unvalved_zone() -> None:
    config = CoreConfig(zones=(_zone("a", "A"), _zone("b", "B", has_valve=False)))
    assert config_warnings(config) == []
