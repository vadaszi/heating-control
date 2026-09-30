"""The example dashboard (docs/design.md §5.7, D-140): it loads, uses built-in cards
only, refers only to entities that exist with the example configuration, shows every
entity of the integration, and explains every zone state and reason."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from homeassistant.helpers import entity_registry as er
from homeassistant.util.yaml import load_yaml

from custom_components.multizone_floor_heating_manager.const import DOMAIN

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
        if "type" in node:  # views have no type; rows are not visited
            cards.append(node)
        for key in ("views", "cards", "card"):
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
    assert [view["path"] for view in dashboard["views"]] == [
        "floor-heating",
        "floor-heating-settings",
    ]


def test_explanation_card_covers_every_state_and_reason() -> None:
    [card] = [c for c in _cards(_dashboard()) if c.get("title", "").startswith("What the states")]
    texts = json.loads(TRANSLATIONS.read_text())["entity"]["sensor"]
    for key in ("zone_state", "reason"):
        for text in texts[key]["state"].values():
            assert f"**{text}**" in card["content"], text


async def test_every_entity_exists_and_every_integration_entity_is_shown(world: World) -> None:
    hass = world.hass
    for sensor in ("sensor.living_room_temperature", "sensor.bathroom_temperature"):
        hass.states.async_set(
            sensor, "21.5", {"device_class": "temperature", "unit_of_measurement": "°C"}
        )
    world.switch("switch.heat_pump_request", "off")
    world.switch("switch.valve_living_room", "off")
    assert await world.setup(EXAMPLE)

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
