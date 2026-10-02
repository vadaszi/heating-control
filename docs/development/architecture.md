# Architecture

## The four parts

```
 HA sensors / switches ──► adapter ──► step() ──► adapter ──► switch.turn_on/off
        ▲                    │  (pure control core)  │
        │                    └──────► entities, notifications, Store
        │
 Shelly relays ◄── heartbeat (HTTP) ── adapter          healthchecks.io ◄── ping
   (watchdog scripts act on their own if the heartbeat stops)
```

1. **Control core** (`custom_components/multizone_floor_heating_manager/core/`): pure Python with no Home Assistant imports. One deterministic function, `step(config, state, inputs, now) -> (outputs, new_state, events)`, makes every decision. It never reads the clock: `now` (an aware datetime) and HA's time zone come in. [Control core](control-core.md).
2. **Thin HA adapter** (the rest of the package): reads the YAML, collects the inputs from HA states, runs the reconcile loop, owns the settings and the stored data, provides the entities, services and notifications, and talks to the Shellys. Async only; it never blocks the event loop.
3. **Reconcile loop** (`controller.py`, `outputs.py`): calls `step` on a timer and on every relevant state change, compares desired and actual outputs, and corrects the differences with backoff. Idempotent. [Reconcile loop](reconcile-loop.md).
4. **Shelly watchdog scripts** (`shelly_scripts/`): JavaScript on the relays. They answer the heartbeat with a status and put the outputs into a safe state when the heartbeat stops. [Heartbeat and watchdogs](heartbeat.md).

Why it is built this way: the logic (calling zone, sync rule, minimum times, schedules, failure handling) is too intricate for automations, and it has to be testable with simulated time over days and weeks. A pure core makes every rule a fast unit test; the adapter stays small enough to test with Home Assistant's own test harness.

## Modules

**Core** (`core/`)

| Module | Responsibility |
|---|---|
| `config.py` | `ZoneConfig`, `CoreConfig` (wiring), `ZoneParams`, `GlobalParams` (UI values), `PARAM_SPECS` (every parameter's default, range and step), validation (`ConfigError`), startup warnings. |
| `io.py` | `Inputs`, `Outputs`, `ZoneReport`, the enums `Reason`, `HeatSourceStatus`, `Mode`, `OutputState`, and `Event` / `EventKind`. |
| `state.py` | `CoreState` / `ZoneState`, their (de)serialisation and the schema version. |
| `engine.py` | `step`: zone transitions, calling zone and sync rule, heat source protection, valves, reasons. |
| `schedule.py` | Auto and manual schedules, holiday, the target of each zone at a moment, the creation check, DST handling. |
| `failsafe.py` | The failsafe while HA runs and every sensor is dead. |
| `exercise.py` | The off-season valve exercise slots. |
| `alerts.py` | Notification events: sensor faults, reminders, output mismatch, failsafe, long run; `active_alerts`. |
| `heartbeat.py` | Evaluation of a Shelly status answer and the failure counting (the HTTP part is in the adapter). |
| `units.py` | °C / °F conversion helpers for the adapter. |

**Adapter**

| Module | Responsibility |
|---|---|
| `__init__.py` | Setup: YAML import into a single config entry, devices, platforms, removal of stale devices and entities, start and stop. |
| `config_flow.py` | Import only; the UI step points to the YAML. |
| `schema.py`, `const.py` | The YAML schema and defaults; `build_config` converts units and checks the wiring. |
| `controller.py` | The reconcile loop, the settings (parameters, season, Control active, schedules, holiday), events, liveness. |
| `inputs.py`, `outputs.py` | Reading sensors and switches into `Inputs`; sending commands with backoff. |
| `storage.py` | The `Store` file: core state, settings, pending final OFF, heartbeat alert state. |
| `heartbeat.py`, `watchdog.py` | The Shelly heartbeat client; the external watchdog ping. |
| `notifications.py` | Events → notify services or notify entities. |
| `services.py`, `schedules.py`, `form.py` | Schedule services, labels, the dashboard form. |
| `entity.py` + platforms | `climate`, `sensor`, `binary_sensor`, `number`, `switch`, `time`, `date`, `select`, `button`. |

## Rules that hold everywhere

- **The core is pure:** no HA imports, no clock, no I/O. `tests/core/test_core_purity.py` enforces it; mypy is strict for the core.
- **Units:** the core computes in °C. The adapter converts YAML values and readings into °C and shows values in HA's unit system. Temperature *differences* (hysteresis, resume difference) are converted by the adapter, because HA converts only absolute temperatures.
- **Generic:** any number of zones, zones without a valve, user-mapped entities, the heat source is just a switch. Nothing specific to one installation.
- **Actual state, not commanded state:** the heat source timers count from the switch's real transitions. An unavailable switch counts as OFF; when it reports again, the core decides whether it ever stopped (a relay that lost power restarts OFF).
- **Settings live in the adapter:** entities and services only show and change the controller's settings; they don't restore their own state.
- **YAML is the only configuration:** the config entry holds no data; the parsed YAML stays in memory, so no password is copied into HA's entry storage.
