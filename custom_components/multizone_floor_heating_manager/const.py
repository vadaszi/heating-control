"""Constants of the Multizone Floor Heating Manager HA adapter."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Final

from homeassistant.util.hass_dict import HassKey

if TYPE_CHECKING:
    from .schema import FloorheatConfig

DOMAIN: Final = "multizone_floor_heating_manager"
NAME: Final = "Multizone Floor Heating Manager"  # the config entry title (D-127)
SHORT_NAME: Final = "Floor heating"  # the global device and notification titles (D-125)
ZONE_DEVICE_SUFFIX: Final = "floor heating"  # zone device: "<zone name> floor heating"
GLOBAL_DEVICE: Final = "global"  # identifier of the "Floor heating" device

# The validated YAML, kept in memory for the config entry (D-124).
DATA_YAML: HassKey[FloorheatConfig] = HassKey(DOMAIN)

# YAML keys (docs/configuration.md)
CONF_HEAT_SOURCE_SWITCH: Final = "heat_source_switch"
CONF_ZONES: Final = "zones"
CONF_ID: Final = "id"
CONF_NAME: Final = "name"
CONF_SENSOR: Final = "sensor"
CONF_VALVE: Final = "valve"
CONF_SENSOR_OFFSET: Final = "sensor_offset"
CONF_PLAUSIBLE_MIN: Final = "plausible_min"
CONF_PLAUSIBLE_MAX: Final = "plausible_max"
CONF_RECONCILE_INTERVAL: Final = "reconcile_interval"
CONF_OUTPUT_MISMATCH_ALERT: Final = "output_mismatch_alert"
CONF_NOTIFY: Final = "notify"
CONF_SHELLYS: Final = "shellys"
CONF_HOST: Final = "host"
CONF_SCRIPT_ID: Final = "script_id"
CONF_SWITCHES: Final = "switches"
CONF_PASSWORD: Final = "password"
CONF_NO_WATCHDOG: Final = "no_watchdog"
CONF_HEARTBEAT_INTERVAL: Final = "heartbeat_interval"
CONF_HEARTBEAT_FAIL_ALERT: Final = "heartbeat_fail_alert"
CONF_HEARTBEAT_TIMEOUT: Final = "heartbeat_timeout"
CONF_HEARTBEAT_CHECK_INTERVAL: Final = "heartbeat_check_interval"

NO_VALVE: Final = "none"

DEFAULT_RECONCILE_INTERVAL: Final = 60  # s (§4)
MIN_RECONCILE_INTERVAL: Final = 10
MAX_RECONCILE_INTERVAL: Final = 300

# Heartbeat to the Shelly watchdog scripts (§4, §5.4, docs/heartbeat-protocol.md)
DEFAULT_HEARTBEAT_INTERVAL: Final = 300  # s, HeartbeatInterval
MIN_HEARTBEAT_INTERVAL: Final = 60
MAX_HEARTBEAT_INTERVAL: Final = 3600
DEFAULT_HEARTBEAT_FAIL_ALERT: Final = 3  # HeartbeatFailAlert (D-61)
DEFAULT_HEARTBEAT_TIMEOUT: Final = 18000  # s; the scripts' expected heartbeat_timeout_s
MAX_SCRIPT_SECONDS: Final = 604800  # the scripts accept 1 s to 7 days
HEARTBEAT_CALL_TIMEOUT: Final = 10  # s; per heartbeat call
HEARTBEAT_LIVENESS_TICKS: Final = 3  # heartbeat only after a run within 3 intervals (D-122)
SHELLY_USERNAME: Final = "admin"  # Shelly digest auth always uses "admin"

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
NOTIFY_TIMEOUT: Final = 30  # s; per notify call

STORAGE_KEY: Final = DOMAIN
STORAGE_VERSION: Final = 1
SAVE_DELAY: Final = 30  # s; at most one write per delay, flushed when HA stops
