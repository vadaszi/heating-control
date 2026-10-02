# Multizone Floor Heating Manager

[![GitHub Release](https://img.shields.io/github/v/release/vadaszi/multizone-floor-heating-manager)](https://github.com/vadaszi/multizone-floor-heating-manager/releases)
[![License](https://img.shields.io/github/license/vadaszi/multizone-floor-heating-manager)](LICENSE)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![CI](https://img.shields.io/github/actions/workflow/status/vadaszi/multizone-floor-heating-manager/ci.yml?branch=main&label=tests)](https://github.com/vadaszi/multizone-floor-heating-manager/actions)

A Home Assistant integration that controls **underfloor heating room by room**, fed by a heat pump or any other heat source that can be asked for heat through a switch.

Each zone has a temperature sensor and (usually) a valve actuator on a relay. The integration opens the valves of the zones that need heat and requests heat from the heat source, while protecting the heat pump from short cycles. It is built for the slow, heavy nature of floor heating: long heat source runs, an open-window filter, and zones that finish a run together instead of calling for heat one after another.

## Features

- **Per-zone thermostat** with hysteresis and an open-window wait time.
- **Heat pump protection:** minimum on and off times; a "calling zone" and a sync rule so all zones are topped up at the end of a run; leftover heat is spread over the house instead of switching off early.
- **Defined behaviour when things fail:** sensor faults (the zone keeps getting heat with the others), an unavailable heat source switch (a Wi-Fi glitch never switches a working heat pump off), relays that don't follow their command, a failsafe timetable when every sensor is dead.
- **Shelly watchdog scripts** (optional): if Home Assistant stops, the Shelly relays put the house into a safe state on their own and, after a day, heat on a daily timetable.
- **Schedules:** auto schedules (a different target for a time window) and manual schedules (force a zone to heat), one-shot or recurring; **holiday mode** with a temperature per zone.
- **Shadow mode:** run it next to your current controller; it decides but switches nothing.
- **Notifications** to your phone or email; an **external watchdog** ping (e.g. healthchecks.io) that tells you when Home Assistant is down.
- Off-season **valve exercise**, a **long run alarm**, an example **dashboard** that explains every decision.
- Any number of zones, zones without a valve, any relays, °C or °F.

## What you need

- Home Assistant **2026.9** or newer.
- A temperature sensor per zone (any `sensor` with a temperature unit).
- A relay per valve actuator, and a relay for the heat source request (e.g. on the heat pump's room thermostat terminals). Any relay Home Assistant can switch; Shelly Gen2+ relays can additionally run the watchdog scripts.
- **A flow path whenever the heat source runs:** a zone without a valve, a bypass, or a buffer tank / hydraulic separator. The integration does not wait for valves to open before it requests heat.

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=vadaszi&repository=multizone-floor-heating-manager&category=integration)

Or in HACS: ⋮ → *Custom repositories* → add `https://github.com/vadaszi/multizone-floor-heating-manager` as *Integration*, then download "Multizone Floor Heating Manager". Manual installation: copy `custom_components/multizone_floor_heating_manager` into your `custom_components` folder.

The integration is configured in `configuration.yaml`; daily settings (targets, parameters, schedules, holiday) are changed in the UI. Follow **[Getting started](docs/getting-started.md)**.

## Safety

This integration switches a heating system. Please keep in mind:

- Run it in **shadow mode** first and check its decisions before you hand over control.
- Set your relays' **power-on state to OFF**, and remove any timers or schedules on the relays themselves.
- Without the Shelly watchdog scripts, a relay stays as it is when Home Assistant stops. Set up the **external watchdog** so you learn about it.
- The integration requests heat; it does not control flow temperatures, pumps or the heat source's own protection. Those stay with the heat source and the installer.
- Use it at your own risk ([MIT license](LICENSE)).

## Limitations

- On/off control with hysteresis: no PID, no weather compensation, no learning, no automatic preheat.
- One heat source switch for all zones.
- It sees only whether the heat source *request* is on, not whether the compressor or burner really runs.
- YAML configuration (no setup in the UI).

## Documentation

- [Getting started](docs/getting-started.md)
- [How it works](docs/how-it-works.md)
- [Configuration reference](docs/configuration.md)
- [Example dashboard](docs/dashboard.md)
- [Shelly watchdog scripts](docs/shelly-scripts.md) and their [heartbeat protocol](docs/heartbeat-protocol.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Changelog](CHANGELOG.md)
- For developers: [developer documentation](docs/development/index.md), [contributing](CONTRIBUTING.md)

## License

[MIT](LICENSE)
