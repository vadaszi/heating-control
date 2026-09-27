"""Constants of the floorheat HA adapter."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Final

from homeassistant.util.hass_dict import HassKey

if TYPE_CHECKING:
    from .controller import FloorheatController

DOMAIN: Final = "floorheat"
DATA_CONTROLLER: HassKey[FloorheatController] = HassKey(DOMAIN)

# YAML keys (docs/configuration.md)
CONF_HEAT_SOURCE_SWITCH: Final = "heat_source_switch"
CONF_ZONES: Final = "zones"
CONF_ID: Final = "id"
CONF_NAME: Final = "name"
CONF_SENSOR: Final = "sensor"
CONF_VALVE: Final = "valve"
CONF_POWER_SENSOR: Final = "power_sensor"
CONF_SENSOR_OFFSET: Final = "sensor_offset"
CONF_PLAUSIBLE_MIN: Final = "plausible_min"
CONF_PLAUSIBLE_MAX: Final = "plausible_max"
CONF_RECONCILE_INTERVAL: Final = "reconcile_interval"
CONF_OUTPUT_MISMATCH_ALERT: Final = "output_mismatch_alert"

NO_VALVE: Final = "none"

DEFAULT_RECONCILE_INTERVAL: Final = 60  # s (§4)
MIN_RECONCILE_INTERVAL: Final = 10
MAX_RECONCILE_INTERVAL: Final = 300

# Command retries while an output does not follow (D-108): the delay after the n-th
# command, then REPEAT for every further retry.
COMMAND_BACKOFF: Final = (
    timedelta(minutes=1),
    timedelta(minutes=2),
    timedelta(minutes=4),
    timedelta(minutes=8),
)
COMMAND_BACKOFF_REPEAT: Final = timedelta(minutes=15)
COMMAND_TIMEOUT: Final = 30  # s; a switch service call never blocks the loop longer

STORAGE_KEY: Final = DOMAIN
STORAGE_VERSION: Final = 1
SAVE_DELAY: Final = 30  # s; at most one write per delay, flushed when HA stops
