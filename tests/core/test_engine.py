"""Unit tests per rule of the step function."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.multizone_floor_heating_manager.core.config import (
    CoreConfig,
    GlobalParams,
    ZoneConfig,
    ZoneParams,
)
from custom_components.multizone_floor_heating_manager.core.engine import step
from custom_components.multizone_floor_heating_manager.core.io import (
    Inputs,
    OutputState,
    Reason,
    ZoneInput,
)
from custom_components.multizone_floor_heating_manager.core.state import (
    CoreState,
    ZoneMode,
    ZoneState,
)

from .harness import DAY, Scenario, at, make_config

IDLE, WAITING, HEATING, FORCED, FAULT = (
    ZoneMode.IDLE,
    ZoneMode.WAITING,
    ZoneMode.HEATING,
    ZoneMode.FORCED,
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
    assert sc.reason(1) == Reason.IDLE
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
    assert sc.reason(1) == Reason.WAITING
    assert sc.until(1) == sc.now + timedelta(minutes=30)


# ---------------------------------------------------------------- rule 3: join while running


def test_join_needs_the_hp_actually_running() -> None:
    """The request is ON, but the switch has not followed yet: no join."""
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
    sc.advance_to("08:00")  # min ON from startup ends; HP OFF at 08:00
    assert not sc.hp
    sc.advance_to("08:30")
    sc.set_setpoint(1, 23.0)
    sc.step()
    assert sc.mode(1) is HEATING
    assert sc.valve(1) is True
    assert not sc.hp
    assert sc.reason(1) == Reason.HELD_BY_MIN_OFF
    assert sc.until(1) == at("09:00")
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
    """A lowered SetPoint that leaves RoomTemp above StartTemp ends the wait at once."""
    sc = Scenario(2, temps={1: 21.0})
    sc.step()
    sc.advance(5)
    assert sc.mode(1) is WAITING
    sc.set_setpoint(1, 18.0)
    sc.step()
    assert sc.mode(1) is IDLE
    assert sc.reason(1) == Reason.IDLE
    sc.advance_to("07:00")
    assert not sc.hp


def test_small_setpoint_decrease_keeps_waiting() -> None:
    """Still at or below the new StartTemp: the wait goes on."""
    sc = Scenario(2, temps={1: 21.0})
    sc.step()
    sc.advance(5)
    sc.set_setpoint(1, 21.5)  # StartTemp 21.3 >= RoomTemp 21.0
    sc.step()
    assert sc.mode(1) is WAITING
    assert sc.reason(1) == Reason.WAITING
    assert sc.until(1) == at("06:30")


def test_rising_reading_during_the_wait_does_not_end_it() -> None:
    """Only a SetPoint change ends the wait early, not a reading."""
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
    assert sc.mode(4) is FAULT  # never delivered a reading since 06:00
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


# ---------------------------------------------------------------- calling zone


def test_first_start_with_hp_on_counts_min_on_from_startup() -> None:
    """Min ON counts from startup; a zone at StartTemp joins at once."""
    sc = Scenario(2, start="07:00", hp_on=True, temps={1: 21.8})
    sc.step()
    assert sc.state.hp_last_on_at == sc.now
    assert sc.mode(1) is HEATING
    assert sc.calling_zone == "zone_1"


def test_calling_zone_chosen_while_running_without_one() -> None:
    """During the startup min ON spread no zone calls; the first zone to heat becomes caller."""
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


# ---------------------------------------------------------------- HP protection


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
    assert sc.hp  # min ON spread
    assert sc.reason(2) == Reason.TOO_WARM_FOR_SPREADING
    assert sc.reason(3) == Reason.SPREADING_HEAT
    assert sc.until(3) == at("07:30")
    assert sc.valve(3) is None


def test_zone_without_any_reading_stays_closed_during_spread() -> None:
    sc = Scenario(2, start="07:00", hp_on=True, temps={2: None})
    sc.step()
    assert sc.hp  # startup min ON spread
    assert sc.open_valves() == {"zone_1"}
    assert sc.reason(2) == Reason.NO_READING_YET


def test_request_on_immediately_without_last_off_time() -> None:
    """Nothing persisted → no min OFF."""
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


# ---------------------------------------------------------------- heat source unavailable


def test_heat_source_off_before_outage_stays_off_with_its_old_off_time() -> None:
    sc = Scenario(2, start="07:00", hp_on=True)
    sc.step()
    sc.advance_to("08:00")  # OFF at 08:00 (min ON from startup)
    sc.advance_to("08:10")
    sc.set_hp_actual(OutputState.UNAVAILABLE)
    sc.advance(5)
    sc.set_hp_actual(OutputState.OFF, follows=True)
    sc.step()
    assert sc.state.hp_last_off_at == sc.now.replace(hour=8, minute=0)


def test_heat_source_off_before_outage_back_on_is_a_new_start() -> None:
    """It was OFF and reports ON (switched on elsewhere): an ON transition now."""
    sc = Scenario(2, start="07:00", hp_on=True)
    sc.step()
    sc.advance_to("08:10")
    sc.set_hp_actual(OutputState.UNAVAILABLE)
    sc.advance(5)
    sc.set_hp_actual(OutputState.ON)
    sc.step()
    assert sc.state.hp_last_on_at == sc.now


def test_heat_source_unavailable_at_first_start() -> None:
    """Nothing known yet: no min OFF; back ON counts from then."""
    sc = Scenario(2, temps={1: 21.8}, zone_params=ZoneParams(wait_time=timedelta(0)))
    sc.set_hp_actual(OutputState.UNAVAILABLE)
    sc.step()
    assert sc.state.hp_actual_on is None
    assert sc.hp  # demand, no known OFF time: the request is ON (not deliverable yet)
    sc.advance(3)
    sc.set_hp_actual(OutputState.ON)
    sc.step()
    assert sc.state.hp_last_on_at == sc.now


def test_restart_during_outage_keeps_the_outage() -> None:
    sc = Scenario(2, temps={1: 21.8}, zone_params=ZoneParams(wait_time=timedelta(0)))
    sc.step()
    sc.advance(10)
    started = sc.state.hp_last_on_at
    sc.set_hp_actual(OutputState.UNAVAILABLE)
    sc.step()
    outage = sc.now
    sc.restart(downtime=5)
    sc.step()
    assert sc.state.hp_unavailable_since == outage
    sc.set_hp_actual(OutputState.ON, follows=True)
    sc.step()
    assert sc.state.hp_last_on_at == started  # never stopped


# ---------------------------------------------------------------- heating season


def test_season_on_again_is_held_by_min_off_from_the_actual_off() -> None:
    sc = Scenario(2, temps={1: 21.8}, zone_params=ZoneParams(wait_time=timedelta(0)))
    sc.step()
    assert sc.hp
    sc.advance(20)
    sc.set_season(False)
    sc.step()
    off_at = sc.now
    sc.advance(10)
    sc.set_season(True)
    sc.step()
    assert sc.mode(1) is HEATING  # WaitTime 0
    assert not sc.hp
    assert sc.reason(1) == Reason.HELD_BY_MIN_OFF
    assert sc.until(1) == off_at + timedelta(minutes=60)
    sc.now = off_at + timedelta(minutes=59)
    sc.step()
    assert not sc.hp
    sc.advance(1)
    assert sc.hp


def test_season_off_closes_faulty_zones_while_the_hp_still_runs() -> None:
    sc = Scenario(2, temps={1: 21.8, 2: None})
    sc.step()
    sc.advance_to("07:01")
    assert sc.mode(2) is FAULT
    assert sc.hp
    assert sc.valve(2) is True  # follows the house
    sc.set_hp_actual(OutputState.ON)  # the switch does not follow the OFF command yet
    sc.set_season(False)
    sc.step()
    assert not sc.hp
    assert sc.valve(2) is False
    assert sc.mode(2) is FAULT  # fault detection keeps running
    assert sc.reason(2) == Reason.SENSOR_FAULT_SEASON_OFF
    assert sc.mode(1) is IDLE  # no join by rule 3 although the HP still runs


def test_season_off_ends_the_cycle_while_the_heat_source_is_unavailable() -> None:
    sc = Scenario(2, temps={1: 21.8}, zone_params=ZoneParams(wait_time=timedelta(0)))
    sc.step()
    sc.set_hp_actual(OutputState.UNAVAILABLE)
    sc.advance(5)
    assert sc.calling_zone == "zone_1"  # kept while unknown
    sc.set_season(False)
    sc.step()
    assert sc.calling_zone is None
    assert not sc.sync_fired
    assert sc.reason(1) == Reason.SEASON_OFF


def test_season_off_at_first_start_with_the_hp_on() -> None:
    """No startup min ON spread outside the season: the request is OFF at once."""
    sc = Scenario(2, start="07:00", hp_on=True)
    sc.set_season(False)
    sc.step()
    assert not sc.hp
    assert sc.open_valves() == set()


def test_setpoint_change_during_season_off_is_not_a_raise_later() -> None:
    """SetPoint tracking continues while OFF, so switching ON is not a SetPoint raise."""
    sc = Scenario(2, temps={1: 21.5})
    sc.set_season(False)
    sc.step()
    sc.set_setpoint(1, 23.0)
    sc.advance(5)
    sc.set_season(True)
    sc.step()
    assert sc.mode(1) is WAITING


# ---------------------------------------------------------------- readings and fault


def test_implausible_raw_reading_is_ignored() -> None:
    sc = Scenario(2, temps={1: 45.0})
    sc.step()
    assert sc.room_temp(1) is None
    assert sc.reason(1) == Reason.NO_READING_YET
    sc.temp(1, 21.9)
    sc.step()
    sc.temp(1, -3.0)
    sc.advance(1)
    assert sc.room_temp(1) == 21.9  # implausible values are as if nothing was received


def test_plausibility_uses_the_raw_reading_before_the_offset() -> None:
    """Raw 41 is implausible even though 41 - 2 = 39 would be inside 0 to 40."""
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
    """No valid reading since startup → SENSOR_FAULT after SensorFaultTimeout."""
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


# ---------------------------------------------------------------- manual schedules


def _hp_off_at_0400(zones: int = 2, temps: dict[int | str, float] | None = None) -> Scenario:
    """First start at 03:00 with the heat source ON and no demand: OFF at 04:00, so min
    OFF runs until 05:00."""
    sc = Scenario(zones, temps=temps or {}, start="03:00", hp_on=True)
    sc.step()
    sc.advance_to("04:00")
    assert not sc.hp
    return sc


def test_forced_zone_is_held_by_min_off_with_its_valve_open() -> None:
    sc = _hp_off_at_0400()
    sc.add_manual([1], "04:10", "06:00")
    sc.advance_to("04:10")
    assert sc.mode(1) is FORCED
    assert sc.valve(1) is True  # like a zone held by min OFF
    assert not sc.hp
    assert sc.reason(1) == Reason.HELD_BY_MIN_OFF
    assert sc.until(1) == at("05:00")
    sc.advance_to("05:00")
    assert sc.hp
    assert sc.reason(1) == Reason.FORCED
    assert sc.until(1) == at("06:00")


def test_calling_zone_forced_hands_the_role_over() -> None:
    """A forced zone never calls; the heating zone with the largest deficit
    takes over."""
    sc = Scenario(3, temps={1: 21.8})
    sc.add_manual([1], "06:40", "07:00")
    sc.step()
    sc.advance_to("06:30")
    assert sc.calling_zone == "zone_1"
    sc.advance_to("06:35")
    sc.temp(2, 21.7)
    sc.temp(3, 21.5)
    sc.step()
    assert sc.modes() == {"zone_1": HEATING, "zone_2": HEATING, "zone_3": HEATING}
    sc.advance_to("06:40")
    assert sc.mode(1) is FORCED
    assert sc.calling_zone == "zone_3"
    assert sc.reason(3) == Reason.CALLING_ZONE
    assert not sc.sync_fired


def test_forced_calling_zone_passes_the_role_to_the_next_joining_zone() -> None:
    sc = Scenario(2, temps={1: 21.8})
    sc.add_manual([1], "06:40", "08:00")
    sc.step()
    sc.advance_to("06:40")
    assert sc.mode(1) is FORCED
    assert sc.calling_zone is None
    assert sc.hp
    sc.advance_to("06:50")
    sc.temp(2, 21.8)
    sc.step()
    assert sc.calling_zone == "zone_2"


def test_window_end_while_running_joins_without_wait() -> None:
    """After the window the zone is IDLE and evaluated at once."""
    sc = Scenario(2, start="04:00")
    sc.add_manual([1], "04:00", "05:00")
    sc.step()
    sc.temp(1, 21.8)
    sc.advance_to("05:00")
    assert sc.mode(1) is HEATING
    assert sc.hp
    assert sc.calling_zone == "zone_1"


def test_window_end_while_the_hp_is_off_starts_the_wait() -> None:
    sc = _hp_off_at_0400()
    sc.add_manual([1], "04:10", "04:40")
    sc.advance_to("04:10")
    assert sc.mode(1) is FORCED
    sc.temp(1, 21.8)
    sc.advance_to("04:40")
    assert sc.mode(1) is WAITING  # rule 1: a full WaitTime from the window end
    assert sc.until(1) == at("05:10")


def test_zone_without_a_reading_is_not_forced() -> None:
    """Like SENSOR_FAULT, the cap cannot be checked."""
    sc = Scenario(2, temps={1: None}, start="04:00")
    sc.add_manual([1], "04:00", "06:00")
    sc.step()
    assert sc.mode(1) is IDLE
    assert sc.reason(1) == Reason.NO_READING_YET
    assert not sc.hp
    sc.advance(1)
    sc.temp(1, 22.0)
    sc.step()
    assert sc.mode(1) is FORCED
    assert sc.hp


def test_fault_recovery_inside_the_window_forces_at_once() -> None:
    sc = Scenario(2, start="02:00")
    sc.add_manual([1], "04:00", "06:00")
    sc.step()
    sc.silence(1)
    sc.advance_to("04:00")
    assert sc.mode(1) is FAULT
    sc.temp(1, 22.0)
    sc.advance(1)
    assert sc.mode(1) is FORCED
    assert sc.state.zones["zone_1"].fault_since is None


@pytest.mark.parametrize(("temp", "capped"), [(24.5, False), (24.99, False), (25.0, True)])
def test_cap_on_entering_the_window(temp: float, capped: bool) -> None:
    sc = Scenario(2, temps={1: temp}, start="04:00")
    sc.add_manual([1], "04:00", "06:00")
    sc.step()
    assert sc.capped(1) is capped
    assert sc.valve(1) is not capped
    assert sc.hp is not capped


def test_cap_survives_a_restart() -> None:
    sc = Scenario(2, temps={1: 25.0}, start="04:00")
    sc.add_manual([1], "04:00", "06:00")
    sc.step()
    sc.temp(1, 24.5)
    sc.restart(downtime=5)
    sc.step()
    assert sc.capped(1)
    assert sc.valve(1) is False


def test_forced_zone_outside_the_season() -> None:
    sc = Scenario(2, start="04:00")
    sc.add_manual([1], "04:00", "08:00")
    sc.step()
    assert sc.hp
    sc.advance_to("04:20")
    sc.set_season(False)
    sc.step()
    assert sc.mode(1) is IDLE
    assert sc.reason(1) == Reason.SEASON_OFF
    assert not sc.hp
    assert sc.valve(1) is False
    sc.advance_to("04:30")
    sc.set_season(True)
    sc.step()
    assert sc.mode(1) is FORCED
    assert sc.reason(1) == Reason.HELD_BY_MIN_OFF  # min OFF from the actual OFF at 04:20
    sc.advance_to("05:20")
    assert sc.hp


def test_forced_zone_with_the_heat_source_unavailable() -> None:
    sc = Scenario(2, start="04:00")
    sc.add_manual([1], "04:00", "06:00")
    sc.step()
    sc.set_hp_actual(OutputState.UNAVAILABLE)
    sc.advance(1)
    assert sc.mode(1) is FORCED
    assert sc.valve(1) is True
    assert sc.reason(1) == Reason.HEAT_SOURCE_UNAVAILABLE
    assert sc.until(1) is None


def test_unvalved_forced_zone_creates_demand() -> None:
    sc = Scenario(2, unvalved=[2], start="04:00")
    sc.add_manual([2], "04:00", "06:00")
    sc.step()
    assert sc.mode(2) is FORCED
    assert sc.hp
    assert sc.valve(2) is None  # no output


def test_forced_zone_is_left_alone_by_the_sync_rule() -> None:
    sc = Scenario(3, temps={1: 21.8})
    sc.add_manual([2], "06:00", "08:00")
    sc.step()
    sc.advance_to("06:30")
    assert sc.calling_zone == "zone_1"
    sc.temp(1, 22.0)
    sc.step()
    assert sc.sync_fired
    assert sc.mode(2) is FORCED
    assert sc.mode(3) is HEATING  # 22.0 < StopTemp


# ---------------------------------------------------------------- auto schedules and holiday


def test_auto_schedule_end_ends_the_wait() -> None:
    """An ending auto schedule lowers the SetPoint during the wait."""
    sc = Scenario(2, temps={1: 22.5}, start="06:00")
    sc.add_auto([1], "05:00", "06:10", 23.0)
    sc.step()  # no previous SetPoint: rule 1, not a raise
    assert sc.mode(1) is WAITING
    sc.advance_to("06:10")
    assert sc.mode(1) is IDLE


def test_holiday_start_mid_cycle_switches_zones_off() -> None:
    sc = Scenario(2, temps={1: 21.8})
    sc.step()
    sc.advance_to("06:40")
    assert sc.mode(1) is HEATING
    sc.holiday("12:00", DAY + timedelta(days=3))
    sc.step()
    assert sc.setpoint(1) == 18.0
    assert sc.mode(1) is IDLE  # rule 6 with the holiday SetPoint
    assert sc.hp  # min ON from 06:30: the heat is spread
    sc.advance_to("07:30")
    assert not sc.hp


def test_holiday_temperature_is_per_zone() -> None:
    """Each zone has its own holiday temperature, above or below its base."""
    sc = Scenario(2, temps=20.0)
    sc.zone_params["zone_1"] = ZoneParams(base_setpoint=16.0, holiday_temp=15.0)
    sc.zone_params["zone_2"] = ZoneParams(holiday_temp=19.5)
    sc.holiday("12:00")
    sc.step()
    assert (sc.setpoint(1), sc.setpoint(2)) == (15.0, 19.5)
    assert sc.holiday_active
    sc.holiday(None)  # stopped manually
    sc.step()
    assert (sc.setpoint(1), sc.setpoint(2)) == (16.0, 22.0)
    assert not sc.holiday_active
    assert sc.mode(2) is HEATING  # a raise: no wait


def test_holiday_without_end_runs_until_switched_off() -> None:
    """No end: active for days until switched OFF by hand."""
    sc = Scenario(1, temps=20.0)
    sc.holiday_without_end()
    sc.step()
    assert sc.setpoint(1) == 18.0
    sc.advance(minutes=3 * 24 * 60)
    assert sc.holiday_active
    assert sc.holiday_on  # the adapter keeps it on
    sc.holiday(None)
    sc.step()
    assert sc.setpoint(1) == 22.0
    assert not sc.holiday_active


def test_holiday_end_is_ignored_while_off() -> None:
    """The end only counts while holiday is switched ON."""
    sc = Scenario(1, temps=20.0)
    sc.holiday_until = at("12:00", DAY + timedelta(days=1), sc.tz)  # set, but not switched ON
    sc.step()
    assert sc.setpoint(1) == 22.0
    assert not sc.holiday_active


def test_outputs_report_ended_one_shots() -> None:
    sc = Scenario(1, start="10:00")
    sc.add_manual([1], "08:00", "09:00")
    daily = sc.add_manual([1], "08:00", "09:00", weekdays=range(7))
    outputs, _, _ = step(sc.config, sc.state, sc.inputs(), sc.now)
    assert outputs.ended_schedules == ("s1",)
    sc.step()
    assert sc.schedules == [daily]


def test_holiday_until_must_be_timezone_aware() -> None:
    sc = Scenario(1)
    naive = datetime(2026, 1, 13, 12, 0)  # noqa: DTZ001
    inputs = dataclasses.replace(sc.inputs(), holiday_until=naive)
    with pytest.raises(ValueError, match="holiday_until must carry a time zone"):
        step(sc.config, sc.state, inputs, sc.now)


# ---------------------------------------------------------------- step contract


def _inputs(zones: dict[str, ZoneInput], params: dict[str, ZoneParams]) -> Inputs:
    return Inputs(
        zones=zones,
        heat_source=OutputState.OFF,
        zone_params=params,
        global_params=GlobalParams(),
        heating_season=True,
        control_active=True,
        time_zone=UTC,
        reconcile_tick=True,
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


def test_time_zone_is_required() -> None:
    """HA's time zone comes with the inputs."""
    sc = Scenario(1)
    inputs = dataclasses.replace(sc.inputs(), time_zone=None)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="time_zone must be a tzinfo"):
        step(sc.config, sc.state, inputs, sc.now)


def test_valved_zone_needs_its_valve_state() -> None:
    config = make_config(1)
    inputs = _inputs({"zone_1": ZoneInput(22.0, None, None)}, {"zone_1": ZoneParams()})
    with pytest.raises(ValueError, match="zone_1: the valve state is missing"):
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
