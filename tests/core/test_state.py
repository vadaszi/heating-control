"""Core state model and persistence format (docs/design.md §3.8, D-76, D-78, D-87)."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from custom_components.floorheat.core.config import CoreConfig, ZoneConfig
from custom_components.floorheat.core.state import (
    SCHEMA_VERSION,
    CoreState,
    OutputTracking,
    StateFormatError,
    ZoneMode,
    ZoneState,
    load_state,
)

T0 = datetime(2026, 9, 27, 6, 0, tzinfo=UTC)
CONFIG = CoreConfig(
    zones=(
        ZoneConfig(id="living_room", name="Living room"),
        ZoneConfig(id="bathroom", name="Bathroom", has_valve=False),
    )
)

# ---------------------------------------------------------------- strategies

# hypothesis takes naive bounds and attaches the time zones itself.
_MIN_DT = datetime(2000, 1, 1)  # noqa: DTZ001
_MAX_DT = datetime(2100, 1, 1)  # noqa: DTZ001
aware_datetimes = st.datetimes(_MIN_DT, _MAX_DT, timezones=st.timezones())
utc_datetimes = st.datetimes(_MIN_DT, _MAX_DT, timezones=st.just(UTC))
finite_floats = st.floats(allow_nan=False, allow_infinity=False)
zone_ids = st.from_regex(r"[a-z][a-z0-9_]{0,15}", fullmatch=True)
trackings = st.builds(
    OutputTracking, mismatch_count=st.integers(min_value=0, max_value=10_000), alerted=st.booleans()
)


def core_states(dts: st.SearchStrategy[datetime]) -> st.SearchStrategy[CoreState]:
    opt_dt = st.none() | dts
    zone_states = st.builds(
        ZoneState,
        mode=st.sampled_from(ZoneMode),
        wait_started_at=opt_dt,
        last_valid_value=st.none() | finite_floats,
        last_valid_at=opt_dt,
        fault_since=opt_dt,
        forced_capped=st.booleans(),
        valve_output=trackings,
        last_setpoint=st.none() | finite_floats,
        awaiting_reading_since=opt_dt,
    )
    return st.builds(
        CoreState,
        zones=st.dictionaries(zone_ids, zone_states, max_size=6),
        hp_actual_on=st.none() | st.booleans(),
        hp_last_on_at=opt_dt,
        hp_last_off_at=opt_dt,
        hp_unavailable_since=opt_dt,
        calling_zone=st.none() | zone_ids,
        sync_fired=st.booleans(),
        heat_source_output=trackings,
        last_fault_reminder_on=st.none() | st.dates(),
    )


def _json_round_trip(data: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(json.dumps(data))
    return result


# ---------------------------------------------------------------- round trip


@given(core_states(utc_datetimes))
def test_round_trip_is_lossless(state: CoreState) -> None:
    assert CoreState.from_dict(_json_round_trip(state.to_dict())) == state


@given(core_states(aware_datetimes))
def test_round_trip_keeps_instants_in_any_time_zone(state: CoreState) -> None:
    """Datetimes are stored in UTC; the instant survives even in DST folds and gaps."""
    data = _json_round_trip(state.to_dict())
    restored = CoreState.from_dict(data)
    assert restored.to_dict() == data == state.to_dict()
    if state.hp_last_on_at is not None:
        assert restored.hp_last_on_at is not None
        assert restored.hp_last_on_at.timestamp() == state.hp_last_on_at.timestamp()


def test_to_dict_carries_schema_version_and_uses_utc() -> None:
    cest = timezone(timedelta(hours=2))
    state = CoreState(hp_last_on_at=datetime(2026, 9, 27, 8, 0, tzinfo=cest))
    data = state.to_dict()
    assert data["schema_version"] == SCHEMA_VERSION == 1
    assert data["hp_last_on_at"] == "2026-09-27T06:00:00+00:00"


def test_persisted_zones_are_keyed_by_zone_id() -> None:
    """D-76: state is keyed by the stable zone id, not the display name."""
    data = CoreState.initial(CONFIG).to_dict()
    assert set(data["zones"]) == {"living_room", "bathroom"}


def test_missing_optional_keys_get_defaults() -> None:
    """Additive fields may be missing in older data of the same schema version (D-87)."""
    state = CoreState.from_dict({"schema_version": 1, "zones": {"a": {}}})
    assert state == CoreState(zones={"a": ZoneState()})


def test_unknown_keys_are_ignored() -> None:
    data = CoreState().to_dict() | {"added_by_a_newer_minor": 1}
    assert CoreState.from_dict(data) == CoreState()


def test_integral_reading_is_read_as_float() -> None:
    state = CoreState.from_dict({"schema_version": 1, "zones": {"a": {"last_valid_value": 22}}})
    value = state.zones["a"].last_valid_value
    assert isinstance(value, float)
    assert value == 22.0


# ---------------------------------------------------------------- invalid data


@pytest.mark.parametrize(
    ("data", "match"),
    [
        ([], "expected a mapping"),
        ({}, "schema_version"),
        ({"schema_version": "1"}, "schema_version"),
        ({"schema_version": True}, "schema_version"),
        ({"schema_version": 0}, "unsupported schema version 0"),
        ({"schema_version": 2}, "newer"),
        ({"schema_version": 1, "zones": []}, "zones: expected a mapping"),
        ({"schema_version": 1, "zones": {"a": []}}, r"zones\.a: expected a mapping"),
        ({"schema_version": 1, "zones": {5: {}}}, "zones: keys must be strings"),
        ({"schema_version": 1, "zones": {"a": {"mode": "bogus"}}}, r"zones\.a\.mode"),
        ({"schema_version": 1, "zones": {"a": {"mode": 1}}}, r"zones\.a\.mode"),
        (
            {"schema_version": 1, "zones": {"a": {"wait_started_at": "2026-09-27T06:00:00"}}},
            "time zone",
        ),
        ({"schema_version": 1, "zones": {"a": {"fault_since": "yesterday"}}}, "fault_since"),
        ({"schema_version": 1, "zones": {"a": {"last_valid_at": 5}}}, "last_valid_at"),
        ({"schema_version": 1, "zones": {"a": {"last_valid_value": "22"}}}, "last_valid_value"),
        ({"schema_version": 1, "zones": {"a": {"last_valid_value": True}}}, "last_valid_value"),
        (
            {"schema_version": 1, "zones": {"a": {"last_valid_value": float("nan")}}},
            "last_valid_value",
        ),
        ({"schema_version": 1, "zones": {"a": {"forced_capped": 1}}}, "forced_capped"),
        ({"schema_version": 1, "zones": {"a": {"last_setpoint": "22"}}}, "last_setpoint"),
        (
            {"schema_version": 1, "zones": {"a": {"awaiting_reading_since": "x"}}},
            "awaiting_reading_since",
        ),
        ({"schema_version": 1, "zones": {"a": {"valve_output": 3}}}, "valve_output"),
        (
            {"schema_version": 1, "zones": {"a": {"valve_output": {"mismatch_count": -1}}}},
            "mismatch_count",
        ),
        ({"schema_version": 1, "heat_source_output": {"mismatch_count": True}}, "mismatch_count"),
        ({"schema_version": 1, "heat_source_output": {"alerted": "no"}}, "alerted"),
        ({"schema_version": 1, "hp_actual_on": "on"}, "hp_actual_on"),
        ({"schema_version": 1, "hp_unavailable_since": 5}, "hp_unavailable_since"),
        ({"schema_version": 1, "hp_last_off_at": "2026-13-01T00:00:00+00:00"}, "hp_last_off_at"),
        ({"schema_version": 1, "calling_zone": 3}, "calling_zone"),
        ({"schema_version": 1, "sync_fired": None}, "sync_fired"),
        ({"schema_version": 1, "last_fault_reminder_on": "2026-02-30"}, "last_fault_reminder_on"),
        ({"schema_version": 1, "last_fault_reminder_on": 20260927}, "last_fault_reminder_on"),
    ],
)
def test_invalid_data_raises_state_format_error(data: object, match: str) -> None:
    with pytest.raises(StateFormatError, match=match):
        CoreState.from_dict(data)


# ---------------------------------------------------------------- load_state (D-78, D-87)


def test_initial_state_is_a_first_start() -> None:
    state = CoreState.initial(CONFIG)
    assert state.zones == {"living_room": ZoneState(), "bathroom": ZoneState()}
    assert state.hp_actual_on is None
    assert state.hp_last_on_at is None
    assert state.hp_last_off_at is None  # D-78: HpMinOffTime not applied
    assert state.calling_zone is None
    assert state.sync_fired is False
    assert state.zones["living_room"].mode is ZoneMode.IDLE


def test_load_without_stored_data_is_a_silent_first_start() -> None:
    assert load_state(None, CONFIG) == (CoreState.initial(CONFIG), [])


@pytest.mark.parametrize("data", [{"schema_version": 2}, {"zones": "garbage"}, "not a dict"])
def test_unusable_data_falls_back_to_first_start(data: object) -> None:
    state, warnings = load_state(data, CONFIG)
    assert state == CoreState.initial(CONFIG)
    assert len(warnings) == 1
    assert "first start" in warnings[0]


def test_load_keeps_valid_state() -> None:
    stored = CoreState(
        zones={
            "living_room": ZoneState(mode=ZoneMode.WAITING, wait_started_at=T0),
            "bathroom": ZoneState(mode=ZoneMode.HEATING),
        },
        hp_actual_on=True,
        hp_last_on_at=T0 - timedelta(minutes=20),
        hp_last_off_at=T0 - timedelta(hours=3),
        calling_zone="bathroom",
        sync_fired=True,
    )
    assert load_state(stored.to_dict(), CONFIG) == (stored, [])


def test_load_aligns_zones_with_config() -> None:
    stored = CoreState(
        zones={
            "living_room": ZoneState(mode=ZoneMode.WAITING, wait_started_at=T0),
            "old_zone": ZoneState(mode=ZoneMode.HEATING),
        },
        hp_actual_on=True,
        hp_last_on_at=T0,
        calling_zone="old_zone",
        sync_fired=True,
    )
    state, warnings = load_state(stored.to_dict(), CONFIG)
    assert list(state.zones) == ["living_room", "bathroom"]  # config order
    assert state.zones["living_room"] == stored.zones["living_room"]
    assert state.zones["bathroom"] == ZoneState()
    assert state.calling_zone is None
    assert state.hp_last_on_at == T0
    assert state.sync_fired is True
    assert warnings == ["Discarded stored state of zones no longer configured: old_zone"]


def test_load_state_json_round_trip_through_store_format() -> None:
    stored = CoreState(
        zones={"living_room": ZoneState(last_valid_value=21.7, last_valid_at=T0)},
        last_fault_reminder_on=date(2026, 9, 27),
    )
    state, warnings = load_state(_json_round_trip(stored.to_dict()), CONFIG)
    assert state.zones["living_room"] == stored.zones["living_room"]
    assert state.last_fault_reminder_on == date(2026, 9, 27)
    assert warnings == []
