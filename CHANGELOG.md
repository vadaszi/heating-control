# Changelog

All notable changes to Multizone Floor Heating Manager. Each release lists the entities that were added, renamed or removed, so you know which dashboards and automations to check after an update ([Updating](docs/configuration.md#updating)).

## 1.0.0 (not released yet)

First public release.

**Zone control**
- Per-zone thermostat with hysteresis and an open-window wait time; any number of zones, zones without a valve.
- Calling zone and sync rule: the zone that starts a run gets the heat first, the others are topped up at its end.
- Heat source protection: minimum on and off time (30–180 min), heat spread over the house during the minimum on time, demand held with the valve open during the minimum off time.
- Heating season switch; shadow mode (Control active OFF) that decides but switches nothing.

**Schedules and holiday**
- Auto schedules (a target for a time window) and manual schedules (force a zone to heat, with a temperature cap), one-shot or recurring, across midnight and daylight saving time.
- Schedule services (`add_schedule`, `delete_schedule`, `list_schedules`) and a schedule form for the dashboard.
- Holiday mode with a temperature per zone and an optional end date and time.

**Failures and safety**
- Sensor faults: plausibility range, timeout, the zone follows the house; notifications and a daily reminder.
- A heat source switch that becomes unavailable never switches a working heat pump off.
- Relays that don't follow their command are retried with backoff and alerted.
- Failsafe while Home Assistant runs but every sensor is dead: heating on a daily timetable.
- Shelly watchdog scripts (valve script 1.0.0, heat source script 1.1.0): safe state after 5 h without Home Assistant; the heat source script heats on a daily timetable after 24 h (uptime cycle without a clock). Heartbeat with status, alerts when a Shelly or its script stops answering.
- External watchdog ping (e.g. healthchecks.io).
- Long run alarm; off-season valve exercise.
- The integration removes entities that it no longer provides when it starts.

**Entities and dashboard**
- A device per zone and a "Floor heating" device; climate, state, reason, effective target and parameter entities per zone; heat request, heat source status, mode, alerts, schedules and the global parameters.
- Example dashboard with built-in cards, a 24-hour graph per zone and a card that explains every state and reason.
