# Multizone Floor Heating Manager

A Home Assistant custom integration for room-by-room control of underfloor heating fed by a heat pump (or any heat source that can be requested through a switch).

> **Status: under development.** There is no release yet; the first one will be 1.0.0. The work is done in phases; see [`docs/implementation-plan.md`](docs/implementation-plan.md). The integration can already be installed for a trial in shadow mode (it decides but switches nothing): see [Getting started](docs/getting-started.md).

## Planned features (v1)
- Per-zone thermostat logic with hysteresis and an open-window wait time.
- Long heat pump cycles: minimum ON/OFF times and a "calling zone" + sync rule so zones finish a cycle together.
- Defined behaviour on sensor faults, with notifications.
- Shadow mode to run next to an existing controller before going live.
- Shelly watchdog scripts that put the valves and heat source into a safe state if Home Assistant stops.

v1.1 adds auto and manual schedules, holiday mode and an example dashboard. v1.2 adds a failsafe for the case that every sensor stops reporting (heating in a daily window), a weekly off-season valve exercise and a long run alarm; the heat source script's own failsafe window and an external watchdog follow.

## Documentation
- Design and functional specification: [`docs/design.md`](docs/design.md)
- Getting started (install, stand-in switches, shadow-mode trial): [`docs/getting-started.md`](docs/getting-started.md)
- Configuration reference (YAML keys, devices and entities, schedules and holiday, services, notifications, shadow mode, upgrading from `floorheat`): [`docs/configuration.md`](docs/configuration.md)
- Example dashboard: [`docs/dashboard.md`](docs/dashboard.md)
- Shelly watchdog scripts (which device, upload, configure, bench tests): [`docs/shelly-scripts.md`](docs/shelly-scripts.md)
- Heartbeat protocol between the integration and the scripts: [`docs/heartbeat-protocol.md`](docs/heartbeat-protocol.md)
- Installation and safety notes will be added before the first release (1.0.0).

## License
[MIT](LICENSE)
