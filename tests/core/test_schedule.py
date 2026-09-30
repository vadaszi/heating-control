"""Schedules and holiday (docs/design.md §3.4, D-16 to D-19, D-57, D-58, D-130 to D-136)."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from custom_components.multizone_floor_heating_manager.core.config import (
    ConfigError,
    CoreConfig,
    ZoneConfig,
    ZoneParams,
)
from custom_components.multizone_floor_heating_manager.core.schedule import (
    WEEKDAYS,
    Schedule,
    ScheduleKind,
    ZoneTarget,
    check_new_schedule,
    ended_schedules,
    holiday_active,
    load_schedules,
    overlaps,
    schedules_to_list,
    zone_target,
)

AUTO, MANUAL = ScheduleKind.AUTO, ScheduleKind.MANUAL
BERLIN = ZoneInfo("Europe/Berlin")
MONDAY = date(2026, 1, 12)
SUNDAY = date(2026, 1, 18)
CONFIG = CoreConfig(
    zones=(
        ZoneConfig(id="zone_1", name="Zone 1"),
        ZoneConfig(id="zone_2", name="Zone 2"),
        ZoneConfig(id="zone_3", name="Zone 3"),
    )
)
PARAMS = ZoneParams(base_setpoint=22.0, holiday_temp=17.0)


def _t(hhmm: str) -> time:
    return time.fromisoformat(hhmm)


def auto(
    start: str,
    end: str,
    temperature: float = 23.0,
    *,
    zones: tuple[str, ...] | None = ("zone_1",),
    on: date | None = None,
    weekdays: frozenset[int] = frozenset(),
    schedule_id: str = "a",
) -> Schedule:
    return Schedule(
        id=schedule_id,
        kind=AUTO,
        start=_t(start),
        end=_t(end),
        zone_ids=zones,
        on_date=on if on or weekdays else MONDAY,
        weekdays=weekdays,
        temperature=temperature,
    )


def manual(
    start: str,
    end: str,
    *,
    zones: tuple[str, ...] | None = ("zone_1",),
    on: date | None = None,
    weekdays: frozenset[int] = frozenset(),
    schedule_id: str = "m",
) -> Schedule:
    return Schedule(
        id=schedule_id,
        kind=MANUAL,
        start=_t(start),
        end=_t(end),
        zone_ids=zones,
        on_date=on if on or weekdays else MONDAY,
        weekdays=weekdays,
    )


def local(hhmm: str, day: date = MONDAY, tz: ZoneInfo | None = None) -> datetime:
    return datetime.combine(day, _t(hhmm), tzinfo=tz or UTC)


# ---------------------------------------------------------------- validation


def test_valid_schedules() -> None:
    assert auto("13:00", "17:00").one_shot
    assert not auto("13:00", "17:00", weekdays=WEEKDAYS).one_shot
    assert manual("22:00", "02:00", zones=None).covers("anything")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"end": time(13, 0)}, "start and end must differ"),
        ({"temperature": None}, "an auto schedule needs a temperature"),
        ({"temperature": 9.9}, "temperature: 9.9 °C is out of range (10 to 30 °C)"),
        ({"temperature": 30.1}, "temperature: 30.1 °C is out of range (10 to 30 °C)"),
        ({"weekdays": frozenset({1})}, "has both a date and weekdays"),
        ({"on_date": None}, "needs a date (one-shot) or weekdays (recurring)"),
        ({"on_date": None, "weekdays": frozenset({7})}, "weekdays must be a set of 0"),
        ({"zone_ids": ()}, "needs at least one zone"),
        ({"zone_ids": ("zone_1", "zone_1")}, "zone listed twice"),
        ({"zone_ids": ["zone_1"]}, "zone_ids must be a tuple"),
        ({"start": time(13, 0, tzinfo=UTC)}, "start must be a local time of day"),
        ({"end": "17:00"}, "end must be a local time of day"),
        ({"on_date": datetime(2026, 1, 12, tzinfo=UTC)}, "date must be a date"),
        ({"id": ""}, "id must be a non-empty string"),
        ({"kind": "auto"}, "kind must be auto or manual"),
    ],
)
def test_invalid_schedules_are_rejected(changes: dict[str, object], message: str) -> None:
    with pytest.raises(ConfigError) as err:
        dataclasses.replace(auto("13:00", "17:00"), **changes)  # type: ignore[arg-type]
    assert any(message in error for error in err.value.errors), err.value.errors


def test_temperature_range_is_inclusive() -> None:
    assert auto("13:00", "17:00", 10.0).temperature == 10.0
    assert auto("13:00", "17:00", 30.0).temperature == 30.0


def test_manual_schedule_has_no_temperature() -> None:
    with pytest.raises(ConfigError, match="a manual schedule has no temperature"):
        dataclasses.replace(manual("04:00", "06:00"), temperature=22.0)


def test_every_problem_is_reported_at_once() -> None:
    with pytest.raises(ConfigError) as err:
        Schedule(id="x", kind=AUTO, start=_t("08:00"), end=_t("08:00"), zone_ids=())
    assert len(err.value.errors) == 4  # window, zones, no day, no temperature
    assert all(error.startswith("schedule 'x': ") for error in err.value.errors)


# ---------------------------------------------------------------- windows (D-132)


def test_window_is_half_open() -> None:
    schedule = auto("13:00", "17:00")
    assert schedule.active_window(local("12:59"), UTC) is None
    assert schedule.active_window(local("13:00"), UTC) == (local("13:00"), local("17:00"))
    assert schedule.active_window(local("16:59"), UTC) is not None
    assert schedule.active_window(local("17:00"), UTC) is None


def test_window_crossing_midnight_belongs_to_its_start_day() -> None:
    schedule = auto("22:00", "02:00", weekdays=frozenset({6}))  # Sunday night
    monday_early = local("01:00", SUNDAY + timedelta(days=1))
    assert schedule.active_window(monday_early, UTC) == (
        local("22:00", SUNDAY),
        local("02:00", SUNDAY + timedelta(days=1)),
    )
    assert schedule.active_window(local("01:00", SUNDAY), UTC) is None  # Saturday's night
    assert schedule.active_window(local("23:00", SUNDAY), UTC) is not None


def test_recurring_window_on_selected_weekdays() -> None:
    schedule = auto("10:00", "16:00", weekdays=frozenset({5, 6}))
    assert schedule.active_window(local("12:00", SUNDAY), UTC) is not None
    assert schedule.active_window(local("12:00", MONDAY), UTC) is None


def test_window_uses_the_local_time_zone() -> None:
    schedule = auto("13:00", "17:00")
    assert schedule.active_window(local("12:30"), BERLIN) is not None  # 13:30 in Berlin
    assert schedule.active_window(local("16:00"), BERLIN) is None  # 17:00 in Berlin


def test_one_shot_ends_after_its_window() -> None:
    schedule = auto("22:00", "02:00")
    tuesday = MONDAY + timedelta(days=1)
    assert not schedule.has_ended(local("23:00"), UTC)
    assert not schedule.has_ended(local("01:59", tuesday), UTC)
    assert schedule.has_ended(local("02:00", tuesday), UTC)
    assert not auto("22:00", "02:00", weekdays=WEEKDAYS).has_ended(local("03:00", tuesday), UTC)


def test_ended_schedules_lists_finished_one_shots() -> None:
    schedules = [
        auto("08:00", "09:00", schedule_id="done"),
        auto("10:00", "11:00", schedule_id="later"),
        manual("08:00", "09:00", weekdays=WEEKDAYS, schedule_id="daily"),
    ]
    assert ended_schedules(schedules, local("09:00"), UTC) == ("done",)


# ---------------------------------------------------------------- DST (D-96, D-134)

SPRING = date(2026, 3, 29)  # 02:00 -> 03:00 in Europe/Berlin
AUTUMN = date(2026, 10, 25)  # 03:00 -> 02:00 in Europe/Berlin


def test_window_in_the_spring_gap_is_shifted_by_the_gap() -> None:
    start, end = auto("02:00", "02:30", on=SPRING).window_on(SPRING, BERLIN)
    assert start.astimezone(BERLIN).time() == time(3, 0)
    assert end.astimezone(BERLIN).time() == time(3, 30)


def test_window_emptied_by_the_spring_gap() -> None:
    schedule = auto("02:30", "03:15", on=SPRING)  # starts 03:30 CEST, after its end
    start, end = schedule.window_on(SPRING, BERLIN)
    assert start == end
    for minute in range(0, 240):
        now = datetime.combine(SPRING, time(0, 0), tzinfo=BERLIN).astimezone(UTC)
        assert schedule.active_window(now + timedelta(minutes=minute), BERLIN) is None
    assert schedule.has_ended(start, BERLIN)


def test_window_in_the_repeated_autumn_hour_uses_the_first_occurrence() -> None:
    start, end = auto("02:10", "02:50", on=AUTUMN).window_on(AUTUMN, BERLIN)
    assert end - start == timedelta(minutes=40)
    assert start.astimezone(BERLIN).utcoffset() == timedelta(hours=2)  # still CEST


@pytest.mark.parametrize("day", [SPRING - timedelta(days=1), AUTUMN - timedelta(days=1)])
def test_night_window_across_dst_keeps_its_wall_clock_ends(day: date) -> None:
    start, end = auto("22:00", "02:00", on=day).window_on(day, BERLIN)
    assert start.astimezone(BERLIN).time() == time(22, 0)
    local_end = end.astimezone(BERLIN).time()
    # Spring: 02:00 does not exist and is shifted by the gap; autumn: first 02:00.
    assert local_end == (time(3, 0) if day.month == 3 else time(2, 0))
    assert end - start == timedelta(hours=4)


# ---------------------------------------------------------------- targets (D-16)


def _target(schedules: list[Schedule], now: datetime, *, holiday: bool = False) -> ZoneTarget:
    return zone_target("zone_1", PARAMS, schedules, holiday, now, UTC)


def test_base_setpoint_without_schedules() -> None:
    target = _target([], local("12:00"))
    assert (target.setpoint, target.forced, target.forced_until) == (22.0, False, None)


def test_auto_schedule_overrides_the_setpoint() -> None:
    assert _target([auto("10:00", "14:00", 23.5)], local("12:00")).setpoint == 23.5
    assert _target([auto("10:00", "14:00", 23.5, zones=("zone_2",))], local("12:00")).setpoint == 22


def test_manual_schedule_keeps_the_setpoint_below_it() -> None:
    target = _target([manual("11:00", "13:00"), auto("10:00", "14:00", 23.5)], local("12:00"))
    assert (target.setpoint, target.forced, target.forced_until) == (23.5, True, local("13:00"))


def test_holiday_beats_every_schedule() -> None:
    schedules = [manual("11:00", "13:00"), auto("10:00", "14:00", 23.5)]
    target = _target(schedules, local("12:00"), holiday=True)
    assert (target.setpoint, target.forced) == (17.0, False)  # the zone's holiday temp
    assert _target(schedules, local("12:00")).forced  # no holiday


def test_holiday_active_until_its_end() -> None:
    assert holiday_active(True, local("15:00"), local("14:59"))
    assert not holiday_active(True, local("15:00"), local("15:00"))


def test_holiday_without_end_is_active_until_switched_off() -> None:
    """D-137: switched ON with no end, holiday runs until it is switched OFF."""
    assert holiday_active(True, None, local("15:00"))
    assert not holiday_active(False, None, local("15:00"))
    assert not holiday_active(False, local("15:00"), local("14:59"))


def test_all_zones_schedule_covers_every_zone() -> None:
    target = zone_target(
        "zone_3", PARAMS, [auto("10:00", "14:00", 21.0, zones=None)], False, local("12:00"), UTC
    )
    assert target.setpoint == 21.0


def test_manual_windows_form_their_union() -> None:
    schedules = [
        manual("04:00", "06:00", schedule_id="a"),
        manual("05:00", "07:00", schedule_id="b"),
        manual("07:00", "08:00", schedule_id="c"),  # starts where b ends
        manual("09:00", "10:00", schedule_id="d"),  # a gap: separate
    ]
    assert _target(schedules, local("04:30")).forced_until == local("08:00")
    assert _target(schedules, local("08:00")).forced is False
    assert _target(schedules, local("09:30")).forced_until == local("10:00")


def test_endless_manual_chain_stops_searching() -> None:
    schedules = [
        manual("00:00", "12:00", weekdays=WEEKDAYS, schedule_id="a"),
        manual("12:00", "00:00", weekdays=WEEKDAYS, schedule_id="b"),
    ]
    until = _target(schedules, local("06:00")).forced_until
    assert until is not None
    assert local("06:00") + timedelta(days=7) < until <= local("06:00") + timedelta(days=9)


# ---------------------------------------------------------------- creation check (D-19)


def _check(new: Schedule, existing: list[Schedule], now: datetime | None = None) -> list[str]:
    return check_new_schedule(new, existing, CONFIG, now or local("00:00"), UTC)


def test_overlapping_auto_schedule_is_rejected() -> None:
    daily = auto("13:00", "17:00", weekdays=WEEKDAYS, schedule_id="daily")
    sunday = auto("16:00", "18:00", weekdays=frozenset({6}), schedule_id="sunday")
    assert _check(sunday, [daily]) == [
        "overlaps auto schedule 'daily' for zone_1; auto schedules for the same zone may "
        "not overlap"
    ]


def test_touching_auto_schedules_are_allowed() -> None:
    first = auto("10:00", "12:00", schedule_id="first")
    assert _check(auto("12:00", "14:00", schedule_id="second"), [first]) == []


def test_auto_schedules_for_different_zones_may_overlap() -> None:
    first = auto("10:00", "12:00", schedule_id="first")
    assert _check(auto("11:00", "13:00", zones=("zone_2",), schedule_id="second"), [first]) == []


def test_all_zones_auto_schedule_overlaps_any_zone() -> None:
    first = auto("10:00", "12:00", zones=("zone_3",), schedule_id="first")
    errors = _check(auto("11:00", "13:00", zones=None, schedule_id="all"), [first])
    assert errors == [
        "overlaps auto schedule 'first' for zone_3; auto schedules for the same zone may "
        "not overlap"
    ]


def test_manual_schedules_may_overlap_anything() -> None:
    existing = [auto("10:00", "12:00", schedule_id="a"), manual("10:00", "12:00")]
    assert _check(manual("11:00", "13:00", schedule_id="m2"), existing) == []
    assert _check(auto("11:00", "13:00", schedule_id="a2"), [existing[1]]) == []


def test_overlap_across_midnight_and_weekdays() -> None:
    saturday_night = auto("23:00", "01:00", weekdays=frozenset({5}), schedule_id="sat")
    sunday_early = auto("00:30", "02:00", weekdays=frozenset({6}), schedule_id="sun")
    monday_early = auto("00:30", "02:00", weekdays=frozenset({0}), schedule_id="mon")
    sunday_night = auto("23:00", "01:00", weekdays=frozenset({6}), schedule_id="sun_night")
    assert overlaps(saturday_night, sunday_early)
    assert not overlaps(saturday_night, monday_early)
    assert overlaps(sunday_night, monday_early)  # the week wraps


def test_one_shot_against_recurring() -> None:
    sunday_only = auto("10:00", "12:00", weekdays=frozenset({6}), schedule_id="sun")
    assert overlaps(auto("11:00", "13:00", on=SUNDAY), sunday_only)
    assert not overlaps(auto("11:00", "13:00", on=MONDAY), sunday_only)


def test_other_creation_errors() -> None:
    existing = [auto("10:00", "12:00", schedule_id="a")]
    assert _check(manual("04:00", "05:00", schedule_id="a"), existing) == [
        "a schedule with id 'a' already exists"
    ]
    assert _check(manual("04:00", "05:00", zones=("zone_1", "attic")), []) == [
        "unknown zones: attic"
    ]
    assert _check(manual("08:00", "09:00"), [], now=local("10:00")) == [
        "its window is already over"
    ]
    assert _check(manual("08:00", "09:00"), [], now=local("08:30")) == []


# ---------------------------------------------------------------- overlap vs brute force

_STEP = timedelta(minutes=15)
_FIRST_DAY = date(2026, 1, 5)  # a Monday
_DAYS = 14

_times = st.builds(time, st.integers(0, 23), st.sampled_from([0, 15, 30, 45]))
_days = st.one_of(
    st.builds(
        lambda offset: (_FIRST_DAY + timedelta(days=offset), frozenset()),
        st.integers(1, _DAYS - 3),
    ),
    st.builds(lambda days: (None, frozenset(days)), st.sets(st.integers(0, 6), min_size=1)),
)


@st.composite
def _schedules(draw: st.DrawFn, schedule_id: str) -> Schedule:
    start = draw(_times)
    end = draw(_times.filter(lambda t: t != start))
    on_date, weekdays = draw(_days)
    return Schedule(
        id=schedule_id,
        kind=AUTO,
        start=start,
        end=end,
        zone_ids=("zone_1",),
        on_date=on_date,
        weekdays=weekdays,
        temperature=21.0,
    )


def _covered(schedule: Schedule, moment: datetime) -> bool:
    """Brute force: is this wall-clock moment inside a window of `schedule`?"""
    for day in (moment.date() - timedelta(days=1), moment.date()):
        if not schedule.applies_on(day):
            continue
        start = datetime.combine(day, schedule.start)
        end_day = day + timedelta(days=1) if schedule.end < schedule.start else day
        if start <= moment < datetime.combine(end_day, schedule.end):
            return True
    return False


@settings(max_examples=300, deadline=None)
@given(a=_schedules("a"), b=_schedules("b"))
def test_overlap_matches_a_minute_scan(a: Schedule, b: Schedule) -> None:
    moment = datetime.combine(_FIRST_DAY, time(0, 0))
    end = moment + timedelta(days=_DAYS)
    brute = False
    while moment < end:
        if _covered(a, moment) and _covered(b, moment):
            brute = True
            break
        moment += _STEP
    assert overlaps(a, b) == brute
    assert overlaps(b, a) == brute


# ---------------------------------------------------------------- persistence (D-132)


@st.composite
def _any_schedule(draw: st.DrawFn) -> Schedule:
    kind = draw(st.sampled_from(ScheduleKind))
    start = draw(st.times())
    end = draw(st.times().filter(lambda t: t != start))
    on_date, weekdays = draw(_days)
    zones = draw(st.one_of(st.none(), st.sampled_from([("zone_1",), ("zone_2", "zone_3")])))
    temperature = draw(st.floats(10, 30)) if kind is AUTO else None
    return Schedule(
        id=draw(st.text(min_size=1).filter(str.strip)),
        kind=kind,
        start=start,
        end=end,
        zone_ids=zones,
        on_date=on_date,
        weekdays=weekdays,
        temperature=temperature,
    )


@given(st.lists(_any_schedule(), max_size=4, unique_by=lambda s: s.id))
def test_serialisation_round_trip(schedules: list[Schedule]) -> None:
    data = json.loads(json.dumps(schedules_to_list(schedules)))
    assert load_schedules(data, CONFIG) == (tuple(schedules), [])


def test_nothing_stored() -> None:
    assert load_schedules(None, CONFIG) == ((), [])


def test_unusable_stored_schedules_are_dropped() -> None:
    good = manual("04:00", "05:00", schedule_id="good").to_dict()
    data = [
        good,
        {**good, "id": "bad_kind", "kind": "sometimes"},
        {**good, "id": "bad_time", "start": "25:00"},
        {**good, "id": "no_start", "start": None},
        {"id": "missing"},
        "not a mapping",
        {**good, "id": "same_window", "end": "04:00"},
        good,  # the same id again
    ]
    schedules, warnings = load_schedules(data, CONFIG)
    assert [s.id for s in schedules] == ["good"]
    assert len(warnings) == 7
    assert warnings[-1] == "Discarded a second stored schedule with id 'good'"
    assert load_schedules({"a": 1}, CONFIG) == (
        (),
        ["Stored schedules are unusable ({'a': 1}); starting without schedules."],
    )


def test_removed_zones_are_dropped_from_schedules() -> None:
    data = [
        manual("04:00", "05:00", zones=("zone_1", "attic"), schedule_id="partly").to_dict(),
        manual("04:00", "05:00", zones=("attic",), schedule_id="gone").to_dict(),
        manual("04:00", "05:00", zones=None, schedule_id="all").to_dict(),
    ]
    schedules, warnings = load_schedules(data, CONFIG)
    assert [(s.id, s.zone_ids) for s in schedules] == [("partly", ("zone_1",)), ("all", None)]
    assert warnings == [
        "Removed zones no longer configured from schedule 'partly': attic",
        "Discarded schedule 'gone': its zones are no longer configured (attic)",
    ]
