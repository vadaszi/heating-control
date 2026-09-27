"""Unit tests per rule of the step function (docs/design.md §3.2, §3.3, §3.5, §3.6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.floorheat.core.config import (
    CoreConfig,
    GlobalParams,
    ZoneConfig,
    ZoneParams,
)
from custom_components.floorheat.core.engine import step
from custom_components.floorheat.core.io import Inputs, OutputState, ZoneInput
from custom_components.floorheat.core.state import CoreState, ZoneMode, ZoneState

from .harness import Scenario, at, make_config

IDLE, WAITING, HEATING, FAULT = (
    ZoneMode.IDLE,
    ZoneMode.WAITING,
    ZoneMode.HEATING,
    ZoneMode.SENSOR_FAULT,
)


# ---------------------------------------------------------------- rules 1 and 2: wait


def test_wait_time_zero_starts_at_once() -> None:
    sc = Scenario(2, temps={1: 21.8}, zone_params=ZoneParams(wait_time=timedelta(0)))
    sc.step()
    assert sc.mode(1) is HEATING
    assert sc.hp
    assert sc.calling_zone == "zone_1"


def test_zone_above_starttemp_stays_idle() -> None:
    sc = Scenario(2, temps={1: 21.81})
    sc.step()
    assert sc.mode(1) is IDLE
    assert sc.reason(1) == "Idle"
    assert sc.valve(1) is False


def test_sensor_offset_is_applied() -> None:
    config = CoreConfig(zones=(ZoneConfig(id="zone_1", name="Zone 1", sensor_offset=-0.2),))
    sc = Scenario(config, temps={1: 22.0})
    sc.step()
    assert sc.room_temp(1) == pytest.approx(21.8)
    assert sc.mode(1) is WAITING


def test_waiting_without_start_time_restarts_the_wait() -> None:
    """Defensive: a WAITING zone without a start time (bad stored data) waits from now."""
    state = CoreState(zones={"zone_1": ZoneState(mode=WAITING), "zone_2": ZoneState()})
    sc = Scenario(2, temps={1: 21.8}, state=state)
    sc.step()
    assert sc.state.zones["zone_1"].wait_started_at == sc.now
    assert sc.reason(1) == "Waiting, 30 min left"


# ---------------------------------------------------------------- rule 3: join while running


def test_join_needs_the_hp_actually_running() -> None:
    """The request is ON, but the switch has not followed yet: no join (D-66)."""
    sc = Scenario(3, temps={1: 21.8})
    sc.step()
    sc.set_hp_actual(OutputState.OFF)  # the switch ignores commands from now on
    sc.advance_to("06:30")
    assert sc.hp
    sc.temp(2, 21.8)
    sc.step()
    assert sc.mode(2) is WAITING


# ---------------------------------------------------------------- rule 4: SetPoint raised


def test_setpoint_raise_starts_without_wait() -> None:
    """A10 logic: SetPoint 22 → 23 with RoomTemp 22.1 heats at once."""
    sc = Scenario(2, temps={1: 22.1})
    sc.step()
    sc.advance(1)
    sc.set_setpoint(1, 23.0)
    sc.step()
    assert sc.mode(1) is HEATING
    assert sc.hp
    assert sc.calling_zone == "zone_1"


def test_setpoint_raise_while_waiting_skips_the_rest_of_the_wait() -> None:
    sc = Scenario(2, temps={1: 21.8})
    sc.step()
    sc.advance(10)
    sc.set_setpoint(1, 22.5)
    sc.step()
    assert sc.mode(1) is HEATING
    assert sc.hp


def test_setpoint_raise_is_still_held_by_min_off() -> None:
    sc = Scenario(2, start="07:00", hp_on=True)
    sc.step()
    sc.advance_to("08:00")  # D-91 min ON from startup ends; HP OFF at 08:00
    assert not sc.hp
    sc.advance_to("08:30")
    sc.set_setpoint(1, 23.0)
    sc.step()
    assert sc.mode(1) is HEATING
    assert sc.valve(1) is True
    assert not sc.hp
    assert sc.reason(1) == "Held by min OFF, 30 min left"
    sc.advance_to("09:00")
    assert sc.hp


def test_small_setpoint_raise_above_starttemp_does_nothing() -> None:
    sc = Scenario(2, temps={1: 22.1})
    sc.step()
    sc.set_setpoint(1, 22.2)  # StartTemp 22.0 < RoomTemp 22.1
    sc.advance(1)
    assert sc.mode(1) is IDLE


def test_no_raise_detected_on_the_first_step() -> None:
    """Without a previous SetPoint (first start), rule 1 applies: wait first."""
    sc = Scenario(2, temps={1: 21.0}, zone_params=ZoneParams(base_setpoint=25.0))
    sc.step()
    assert sc.mode(1) is WAITING
    assert sc.state.zones["zone_1"].last_setpoint == 25.0


# ---------------------------------------------------------------- rule 6: switch-off


def test_setpoint_decrease_ends_the_wait() -> None:
    """D-94: a lowered SetPoint that leaves RoomTemp above StartTemp ends the wait at once."""
    sc = Scenario(2, temps={1: 21.0})
    sc.step()
    sc.advance(5)
    assert sc.mode(1) is WAITING
    sc.set_setpoint(1, 18.0)
    sc.step()
    assert sc.mode(1) is IDLE
    assert sc.reason(1) == "Idle"
    sc.advance_to("07:00")
    assert not sc.hp


def test_small_setpoint_decrease_keeps_waiting() -> None:
    """Still at or below the new StartTemp: the wait goes on (D-05 is about readings)."""
    sc = Scenario(2, temps={1: 21.0})
    sc.step()
    sc.advance(5)
    sc.set_setpoint(1, 21.5)  # StartTemp 21.3 >= RoomTemp 21.0
    sc.step()
    assert sc.mode(1) is WAITING
    assert sc.reason(1) == "Waiting, 25 min left"


def test_rising_reading_during_the_wait_does_not_end_it() -> None:
    """D-05 still holds: only a SetPoint change ends the wait early, not a reading."""
    sc = Scenario(2, temps={1: 21.8})
    sc.step()
    sc.advance(5)
    sc.temp(1, 23.0)
    sc.step()
    assert sc.mode(1) is WAITING


def test_setpoint_decrease_switches_off() -> None:
    sc = Scenario(2, temps={1: 21.8}, zone_params=ZoneParams(wait_time=timedelta(0)))
    sc.step()
    sc.temp(1, 21.9)
    sc.advance(5)
    assert sc.mode(1) is HEATING
    sc.set_setpoint(1, 21.5)  # StopTemp 21.7
    sc.step()
    assert sc.mode(1) is IDLE


# ---------------------------------------------------------------- rule 5: sync


def test_sync_skips_faulty_and_unread_zones() -> None:
    sc = Scenario(4, temps={1: 21.8, 2: 21.9, 4: None})
    sc.step()
    sc.silence(3)
    sc.advance_to("06:30")
    sc.advance_to("07:05")  # zone 3 silent since 06:00: SENSOR_FAULT at 07:01
    assert sc.mode(3) is FAULT
    assert sc.mode(4) is FAULT  # never delivered a reading since 06:00 (D-93)
    sc.temp(1, 22.0)
    sc.step()
    assert sc.sync_fired
    assert sc.mode(2) is HEATING
    assert sc.mode(3) is FAULT
    assert sc.mode(4) is FAULT


def test_sync_waits_for_the_calling_zone_to_reach_setpoint() -> None:
    sc = Scenario(2, temps={1: 21.8, 2: 21.9})
    sc.step()
    sc.advance_to("06:30")
    sc.temp(1, 21.99)
    sc.step()
    assert not sc.sync_fired
    assert sc.mode(2) is IDLE


def test_waiting_zone_joins_by_sync() -> None:
    """A WAITING zone above StartTemp (window closed) joins through the sync rule."""
    sc = Scenario(2, temps={1: 21.8})
    sc.step()
    sc.set_hp_actual(OutputState.OFF)  # hold the HP off so zone 2 stays WAITING
    sc.advance_to("06:30")
    sc.temp(2, 21.8)
    sc.step()
    sc.temp(2, 22.0)
    sc.step()
    assert sc.mode(2) is WAITING
    sc.set_hp_actual(OutputState.ON, follows=True)
    sc.temp(1, 22.0)
    sc.step()
    assert sc.sync_fired
    assert sc.mode(2) is HEATING


# ---------------------------------------------------------------- calling zone (D-65, D-92)


def test_first_start_with_hp_on_counts_min_on_from_startup() -> None:
    """D-91: min ON counts from startup; a zone at StartTemp joins at once (rule 3)."""
    sc = Scenario(2, start="07:00", hp_on=True, temps={1: 21.8})
    sc.step()
    assert sc.state.hp_last_on_at == sc.now
    assert sc.mode(1) is HEATING
    assert sc.calling_zone == "zone_1"  # D-92


def test_calling_zone_chosen_while_running_without_one() -> None:
    """D-92: during the D-91 spread no zone calls; the first zone to heat becomes caller."""
    sc = Scenario(3, start="07:00", hp_on=True, temps={3: 21.9})
    sc.step()
    assert sc.calling_zone is None
    sc.advance(5)
    sc.temp(2, 21.8)
    sc.step()
    assert sc.calling_zone == "zone_2"
    sc.temp(2, 22.0)
    sc.step()
    assert sc.sync_fired
    assert sc.mode(3) is HEATING


def test_calling_zone_kept_while_request_on() -> None:
    sc = Scenario(3, temps={1: 21.8})
    sc.step()
    sc.advance_to("06:40")
    sc.temp(2, 21.0)  # larger deficit, but the cycle already has its calling zone
    sc.step()
    assert sc.calling_zone == "zone_1"


# ---------------------------------------------------------------- §3.5 HP protection


def test_spread_excludes_zones_at_manual_max_temp() -> None:
    sc = Scenario(4, temps={1: 21.8, 2: 25.0, 3: 24.99, 4: 22.2})
    sc.step()
    sc.advance_to("06:30")
    sc.temp(1, 22.2)
    sc.advance(1)
    assert sc.hp
    assert sc.open_valves() == {"zone_1", "zone_3", "zone_4"}


def test_spread_opens_faulty_zones() -> None:
    sc = Scenario(3, temps={1: 21.8, 3: None})
    sc.step()
    sc.silence(2)
    sc.advance_to("07:01")  # zone 2: fault; zone 3: fault too (no reading since 06:00)
    assert (sc.mode(2), sc.mode(3)) == (FAULT, FAULT)
    sc.temp(1, 22.2)
    sc.step()
    assert sc.hp  # min ON until 07:30
    assert sc.open_valves() == {"zone_1", "zone_2", "zone_3"}


def test_unvalved_zone_never_shows_the_manual_max_exclusion() -> None:
    """Review B: water flows through a zone without a valve whenever the HP runs."""
    sc = Scenario(3, unvalved=[3], temps={1: 21.8, 2: 25.0, 3: 25.0})
    sc.step()
    sc.advance_to("06:30")
    sc.temp(1, 22.2)
    sc.advance(1)
    assert sc.hp  # D-20 spread
    assert sc.reason(2) == "Idle, at or above ManualMaxTemp"
    assert sc.reason(3) == "Spreading heat (min ON), 59 min left"
    assert sc.valve(3) is None


def test_zone_without_any_reading_stays_closed_during_spread() -> None:
    sc = Scenario(2, start="07:00", hp_on=True, temps={2: None})
    sc.step()
    assert sc.hp  # D-91 spread
    assert sc.open_valves() == {"zone_1"}
    assert sc.reason(2) == "Waiting for a sensor reading"


def test_request_on_immediately_without_last_off_time() -> None:
    """D-78: nothing persisted → no min OFF."""
    sc = Scenario(2, temps={1: 21.8}, zone_params=ZoneParams(wait_time=timedelta(0)))
    sc.step()
    assert sc.hp


def test_min_off_counts_from_the_actual_off_transition() -> None:
    params = GlobalParams(hp_min_off_time=timedelta(minutes=30))
    sc = Scenario(2, start="07:00", hp_on=True, params=params)
    sc.step()
    sc.advance_to("08:00")
    assert not sc.hp
    sc.temp(1, 21.8)
    sc.step()
    sc.advance_to("08:29")
    assert not sc.hp
    sc.advance_to("08:30")  # WaitTime and min OFF (30) end together
    assert sc.hp


# ---------------------------------------------------------------- §3.6 readings and fault


def test_implausible_raw_reading_is_ignored() -> None:
    sc = Scenario(2, temps={1: 45.0})
    sc.step()
    assert sc.room_temp(1) is None
    assert sc.reason(1) == "Waiting for a sensor reading"
    sc.temp(1, 21.9)
    sc.step()
    sc.temp(1, -3.0)
    sc.advance(1)
    assert sc.room_temp(1) == 21.9  # implausible values are as if nothing was received


def test_plausibility_uses_the_raw_reading_before_the_offset() -> None:
    """D-88: raw 41 is implausible even though 41 - 2 = 39 would be inside 0 to 40."""
    config = CoreConfig(zones=(ZoneConfig(id="zone_1", name="Zone 1", sensor_offset=-2.0),))
    sc = Scenario(config, temps={1: 41.0})
    sc.step()
    assert sc.room_temp(1) is None
    sc.temp(1, 40.0)  # the range is inclusive
    sc.step()
    assert sc.room_temp(1) == 38.0


def test_short_dropout_uses_the_last_valid_reading() -> None:
    sc = Scenario(2, temps={1: 21.9})
    sc.step()
    sc.temp(1, None)  # unavailable / not numeric
    sc.advance(30)
    assert sc.mode(1) is IDLE
    assert sc.room_temp(1) == 21.9


def test_zone_without_any_reading_faults_after_timeout_from_startup() -> None:
    """D-93: no valid reading since startup → SENSOR_FAULT after SensorFaultTimeout."""
    sc = Scenario(2, temps={1: None})
    sc.step()
    assert sc.mode(1) is IDLE
    assert sc.state.zones["zone_1"].awaiting_reading_since == sc.now
    sc.advance_to("07:00")
    assert sc.mode(1) is IDLE
    sc.advance_to("07:01")
    assert sc.mode(1) is FAULT
    assert sc.valve(1) is False  # follows the house; the HP is off


def test_fault_recovery_evaluates_normally_in_the_same_step() -> None:
    sc = Scenario(2, temps={1: None})
    sc.step()
    sc.advance_to("07:01")
    assert sc.mode(1) is FAULT
    sc.temp(1, 21.5)
    sc.step()
    assert sc.mode(1) is WAITING
    zone = sc.state.zones["zone_1"]
    assert zone.fault_since is None
    assert zone.awaiting_reading_since is None


def test_stored_heating_zone_without_any_reading_goes_idle() -> None:
    state = CoreState(zones={"zone_1": ZoneState(mode=HEATING), "zone_2": ZoneState()})
    sc = Scenario(2, temps={1: None}, state=state)
    sc.step()
    assert sc.mode(1) is IDLE
    assert not sc.hp


def test_reading_timestamps_are_used_as_reported() -> None:
    config = make_config(1)
    params = {"zone_1": ZoneParams()}
    now = at("06:00")

    def run(state: CoreState, reading: float, reported: datetime) -> CoreState:
        inputs = _inputs({"zone_1": ZoneInput(reading, reported, OutputState.OFF)}, params)
        return step(config, state, inputs, now)[1]

    state = run(CoreState(), 21.0, now + timedelta(minutes=5))  # clock skew: capped at now
    assert state.zones["zone_1"].last_valid_at == now
    state = run(state, 23.0, now - timedelta(minutes=5))  # older than what we have
    assert state.zones["zone_1"].last_valid_value == 21.0


# ---------------------------------------------------------------- step contract


def _inputs(zones: dict[str, ZoneInput], params: dict[str, ZoneParams]) -> Inputs:
    return Inputs(
        zones=zones,
        heat_source=OutputState.OFF,
        zone_params=params,
        global_params=GlobalParams(),
        heating_season=True,
        control_active=True,
    )


def test_now_must_be_timezone_aware() -> None:
    sc = Scenario(1)
    with pytest.raises(ValueError, match="time zone"):
        step(sc.config, sc.state, sc.inputs(), datetime(2026, 1, 12, 6, 0))  # noqa: DTZ001


def test_last_reported_must_be_timezone_aware() -> None:
    """Review D: a naive timestamp is rejected with a clear error, not a TypeError."""
    config = make_config(1)
    naive = datetime(2026, 1, 12, 6, 0)  # noqa: DTZ001
    inputs = _inputs({"zone_1": ZoneInput(22.0, naive, OutputState.OFF)}, {"zone_1": ZoneParams()})
    with pytest.raises(ValueError, match="zone_1: last_reported must carry a time zone"):
        step(config, CoreState(), inputs, at("06:00"))


def test_inputs_must_cover_every_zone() -> None:
    config = make_config(2)
    inputs = _inputs({"zone_1": ZoneInput(22.0, None, None)}, {"zone_1": ZoneParams()})
    with pytest.raises(ValueError, match="zone_2"):
        step(config, CoreState(), inputs, datetime(2026, 1, 12, 6, 0, tzinfo=UTC))


def test_state_is_aligned_with_the_config() -> None:
    state = CoreState(zones={"gone": ZoneState(mode=HEATING)})
    sc = Scenario(2, state=state)
    sc.step()
    assert set(sc.state.zones) == {"zone_1", "zone_2"}
    assert not sc.hp


def test_no_events_yet() -> None:
    """Notifications are added in P3."""
    sc = Scenario(2, temps={1: None})
    sc.step()
    sc.advance_to("07:30")
    assert sc.events == []
