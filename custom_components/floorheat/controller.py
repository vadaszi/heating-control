"""The reconcile loop (docs/design.md §5.3, §5.5, §3.8).

A run reads the HA states, calls `step`, stores the new state and commands the outputs
that differ from the desired state. Runs are serialised by a lock and start:
- every ReconcileInterval (the only runs that are reconcile ticks, D-99);
- when a mapped sensor or switch changes state;
- when a setting changes.

Shadow mode (Control active OFF, §5.5):
- no commands; the core gets the commanded states as feedback (D-66). When the desired
  state changes, `step` runs again at once, as if the switches had followed (D-109);
- Control active ON → OFF: every switch not reporting OFF gets OFF, retried with backoff
  until it has reported OFF once, also across a restart; then nothing more is sent
  (D-69, D-110).

The loop starts once HA has started, so entities that are still loading neither get
commands nor count as mismatches (D-109).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .core.config import GlobalParams
from .core.engine import step
from .core.io import Event as CoreEvent
from .core.io import EventKind, Inputs, Outputs, OutputState, ZoneInput
from .core.state import CoreState
from .inputs import SensorReader, read_switch
from .outputs import OutputCommander
from .schema import FloorheatConfig
from .storage import FloorheatStore, Settings, StoredData

_LOGGER = logging.getLogger(__name__)

_MAX_SHADOW_RUNS = 3  # step, then re-run with the new commanded states until settled

_ALERT_KINDS = frozenset(
    {EventKind.SENSOR_FAULT_STARTED, EventKind.SENSOR_FAULT_REMINDER, EventKind.OUTPUT_MISMATCH}
)
MISSING_ENTITIES_NOTIFICATION = f"{DOMAIN}_missing_entities"


class FloorheatController:
    """Owns the logic state and the settings, and runs the reconcile loop."""

    def __init__(
        self,
        hass: HomeAssistant,
        config: FloorheatConfig,
        store: FloorheatStore,
        stored: StoredData,
    ) -> None:
        self.hass = hass
        self.config = config
        self._store = store
        self._state: CoreState = stored.core
        self._settings: Settings = stored.settings
        self._pending_off: set[str] = set(stored.pending_off)
        self._outputs: Outputs | None = None
        self._commander = OutputCommander(hass)
        self._sensors = SensorReader()
        # Commanded states: the core's feedback in shadow mode (D-66). The heat source
        # starts from the last known actual state (D-109), the valves OFF.
        self._commanded: dict[str, bool] = dict.fromkeys(config.switches, False)
        self._commanded[config.heat_source] = self._state.hp_actual_on is True
        self._lock = asyncio.Lock()
        self._run_queued = False
        self._started = False
        self._stopped = False
        self._unsubs: list[CALLBACK_TYPE] = []
        self._saved: dict[str, Any] | None = None
        self._listeners: list[Callable[[], None]] = []
        self._event_handlers: list[Callable[[CoreEvent], None]] = []

    # ------------------------------------------------------------ public view

    @property
    def state(self) -> CoreState:
        return self._state

    @property
    def settings(self) -> Settings:
        return self._settings

    @property
    def outputs(self) -> Outputs | None:
        """Desired outputs of the last run; None before the first run."""
        return self._outputs

    @property
    def pending_off(self) -> frozenset[str]:
        return frozenset(self._pending_off)

    @callback
    def async_add_listener(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Call `listener` after every run (entities, P6)."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def async_add_event_handler(self, handler: Callable[[CoreEvent], None]) -> CALLBACK_TYPE:
        """Call `handler` for every core event (notifications, P6)."""
        self._event_handlers.append(handler)
        return lambda: self._event_handlers.remove(handler)

    # ------------------------------------------------------------ settings (D-106)

    async def async_set_control_active(self, active: bool) -> None:
        if active == self._settings.control_active:
            return
        self._commander.reset()
        if active:
            self._pending_off.clear()
        else:  # D-69, D-110: final safe command set, then no commands
            self._pending_off = set(self.config.switches)
            self._commanded = dict.fromkeys(self.config.switches, False)
        _LOGGER.info("Control active %s", "ON" if active else "OFF (shadow mode)")
        await self._async_update_settings(control_active=active)

    async def async_set_heating_season(self, on: bool) -> None:
        await self._async_update_settings(heating_season=on)

    async def async_set_zone_params(self, zone_id: str, **changes: Any) -> None:
        """Change one zone's parameters; raises `ConfigError` for invalid values."""
        params = dataclasses.replace(self._settings.zone_params[zone_id], **changes)
        await self._async_update_settings(
            zone_params={**self._settings.zone_params, zone_id: params}
        )

    async def async_set_global_params(self, **changes: Any) -> None:
        """Change global parameters; raises `ConfigError` for invalid values."""
        params: GlobalParams = dataclasses.replace(self._settings.global_params, **changes)
        await self._async_update_settings(global_params=params)

    async def _async_update_settings(self, **changes: Any) -> None:
        self._settings = dataclasses.replace(self._settings, **changes)
        self._schedule_save()
        if self._started:
            await self.async_reconcile(tick=False)

    # ------------------------------------------------------------ lifecycle

    async def async_start(self) -> None:
        """Start the loop (called once HA has started)."""
        if self._stopped:
            return
        self._started = True
        self._check_entities()
        self._unsubs.append(
            async_track_state_change_event(
                self.hass, list(self.config.entity_ids), self._on_state_change
            )
        )
        self._unsubs.append(
            async_track_time_interval(
                self.hass,
                self._on_tick,
                self.config.reconcile_interval,
                name=f"{DOMAIN} reconcile",
                cancel_on_shutdown=True,
            )
        )
        await self.async_reconcile(tick=True)

    async def async_stop(self) -> None:
        """Stop the loop and write the state (on HA stop)."""
        if self._stopped:
            return
        self._stopped = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._commander.async_cancel()
        await self._store.async_save_now(self._data())

    def _check_entities(self) -> None:
        """Report mapped entities that HA does not know (D-107); they count as unavailable."""
        registry = er.async_get(self.hass)
        missing = [
            entity_id
            for entity_id in self.config.entity_ids
            if registry.async_get(entity_id) is None and self.hass.states.get(entity_id) is None
        ]
        if not missing:
            persistent_notification.async_dismiss(self.hass, MISSING_ENTITIES_NOTIFICATION)
            return
        listed = ", ".join(missing)
        _LOGGER.error(
            "Unknown entities in the floorheat configuration: %s. They count as "
            "unavailable (sensors: no reading; switches: OFF) until they appear",
            listed,
        )
        persistent_notification.async_create(
            self.hass,
            f"These entities in the floorheat configuration do not exist: {listed}. "
            "They count as unavailable until they appear. Check the entity ids.",
            title="floorheat: unknown entities",
            notification_id=MISSING_ENTITIES_NOTIFICATION,
        )

    @callback
    def _on_tick(self, _now: datetime) -> None:
        self.hass.async_create_task(self.async_reconcile(tick=True), f"{DOMAIN} reconcile tick")

    @callback
    def _on_state_change(self, _event: Event[EventStateChangedData]) -> None:
        if self._run_queued:
            return  # a run that has not started yet will read this state too
        self._run_queued = True
        self.hass.async_create_task(self._async_queued_run(), f"{DOMAIN} reconcile")

    async def _async_queued_run(self) -> None:
        async with self._lock:
            self._run_queued = False
            self._run(tick=False)

    # ------------------------------------------------------------ the run

    async def async_reconcile(self, *, tick: bool) -> None:
        """Run the loop once (`tick`: started by the ReconcileInterval timer)."""
        async with self._lock:
            self._run(tick=tick)

    def _run(self, *, tick: bool) -> None:
        if self._stopped or not self._started:
            return
        now = dt_util.utcnow()
        actual = {
            entity_id: read_switch(self.hass.states.get(entity_id))
            for entity_id in self.config.switches
        }
        control = self._settings.control_active
        for _ in range(_MAX_SHADOW_RUNS):
            feedback = actual if control else self._commanded_states()
            outputs = self._step(feedback, now, tick=tick)
            desired = self._desired(outputs)
            if control or desired == self._commanded:
                break
            self._commanded = desired
            tick = False  # the same `now` is one reconcile tick (D-99)
        if control:
            self._commander.apply(desired, actual, now)
        else:
            self._final_off(actual, now)
        for listener in list(self._listeners):
            listener()

    def _step(self, feedback: dict[str, OutputState], now: datetime, *, tick: bool) -> Outputs:
        inputs = self._inputs(feedback, tick=tick)
        outputs, self._state, events = step(self.config.core, self._state, inputs, now)
        self._outputs = outputs
        for event in events:
            self._log_event(event)
            for handler in list(self._event_handlers):
                handler(event)
        self._schedule_save()
        return outputs

    def _inputs(self, feedback: dict[str, OutputState], *, tick: bool) -> Inputs:
        zones: dict[str, ZoneInput] = {}
        for zone in self.config.zones:
            reading, reported = self._sensors.read(zone.sensor, self.hass.states.get(zone.sensor))
            zones[zone.id] = ZoneInput(
                reading=reading,
                last_reported=reported,
                valve=None if zone.valve is None else feedback[zone.valve],
            )
        settings = self._settings
        return Inputs(
            zones=zones,
            heat_source=feedback[self.config.heat_source],
            zone_params=dict(settings.zone_params),
            global_params=settings.global_params,
            heating_season=settings.heating_season,
            control_active=settings.control_active,
            time_zone=dt_util.get_default_time_zone(),  # follows HA's setting (D-96)
            reconcile_tick=tick,
        )

    def _commanded_states(self) -> dict[str, OutputState]:
        return {
            entity_id: OutputState.ON if on else OutputState.OFF
            for entity_id, on in self._commanded.items()
        }

    def _desired(self, outputs: Outputs) -> dict[str, bool]:
        desired = {self.config.heat_source: outputs.heat_source_on}
        for zone_id, entity_id in self.config.valves.items():
            desired[entity_id] = outputs.valves[zone_id]
        return desired

    def _final_off(self, actual: dict[str, OutputState], now: datetime) -> None:
        """Shadow mode: deliver the final OFF until each switch has reported OFF (D-110)."""
        confirmed = {e for e in self._pending_off if actual[e] is OutputState.OFF}
        if confirmed:
            self._pending_off -= confirmed
            self._schedule_save()
        self._commander.apply(dict.fromkeys(self._pending_off, False), actual, now)

    # ------------------------------------------------------------ events and storage

    def _log_event(self, event: CoreEvent) -> None:
        level = logging.WARNING if event.kind in _ALERT_KINDS else logging.INFO
        _LOGGER.log(level, "%s", event.message)

    def _data(self) -> dict[str, Any]:
        return {
            "core": self._state.to_dict(),
            "settings": self._settings.to_dict(),
            "pending_off": sorted(self._pending_off),
        }

    def _schedule_save(self) -> None:
        """Save when the data changed; the tick timestamp alone is not worth a write."""
        data = self._data()
        compared = {**data, "core": {**data["core"], "reconcile_tick_at": None}}
        if compared != self._saved:
            self._saved = compared
            self._store.schedule_save(data)
