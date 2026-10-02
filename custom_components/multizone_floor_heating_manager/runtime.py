"""What a loaded config entry holds (D-124): the controller, heartbeat and watchdog ping."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry

from .controller import FloorheatController
from .heartbeat import HeartbeatClient
from .watchdog import WatchdogPing


@dataclass(frozen=True)
class FloorheatRuntime:
    controller: FloorheatController
    heartbeat: HeartbeatClient
    watchdog: WatchdogPing


type FloorheatConfigEntry = ConfigEntry[FloorheatRuntime]
