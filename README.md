# Floor Heating Zone Control (`floorheat`)

A Home Assistant custom integration for room-by-room control of underfloor heating fed by a heat pump (or any heat source that can be requested through a switch).

> **Status: under development.** The first release (v1) is being built in phases; see [`docs/implementation-plan.md`](docs/implementation-plan.md). The integration can already be installed for a trial in shadow mode (it decides but switches nothing): see [Getting started](docs/getting-started.md).

## Planned features (v1)
- Per-zone thermostat logic with hysteresis and an open-window wait time.
- Long heat pump cycles: minimum ON/OFF times and a "calling zone" + sync rule so zones finish a cycle together.
- Defined behaviour on sensor faults, with notifications.
- Shadow mode to run next to an existing controller before going live.
- Shelly watchdog scripts that put the valves and heat source into a safe state if Home Assistant stops.

Later releases add schedules, holiday mode, a dashboard example (v1.1), and a 24 h failsafe, valve exercise and external watchdog (v1.2).

## Documentation
- Design and functional specification: [`docs/design.md`](docs/design.md)
- Getting started (install, stand-in switches, shadow-mode trial): [`docs/getting-started.md`](docs/getting-started.md)
- Configuration reference (YAML keys, entities, notifications, shadow mode): [`docs/configuration.md`](docs/configuration.md)
- Shelly watchdog scripts (which device, upload, configure, bench tests): [`docs/shelly-scripts.md`](docs/shelly-scripts.md)
- Heartbeat protocol between the integration and the scripts: [`docs/heartbeat-protocol.md`](docs/heartbeat-protocol.md)
- Installation and safety notes will be added with the v1 release.

## License
[MIT](LICENSE)
