"""Schedules and holiday in the adapter (docs/design.md §3.4, D-136 to D-139)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

import pytest

from custom_components.multizone_floor_heating_manager.core.config import (
    ConfigError,
    CoreConfig,
    ZoneConfig,
)
from custom_components.multizone_floor_heating_manager.core.schedule import (
    WEEKDAYS,
    Schedule,
    ScheduleKind,
)
from custom_components.multizone_floor_heating_manager.core.units import TemperatureUnit
from custom_components.multizone_floor_heating_manager.schedules import (
    days_text,
    schedule_label,
    schedule_view,
)

from .conftest import START, World, make_conf
from .test_restart import _preload, restarted

AUTO, MANUAL = ScheduleKind.AUTO, ScheduleKind.MANUAL
SETPOINT_1 = "sensor.zone_1_floor_heating_effective_target_temperature"
SCHEDULES = "sensor.floor_heating_schedules"
MODE = "sensor.floor_heating_mode"
DASH = "\u2013"  # en dash in the labels


async def _add(world: World, **kwargs: Any) -> Schedule:
    values: dict[str, Any] = {
        "kind": AUTO,
        "zone_ids": ("zone_1",),
        "start": time(6, 0),
        "end": time(8, 0),
        "weekdays": WEEKDAYS,
        "temperature": 23.0,
    }
    schedule = await world.controller.async_add_schedule(**(values | kwargs))
    await world.hass.async_block_till_done()
    return schedule


async def test_auto_schedule_sets_the_effective_setpoint(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    schedule = await _add(world)
    assert schedule.id == "1"
    assert world.state(SETPOINT_1) == "23.0"
    assert world.state(SCHEDULES) == "1"
    state = world.hass.states.get(SCHEDULES)
    assert state is not None
    [view] = state.attributes["schedules"]
    assert view == {
        "id": "1",
        "label": f"#1 Auto · Zone 1 · Every day 06:00{DASH}08:00 · 23.0 °C",
        "kind": "auto",
        "zones": ["zone_1"],
        "date": None,
        "weekdays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"],
        "start": "06:00",
        "end": "08:00",
        "temperature": 23.0,
    }


async def test_rejected_schedule_is_not_stored(world: World) -> None:
    """A12: an overlapping auto schedule is rejected with an error; nothing is stored."""
    world.setup_entities()
    assert await world.setup()
    await _add(world)
    with pytest.raises(ConfigError, match="overlaps auto schedule '1' for zone_1"):
        await _add(world, weekdays=frozenset({0}), start=time(7, 0), end=time(9, 0))
    with pytest.raises(ConfigError) as err:
        await _add(world, start=time(9, 0), end=time(9, 0))
    assert err.value.errors == ["start and end must differ"]  # no internal id in the text
    settings = world.controller.settings
    assert [s.id for s in settings.schedules] == ["1"]
    assert settings.schedule_counter == 1
    assert (await _add(world, zone_ids=("zone_2",))).id == "2"


async def test_ended_one_shot_is_deleted(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    await _add(world, kind=MANUAL, temperature=None, weekdays=frozenset(), on_date=START.date(),
               end=time(6, 30))  # fmt: skip
    assert world.mode(1) == "forced"
    await world.advance(30)
    assert world.controller.settings.schedules == ()
    assert world.state(SCHEDULES) == "0"
    assert world.controller.settings.schedule_counter == 1  # numbers are never reused


async def test_delete_schedule(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    await _add(world)
    with pytest.raises(ConfigError, match="there is no schedule #7"):
        await world.controller.async_delete_schedule("7")
    await world.controller.async_delete_schedule("1")
    await world.hass.async_block_till_done()
    assert world.state(SETPOINT_1) == "22.0"
    assert world.state(SCHEDULES) == "0"


async def _end(world: World, when: datetime) -> None:
    """Set the holiday end date and time (the tests run in UTC: local = UTC)."""
    await world.controller.async_set_holiday_end_date(when.date())
    await world.controller.async_set_holiday_end_time(when.time())
    await world.hass.async_block_till_done()


async def test_holiday_without_end_runs_until_switched_off(world: World) -> None:
    """D-137: switched on without an end date, holiday runs until switched off."""
    world.setup_entities()
    assert await world.setup()
    await world.controller.async_set_holiday_end_time(time(15, 0))  # a time alone: no end
    await world.controller.async_set_holiday(True)
    await world.hass.async_block_till_done()
    assert world.controller.holiday_end is None
    assert world.state(MODE) == "holiday"
    assert world.state(SETPOINT_1) == "18.0"
    await world.advance(24 * 60)
    assert world.controller.settings.holiday_on
    await world.controller.async_set_holiday(False)
    await world.hass.async_block_till_done()
    assert world.state(MODE) == "normal"
    assert world.state(SETPOINT_1) == "22.0"


async def test_holiday_end_reached_switches_off_and_clears_the_date(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    await _end(world, START + timedelta(hours=2))
    await world.controller.async_set_holiday(True)
    await world.advance(119)
    assert world.state(MODE) == "holiday"
    await world.advance()
    settings = world.controller.settings
    assert (settings.holiday_on, settings.holiday_end_date) == (False, None)
    assert settings.holiday_end_time == time(8, 0)  # the time stays (D-142)
    assert world.state(MODE) == "normal"


async def test_values_are_checked_only_when_switching_on(world: World) -> None:
    """D-137: entering a past end is accepted; switching on with it is refused."""
    world.setup_entities()
    assert await world.setup()
    await _end(world, START - timedelta(days=1))  # accepted while off
    assert world.controller.settings.holiday_end_date == (START - timedelta(days=1)).date()
    with pytest.raises(ConfigError, match="the holiday end is in the past"):
        await world.controller.async_set_holiday(True)
    assert not world.controller.settings.holiday_on


async def test_date_only_ends_at_the_stored_time(world: World) -> None:
    """A date alone uses the stored end time (12:00 on a first install)."""
    world.setup_entities()
    assert await world.setup()
    await world.controller.async_set_holiday_end_date(START.date())
    assert world.controller.holiday_end == START.replace(hour=12)


async def test_switching_holiday_off_clears_the_date(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    await _end(world, START + timedelta(days=3))
    await world.controller.async_set_holiday(True)
    await world.controller.async_set_holiday(True)  # already on: nothing changes
    assert world.controller.holiday_end == START + timedelta(days=3)
    await world.controller.async_set_holiday(False)
    assert world.controller.settings.holiday_end_date is None
    assert world.controller.settings.holiday_end_time == time(6, 0)


async def test_changing_the_end_while_on_moves_it_or_ends_it(world: World) -> None:
    """D-137: a later end extends the holiday; an end in the past ends it at once."""
    world.setup_entities()
    assert await world.setup()
    await _end(world, START + timedelta(hours=1))
    await world.controller.async_set_holiday(True)
    await world.controller.async_set_holiday_end_time(time(9, 0))
    await world.advance(90)
    assert world.state(MODE) == "holiday"
    await world.controller.async_set_holiday_end_time(time(7, 0))  # now in the past
    await world.hass.async_block_till_done()
    assert world.state(MODE) == "normal"
    assert not world.controller.settings.holiday_on


async def test_holiday_end_in_the_local_time_zone(world: World) -> None:
    """The end is local wall-clock time (here Budapest, UTC+1 in January)."""
    await world.hass.config.async_set_time_zone("Europe/Budapest")
    world.setup_entities()
    assert await world.setup()
    await world.controller.async_set_holiday_end_date(START.date())
    await world.controller.async_set_holiday_end_time(time(8, 0))  # 07:00 UTC
    await world.controller.async_set_holiday(True)
    await world.advance(59)
    assert world.controller.settings.holiday_on
    await world.advance()
    assert not world.controller.settings.holiday_on


async def test_schedules_and_holiday_survive_a_restart(world: World) -> None:
    world.setup_entities()
    assert await world.setup()
    await _add(world)
    await world.controller.async_delete_schedule("1")
    await _add(world, zone_ids=None, temperature=21.0)
    end = START + timedelta(days=2, hours=9, minutes=30)
    await _end(world, end)
    await world.controller.async_set_holiday(True)

    async with restarted(world, prepare=lambda new: new.setup_entities()) as new:
        assert await new.setup(live=False)
        settings = new.controller.settings
        assert [(s.id, s.zone_ids, s.temperature) for s in settings.schedules] == [
            ("2", None, 21.0)
        ]
        assert settings.schedule_counter == 2
        assert (settings.holiday_on, new.controller.holiday_end) == (True, end)
        assert new.state(MODE) == "holiday"


async def test_holiday_end_of_0_8_0_is_migrated(world: World, hass_storage: dict[str, Any]) -> None:
    """D-142: the 0.8.0 end (an aware datetime) becomes local date and time."""
    await world.hass.config.async_set_time_zone("Europe/Budapest")
    _preload(
        hass_storage,
        {"settings": {"holiday_on": True, "holiday_end": "2026-01-12T22:00:00+00:00"}},
    )
    world.setup_entities()
    assert await world.setup(live=False)
    settings = world.controller.settings
    assert (settings.holiday_end_date, settings.holiday_end_time) == (date(2026, 1, 12), time(23))
    assert settings.holiday_on
    assert "holiday_end" not in settings.to_dict()


async def test_stored_schedule_for_a_removed_zone_is_dropped(
    world: World, caplog: pytest.LogCaptureFixture
) -> None:
    world.setup_entities(3)
    assert await world.setup(make_conf(3))
    await _add(world, zone_ids=("zone_3",))
    await _add(world, zone_ids=("zone_2", "zone_3"), kind=MANUAL, temperature=None)

    async with restarted(world, prepare=lambda new: new.setup_entities(2)) as new:
        assert await new.setup(make_conf(2), live=False)
        settings = new.controller.settings
        assert [(s.id, s.zone_ids) for s in settings.schedules] == [("2", ("zone_2",))]
        assert settings.schedule_counter == 2
    assert "Discarded schedule '1': its zones are no longer configured" in caplog.text
    assert "Removed zones no longer configured from schedule '2': zone_3" in caplog.text


@pytest.mark.parametrize(
    "data",
    [
        {"holiday_on": "yes"},
        {"holiday_on": True, "holiday_end": "tomorrow"},
        {"holiday_on": True, "holiday_end": "2026-01-13T12:00:00"},  # no time zone
        {"holiday_on": True, "holiday_end_date": "13 January"},
        {"holiday_on": True, "holiday_end_date": None, "holiday_end_time": "noon"},
    ],
)
async def test_unusable_stored_holiday_is_off(
    world: World,
    hass_storage: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
    data: dict[str, Any],
) -> None:
    _preload(hass_storage, {"settings": data})
    world.setup_entities()
    assert await world.setup(live=False)
    assert "Stored holiday is unusable" in caplog.text
    assert not world.controller.settings.holiday_on


async def test_stored_counter_is_never_below_a_used_number(
    world: World, hass_storage: dict[str, Any]
) -> None:
    stored = Schedule("5", AUTO, time(6), time(8), None, weekdays=WEEKDAYS, temperature=21.0)
    _preload(hass_storage, {"settings": {"schedules": [stored.to_dict()], "schedule_counter": 2}})
    world.setup_entities()
    assert await world.setup(live=False)
    assert world.controller.settings.schedule_counter == 5


# ---------------------------------------------------------------- labels (D-138)


def _schedule(**kwargs: Any) -> Schedule:
    values: dict[str, Any] = {
        "id": "3",
        "kind": MANUAL,
        "start": time(22, 0),
        "end": time(2, 0),
        "weekdays": WEEKDAYS,
    }
    return Schedule(**(values | kwargs))


@pytest.mark.parametrize(
    ("changes", "text"),
    [
        ({}, "Every day"),
        ({"weekdays": frozenset(range(5))}, f"Monday{DASH}Friday"),
        ({"weekdays": frozenset({5, 6})}, f"Saturday{DASH}Sunday"),
        ({"weekdays": frozenset({2})}, "Wednesday"),
        ({"weekdays": frozenset({4, 0, 2})}, "Mon, Wed, Fri"),
        ({"weekdays": frozenset(), "on_date": date(2026, 10, 5)}, "2026-10-05"),
    ],
)
def test_days_text(changes: dict[str, Any], text: str) -> None:
    assert days_text(_schedule(**changes)) == text


def test_label_in_fahrenheit_with_zone_names() -> None:
    config = CoreConfig(
        zones=(ZoneConfig("zone_1", "Living room"), ZoneConfig("zone_2", "Kitchen"))
    )
    schedule = _schedule(kind=AUTO, temperature=21.0, zone_ids=("zone_2", "zone_1"))
    assert schedule_label(schedule, config, TemperatureUnit.FAHRENHEIT) == (
        f"#3 Auto · Kitchen, Living room · Every day 22:00{DASH}02:00 · 69.8 °F"
    )
    manual = _schedule(on_date=date(2026, 10, 5), weekdays=frozenset())
    view = schedule_view(manual, config, TemperatureUnit.CELSIUS)
    assert view["label"] == f"#3 Manual · All zones · 2026-10-05 22:00{DASH}02:00"
    assert (view["zones"], view["date"], view["weekdays"], view["temperature"]) == (
        "all",
        "2026-10-05",
        [],
        None,
    )
