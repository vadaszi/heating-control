"""Floor Heating Zone Control (floorheat) integration.

Bootstrap stub (work phase P0). The YAML setup, reconcile loop, heartbeat and
watchdog are added in later phases (see docs/implementation-plan.md).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.typing import ConfigType

DOMAIN = "floorheat"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the integration. Does nothing yet."""
    return True
