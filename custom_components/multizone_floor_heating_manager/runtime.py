"""What a loaded config entry holds: the controller, heartbeat and watchdog ping."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry

from .controller import FloorHeatingController
from .heartbeat import HeartbeatClient
from .watchdog import WatchdogPing


@dataclass(frozen=True)
class FloorHeatingRuntime:
    controller: FloorHeatingController
    heartbeat: HeartbeatClient
    watchdog: WatchdogPing


type FloorHeatingConfigEntry = ConfigEntry[FloorHeatingRuntime]
