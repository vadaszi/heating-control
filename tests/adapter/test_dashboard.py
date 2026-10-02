"""The example dashboard (docs/design.md §5.7, D-140): it loads, uses built-in cards
only, refers only to entities that exist with the example configuration, shows every
entity of the integration, and explains every zone state and reason."""

from __future__ import annotations

import json
import re
from datetime import date, time
from pathlib import Path
from typing import Any

from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.template import Template
from homeassistant.util.yaml import load_yaml

from custom_components.multizone_floor_heating_manager.const import DOMAIN
from custom_components.multizone_floor_heating_manager.core.schedule import WEEKDAYS, ScheduleKind

from .conftest import World

ROOT = Path(__file__).parents[2]
DASHBOARD = ROOT / "examples" / "dashboard.example.yaml"
TRANSLATIONS = ROOT / "custom_components" / DOMAIN / "translations" / "en.json"

EXAMPLE = {
    DOMAIN: {
        "heat_source_switch": "switch.heat_pump_request",
        "no_watchdog": ["switch.heat_pump_request", "switch.valve_living_room"],
        "zones": [
            {
                "id": "living_room",
                "name": "Living room",
                "sensor": "sensor.living_room_temperature",
                "valve": "switch.valve_living_room",
            },
            {
                "id": "bathroom",
                "name": "Bathroom",
                "sensor": "sensor.bathroom_temperature",
                "valve": "none",
            },
        ],
    }
}
BUILT_IN_CARDS = {
    "conditional",
    "entities",
    "history-graph",
    "markdown",
    "thermostat",
    "vertical-stack",
}
_ENTITY_ID = re.compile(r"^[a-z_]+\.[a-z0-9_]+$")
_TEMPLATE_ENTITY = re.compile(r"state_attr\('([a-z_]+\.[a-z0-9_]+)'")


def _dashboard() -> dict[str, Any]:
    data = load_yaml(DASHBOARD)
    assert isinstance(data, dict)
    return data


def _cards(node: Any) -> list[dict[str, Any]]:
    """Every card, nested ones included."""
    cards: list[dict[str, Any]] = []
    if isinstance(node, dict):
        if "type" in node and node["type"] not in ("sections", "grid"):  # views, sections
            cards.append(node)
        for key in ("views", "sections", "cards", "card"):
            value = node.get(key)
            for child in value if isinstance(value, list) else [value]:
                cards += _cards(child)
    return cards


def _entity_ids(node: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "entity" and isinstance(value, str):
                found.add(value)
            elif isinstance(value, str):
                found |= set(_TEMPLATE_ENTITY.findall(value))
            else:
                found |= _entity_ids(value)
    elif isinstance(node, list):
        for item in node:
            found |= {item} if isinstance(item, str) and _ENTITY_ID.match(item) else set()
            found |= _entity_ids(item)
    return found


def test_only_built_in_cards() -> None:
    dashboard = _dashboard()
    assert "custom:" not in DASHBOARD.read_text()
    types = {card["type"] for card in _cards(dashboard)}
    assert types <= BUILT_IN_CARDS, types - BUILT_IN_CARDS
    assert [(v["path"], v["type"]) for v in dashboard["views"]] == [
        ("floor-heating", "sections"),
        ("floor-heating-setup", "sections"),
    ]


def test_no_header_toggle() -> None:
    """A "toggle all" header switch would switch the season, Control active and holiday
    with one tap (owner, 2026-10-01)."""
    entities_cards = [c for c in _cards(_dashboard()) if c["type"] == "entities"]
    assert entities_cards
    for card in entities_cards:
        assert card.get("show_header_toggle") is False, card.get("title")


def test_badges_show_everything_at_a_glance() -> None:
    """D-143: the daily view's badges, always shown (no visibility conditions)."""
    daily, _setup = _dashboard()["views"]
    badges = daily["badges"]
    assert all(badge["type"] == "entity" and "visibility" not in badge for badge in badges)
    shown = [badge["entity"] for badge in badges]
    for entity_id in (
        "binary_sensor.floor_heating_heat_request",
        "sensor.floor_heating_heat_source",
        "switch.heat_pump_request",
        "sensor.floor_heating_alerts",
        "climate.living_room_floor_heating",
        "climate.bathroom_floor_heating",
        "switch.valve_living_room",
    ):
        assert entity_id in shown, entity_id
    for entity_id in (  # removed by the owner (D-144): they are on the House card
        "sensor.floor_heating_mode",
        "switch.floor_heating_heating_season",
        "switch.floor_heating_control_active",
    ):
        assert entity_id not in shown, entity_id
    wanted = [b for b in badges if b.get("state_content") == ["valve"]]
    assert [b["entity"] for b in wanted] == ["climate.living_room_floor_heating"]


def test_zones_then_house_and_a_setup_view() -> None:
    """D-145, D-146: the daily view has one section per zone, then the house (with the
    always shown alerts) and the help card (3 columns, so the zones fill the rows); the
    Setup view has one card per parameter group, holiday and schedules."""
    daily, setup = _dashboard()["views"]
    first_cards = [section["cards"][0] for section in daily["sections"]]
    assert [c["cards"][0]["entity"] for c in first_cards[:2]] == [
        "climate.living_room_floor_heating",
        "climate.bathroom_floor_heating",
    ]
    assert daily["max_columns"] == 3
    assert [c.get("title") for c in daily["sections"][2]["cards"]] == ["Alerts", "House"]
    assert daily["sections"][3]["cards"][0]["title"] == "What the states and reasons mean"
    titles = [c.get("title") for c in _cards(setup)]
    for title in (
        "Hysteresis",
        "Wait time (open-window filter)",
        "Heat source",
        "Sensors and failsafe",
        "Manual schedules",
        "Off season",
        "Holiday",
        "Schedules",
        "New schedule",
    ):
        assert title in titles, title
    [hysteresis] = [c for c in _cards(setup) if c.get("title") == "Hysteresis"]
    assert [row["entity"] for row in hysteresis["entities"]] == [
        "number.living_room_floor_heating_hysteresis",
        "number.bathroom_floor_heating_hysteresis",
    ]


def test_explanation_card_covers_every_state_and_reason() -> None:
    [card] = [c for c in _cards(_dashboard()) if c.get("title", "").startswith("What the states")]
    texts = json.loads(TRANSLATIONS.read_text())["entity"]["sensor"]
    for key in ("zone_state", "reason", "heat_source"):
        for text in texts[key]["state"].values():
            assert f"**{text}**" in card["content"], text


async def _setup_example(world: World) -> None:
    hass = world.hass
    for sensor in ("sensor.living_room_temperature", "sensor.bathroom_temperature"):
        hass.states.async_set(
            sensor, "21.5", {"device_class": "temperature", "unit_of_measurement": "°C"}
        )
    world.switch("switch.heat_pump_request", "off")
    world.switch("switch.valve_living_room", "off")
    assert await world.setup(EXAMPLE)


async def test_every_entity_exists_and_every_integration_entity_is_shown(world: World) -> None:
    await _setup_example(world)
    hass = world.hass
    shown = _entity_ids(_dashboard())
    missing = {entity_id for entity_id in shown if hass.states.get(entity_id) is None}
    assert not missing, missing
    registry = er.async_get(hass)
    ours = {
        entry.entity_id
        for entry in registry.entities.values()
        if entry.platform == DOMAIN and entry.config_entry_id == world.entry.entry_id
    }
    assert {"climate.living_room_floor_heating", "button.floor_heating_add_schedule"} <= ours
    assert ours - shown == set()


async def test_markdown_templates_render(world: World) -> None:
    """The Markdown cards' templates work with the integration's entities."""
    await _setup_example(world)
    hass = world.hass
    markdown = [c["content"] for c in _cards(_dashboard()) if c["type"] == "markdown"]
    for content in markdown:
        Template(content, hass).async_render()  # raises on a template error
    [alerts] = [c for c in markdown if "floor_heating_alerts" in c]
    assert Template(alerts, hass).async_render().strip() == "No active alerts."
    [holiday] = [c for c in markdown if "holiday_end_date" in c]
    assert "**No end date:**" in Template(holiday, hass).async_render()
    await world.controller.async_set_holiday_end_date(date(2026, 1, 14))
    await world.controller.async_set_holiday_end_time(time(15, 30))
    await hass.async_block_till_done()
    assert "**Ends:** Wed 2026-01-14 15:30" in Template(holiday, hass).async_render()
    for start in (22, 3):
        await world.controller.async_add_schedule(
            kind=ScheduleKind.MANUAL,
            zone_ids=None,
            start=time(start),
            end=time(start + 1 if start < 23 else 0),
            weekdays=WEEKDAYS,
        )
    await hass.async_block_till_done()
    [schedules] = [c for c in markdown if "floor_heating_schedules" in c]
    lines = [line for line in Template(schedules, hass).async_render().splitlines() if line]
    assert [line[:4] for line in lines] == ["- #1", "- #2"]  # one Markdown list item each
