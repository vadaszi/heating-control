"""What a loaded config entry holds (D-124): the controller and the heartbeat client."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.config_entries import ConfigEntry

from .controller import FloorheatController
from .heartbeat import HeartbeatClient


@dataclass(frozen=True)
class FloorheatRuntime:
    controller: FloorheatController
    heartbeat: HeartbeatClient


type FloorheatConfigEntry = ConfigEntry[FloorheatRuntime]
