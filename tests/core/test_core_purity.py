"""Architecture guard for the control core (docs/design.md §5.3).

The core must not import Home Assistant and must never read the system clock.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import custom_components.floorheat.core as core

CORE_DIR = Path(__file__).resolve().parents[2] / "custom_components" / "floorheat" / "core"

# Calls that read the current time. Legitimate uses such as `dt.time()` (time-of-day
# part of a passed-in datetime) are not listed.
_CLOCK_CALLS = frozenset({"now", "utcnow", "today"})
_FORBIDDEN_MODULES = frozenset({"homeassistant", "time"})


def find_violations(source: str) -> list[str]:
    """Return a description of every forbidden import or clock read in `source`."""
    violations: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import | ast.ImportFrom):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            else:
                modules = [node.module] if node.level == 0 and node.module else []
            violations.extend(
                f"line {node.lineno}: import of {module}"
                for module in modules
                if module.split(".")[0] in _FORBIDDEN_MODULES
            )
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in _CLOCK_CALLS
        ):
            violations.append(f"line {node.lineno}: clock read .{node.func.attr}()")
    return violations


def test_core_has_no_ha_imports_or_clock_reads() -> None:
    files = sorted(CORE_DIR.rglob("*.py"))
    assert files, f"no core modules found in {CORE_DIR}"
    problems = {
        str(path.relative_to(CORE_DIR)): found
        for path in files
        if (found := find_violations(path.read_text(encoding="utf-8")))
    }
    assert problems == {}


def test_core_package_imports() -> None:
    assert core.__doc__


@pytest.mark.parametrize(
    "source",
    [
        "import homeassistant",
        "from homeassistant.core import HomeAssistant",
        "import homeassistant.util.dt as dt_util",
        "import time",
        "from time import monotonic",
        "from datetime import datetime\nx = datetime.now()",
        "import datetime\nx = datetime.datetime.utcnow()",
        "from datetime import date\nx = date.today()",
    ],
)
def test_guard_detects_violations(source: str) -> None:
    assert find_violations(source)


@pytest.mark.parametrize(
    "source",
    [
        "from datetime import datetime, timedelta",
        "from zoneinfo import ZoneInfo",
        "def f(now):\n    return now.time()",
        "from . import models",
    ],
)
def test_guard_allows_clean_code(source: str) -> None:
    assert find_violations(source) == []
