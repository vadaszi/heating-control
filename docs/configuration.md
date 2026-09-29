# Configuration reference

Multizone Floor Heating Manager is set up in `configuration.yaml`. The YAML holds only the **wiring**: which sensor and which switches belong to which zone. Every value you change in daily use (set points, parameters, heating season, Control active) is changed from the HA UI and stored by HA ([design §5.6](design.md#56-configuration-d-52-d-55)).

For a first installation, see [Getting started](getting-started.md).

## Example

```yaml
multizone_floor_heating_manager:
  heat_source_switch: switch.heat_pump_request
  notify:
    - notify.mobile_app_phone
    - notify.email
  shellys:
    - name: Valves
      host: 192.0.2.11
      script_id: 1
      switches:
        - switch.valve_living_room
        - switch.valve_kitchen
    - name: Heat pump
      host: 192.0.2.12
      script_id: 1
      switches: [switch.heat_pump_request]
  zones:
    - id: living_room
      name: Living room
      sensor: sensor.living_room_temperature
      valve: switch.valve_living_room
      sensor_offset: -0.2
    - id: kitchen
      name: Kitchen
      sensor: sensor.kitchen_temperature
      valve: switch.valve_kitchen
    - id: bathroom
      name: Bathroom
      sensor: sensor.bathroom_temperature
      valve: none
```

The entity ids and addresses are examples: use your own.

> **Upgrading from 0.7 (the `floorheat` versions):** the integration was renamed (0.7.5). See [Upgrading from floorheat](#upgrading-from-floorheat) at the end of this page.

## Config entry and devices

The integration reads only the YAML. At startup it imports it into a single entry under *Settings → Devices & services* ("Multizone Floor Heating Manager"), so it can create devices:

- one device per zone, named "<zone name> floor heating" (e.g. "Living room floor heating");
- one device "Floor heating" for the global entities (heat request, heating season, Control active, parameters, alerts).

You can assign each zone device to its area in the device's settings; nothing in the YAML is needed for that. When you change the YAML and restart, the entry and devices follow: a new zone gets a device, a removed zone's device and entities are removed, a renamed zone's device gets the new name. *Add integration* in the UI only points to the YAML.

Without a `multizone_floor_heating_manager:` section the entry fails to load with an error saying so; nothing is deleted. Deleting the entry keeps the stored settings (targets, parameters, switches) in `.storage/multizone_floor_heating_manager`: with the YAML still in place, the entry comes back at the next restart with them.

## Keys

### Top level

| Key | Type | Default | Description |
|---|---|---|---|
| `heat_source_switch` | `switch` entity | required | The switch that requests heat from the heat source (e.g. a relay on the heat pump's thermostat terminals). |
| `zones` | list | required | At least one zone; see below. The order matters: on a tie, the zone listed first becomes the calling zone. |
| `plausible_min` | number | 0 °C | Readings below this are ignored as implausible. |
| `plausible_max` | number | 40 °C | Readings above this are ignored as implausible. |
| `reconcile_interval` | integer, seconds (10–300) | 60 | How often the outputs are checked and corrected. |
| `output_mismatch_alert` | integer ≥ 1 | 3 | Alert after this many reconcile intervals in which a switch does not follow its command or is unavailable. |
| `notify` | list of `notify.<name>` | none | Where notifications go: a notify service (e.g. `notify.mobile_app_phone` from the companion app, or an SMTP `notify.email`) or a notify entity. Without targets, events are only written to the log. |
| `shellys` | list | none | The Shellys running a watchdog script; see [Shelly watchdogs](#shelly-watchdogs). |
| `no_watchdog` | list of `switch` entities | none | Mapped switches that are **not** Shellys running the watchdog script (e.g. stand-ins or another relay). They get no heartbeat. |
| `heartbeat_interval` | integer, seconds (60–3600) | 300 | How often every Shelly gets a heartbeat. Must be shorter than `heartbeat_timeout`. |
| `heartbeat_fail_alert` | integer ≥ 1 | 3 | Alert after this many failed heartbeats in a row (3 × 5 min ≈ 15 min). |
| `heartbeat_timeout` | integer, seconds | 18000 (5 h) | The `heartbeat_timeout_s` you expect in the scripts' CONFIG block. The integration alerts if a script reports another value. |
| `heartbeat_check_interval` | integer, seconds | not checked | If set, the `check_interval_s` you expect in the scripts; otherwise it is not compared. |

### Per zone

| Key | Type | Default | Description |
|---|---|---|---|
| `id` | slug | required | Stable key of the zone: lowercase letters, digits and `_`, starting with a letter (e.g. `living_room`). The stored state and the entity ids are based on it. **Never change it** once the zone is in use. |
| `name` | string | required | Display name. Names must be unique (case and surrounding spaces are ignored). |
| `sensor` | `sensor` entity | required | The zone's temperature sensor. It must have a temperature unit (°C, °F or K). |
| `valve` | `switch` entity or `none` | required | The zone's valve actuator switch, or `none` for a zone without a valve. |
| `sensor_offset` | number | 0 | Calibration added to every reading (range ±5 °C). |

### Rules
- Temperatures in the YAML (`sensor_offset`, `plausible_min`, `plausible_max`) are in **your HA unit system** (°C or °F). A sensor reading is converted from the sensor's own unit.
- Each switch may be mapped only once: two zones cannot share a valve switch, and a valve switch cannot also be the heat source switch.
- **At least one flow path.** The integration assumes water can flow whenever the heat source request is ON: through a zone without a valve, a bypass or a buffer/hydraulic separator. If every zone has a valve, a warning is logged at startup. Make sure your installation has such a path.

### Shelly watchdogs

Each Shelly that switches valves or the heat source runs a watchdog script ([Shelly scripts](shelly-scripts.md)). It puts its outputs into a safe state if Home Assistant stops sending heartbeats. List every such Shelly under `shellys`:

| Key | Type | Default | Description |
|---|---|---|---|
| `name` | string | the `host` | Shown in notifications and the log. Names must be unique. |
| `host` | string | required | The Shelly's address as in its web UI URL, e.g. `192.0.2.11` or a host name, optionally with `:port`. No `http://`. |
| `script_id` | integer ≥ 1 | required | The id of the watchdog script on the device (the number in the web UI's script list). |
| `switches` | list of `switch` entities | required | The mapped switches (valves or the heat source) on this Shelly. |
| `password` | string | none | Only if authentication is on for the device; use `!secret`, e.g. `password: !secret floor_heating_shelly_password`. The user name is always `admin`. |

Rules:
- **Every mapped switch** (the heat source and every valve) is either on exactly one Shelly under `shellys` or listed in `no_watchdog`. Anything else is a configuration error.
- The Shelly with the heat source switch runs the **heat source script** and must hold no valve; every other listed Shelly runs the **valve script**. The integration checks this with each answer.
- The integration does not detect device types: it trusts this list.

> ⚠️ **A switch in `no_watchdog` has no device failsafe.** If Home Assistant stops, it stays as it was, e.g. a heat source request ON, until someone switches it. Only the external watchdog (v1.2) would tell you that HA is down. Use `no_watchdog` only for stand-ins during a trial or for relays that cannot run the script.

## Checks at startup

- A structural error (missing key, wrong entity domain, duplicate zone id or name, invalid id, value out of range, unknown key) stops the setup. HA shows "Invalid config" and the log names the problem. For an invalid id, a valid one is suggested.
- An entity that HA does not know (neither registered nor reporting a state) is logged as an error and shown as a persistent notification once HA has started. The integration still runs:
  - an unknown sensor gives no reading, and the zone goes into sensor fault after the timeout;
  - an unknown switch counts as unavailable, which is OFF.
- A sensor without a temperature unit is logged once as a warning; its readings are ignored.
- A `notify` target that is neither a notify service nor a notify entity is logged as a warning and shown as a persistent notification once HA has started.
- A mapped switch that is neither on a listed Shelly nor in `no_watchdog`, a switch listed twice, a heat source Shelly holding a valve, or a `heartbeat_interval` not shorter than `heartbeat_timeout` stops the setup.

## How it runs

- **Reconcile loop.** Every `reconcile_interval`, and at once whenever a mapped sensor or switch changes state, the integration computes the desired outputs from the current states and switches every output that differs. It starts only when HA has finished starting, so entities that are still loading neither get commands nor count as failures.
- **Retries.** A switch that does not follow gets its command again after 1, 2, 4 and 8 minutes, then every 15 minutes. Nothing is sent to an unavailable switch; when it returns, it is corrected at once. After `output_mismatch_alert` intervals the alert "output not following command" is notified.
- **Restart.** The logic state (timers, calling zone, sensor faults, heat source ON/OFF times) and the UI settings are stored in HA's `.storage/multizone_floor_heating_manager` file, at most every 30 seconds and when HA stops. After a restart the switch states are read back and control continues where it stopped.
- **Unavailable heat source switch.** It counts as OFF while it is unavailable. If it comes back ON, the heat pump never stopped (a device that lost power restarts OFF), so it is not switched OFF because of a Wi-Fi glitch.
- **Heartbeat.** Every `heartbeat_interval` each listed Shelly gets a heartbeat (`POST http://<host>/script/<script_id>/heartbeat`, [protocol](heartbeat-protocol.md)), also in shadow mode. The heat source Shelly's heartbeat carries the heating season switch; it gets one at once when you switch the season. Each answer is the script's status:
  - after `heartbeat_fail_alert` failed heartbeats in a row, "Shelly … not answering" is notified once, naming the cause: unreachable, script not running (HTTP 404: stopped, or wrong `script_id`), authentication failed, or an unexpected answer (wrong script, unsupported protocol version). When it answers again, the recovery is notified. A single failed call (a Wi-Fi hiccup) never alerts;
  - if the script's `heartbeat_timeout_s` (or `check_interval_s`, if you set `heartbeat_check_interval`) differs from the expected value, that is notified once; it clears silently when the values match again;
  - only logged, never notified: a watchdog that had timed out (seen in the status read on the first call after HA starts and after a failed call) and a Shelly that restarted.
- **Heartbeat only while the integration works.** Heartbeats go out only if the reconcile loop has completed a run within the last 3 reconcile intervals. If the integration is broken (e.g. after an HA update), the Shellys stop getting heartbeats and act after their timeout, as if HA had stopped.

## Shadow mode

After the first installation **Control active is OFF** (shadow mode). The integration then reads everything and makes its decisions, but it sends **no** switch commands. It treats its own decisions as if the switches had followed, so the decisions stay consistent. Use it to compare the integration with your existing controller before going live.

- **Switching Control active OFF** (live → shadow) sends one final safe set: heat source OFF, all valves OFF. A switch that is unavailable at that moment, or does not follow, gets the OFF again (with the retries above) until it has reported OFF once, also across a restart. After that, nothing more is sent.
- **Switching Control active ON** (shadow → live) sets every output to the desired state at the next run.
- **Going live after shadow mode:** from then on the real switch states count. If shadow mode believed the heat source was running, the real switch reads OFF. That counts as a stop, so the minimum OFF time (default 60 min) runs before the heat source is first requested. The zones that need heat already open their valves.

## Notifications

Every notification goes to every `notify` target, with a title and a message:

| Title | When |
|---|---|
| Floor heating: sensor fault | A zone has had no valid reading for longer than the sensor fault timeout. Also in shadow mode and outside the heating season. |
| Floor heating: sensor fault reminder | Once a day at the reminder time (default 08:00), listing every zone faulty since an earlier day. Only in the heating season. |
| Floor heating: sensor recovered | The zone's sensor reports again. |
| Floor heating: switch not following command | A switch has differed from its command, or been unavailable, for `output_mismatch_alert` reconcile intervals. Not in shadow mode. |
| Floor heating: switch following again | That switch follows again. |
| Floor heating: Shelly watchdog not answering | `heartbeat_fail_alert` heartbeats in a row to a Shelly failed; the message names the cause. Also in shadow mode. |
| Floor heating: Shelly watchdog answering again | That Shelly answers again. |
| Floor heating: Shelly script parameters differ | A script reports a `heartbeat_timeout_s` (or `check_interval_s`) other than expected. Once, until the values match again. |

A target that is a notify service (the companion app, SMTP) is called as `notify.<name>`; otherwise the notify entity of that id gets `notify.send_message`. A failing target is logged and never stops the control.

## Entities

The entities follow Home Assistant's naming conventions. Each belongs to a zone device ("<zone name> floor heating") or to the "Floor heating" device, and its name names only the value (e.g. "Reason"). Home Assistant generates each entity id from the device name and the entity name when the entity is first created, e.g. `sensor.living_room_floor_heating_reason`. After that the id is kept, also when you rename the zone; you can change it yourself in the entity settings. The ids below are the generated ones, for a zone named "Living room" (`<zone>` = `living_room`). Values changed through these entities are stored by the integration and survive restarts.

Settings (the parameter numbers and the reminder time) have the *configuration* category: HA shows them under "Configuration" on the device page and leaves them out of automatically generated dashboards.

### Per zone (device "<zone name> floor heating")

| Entity (name) | Shows / changes |
|---|---|
| `climate.<zone>_floor_heating` (named like the device) | Current temperature = the zone temperature (reading + offset); target = the zone's base set point (10–30 °C, step 0.1). Mode `heat` only. `hvac_action` is *heating* while the heat source request is ON and the zone gets flow (valve open, or no valve), otherwise *idle*. Attributes: `zone_state`, `reason` (the keys below), `valve` (desired state; none without a valve), `calling_zone`. |
| `sensor.<zone>_floor_heating_state` (State) | `idle` (Idle), `waiting` (Waiting), `heating` (Heating), `forced` (Forced, v1.1), `sensor_fault` (Sensor fault). |
| `sensor.<zone>_floor_heating_reason` (Reason) | Why the zone is in its state; the table below. The state is a fixed key, shown as its text; it never counts down, so the state changes only when the reason does. While a timer runs (`waiting`, `held_by_minimum_off_time`, `spreading_heat`), the attribute `until` holds its end time; otherwise there is no `until` attribute. |
| `sensor.<zone>_floor_heating_effective_target_temperature` (Effective target temperature) | The set point in force (the base set point until schedules and holiday arrive in v1.1). |
| `number.<zone>_floor_heating_hysteresis` (Hysteresis) | 0.1–1.0 °C (default 0.2). StartTemp = set point − hysteresis, StopTemp = set point + hysteresis. |
| `number.<zone>_floor_heating_wait_time` (Wait time) | 0–120 min (default 30). Open-window filter before the zone may start the heat source. |

### Reasons

| Key | Shown as | When |
|---|---|---|
| `idle` | Idle | Warm enough; nothing to do. |
| `waiting` | Waiting period | Too cold while the heat source is off; the wait time runs (`until`). If it is still too cold then, the zone starts heating. |
| `calling_zone` | Calling zone | This zone started the current heat source run. When it reaches its target, the other zones below their stop temperature join. |
| `heating` | Heating | Heating in a run another zone started. |
| `held_by_minimum_off_time` | Waiting for heat source minimum off time | Wants heat, valve open, but the heat source stopped less than its minimum off time ago (`until`: when it may start). |
| `spreading_heat` | Spreading heat (minimum on time) | Every zone is satisfied, but the heat source must run until its minimum on time; the valves open to spread the heat (`until`). |
| `too_warm_for_spreading` | Idle, too warm to take spread heat | During the spread, the zone is at or above the manual max temperature and stays closed. |
| `heat_source_unavailable` | Heating, heat source switch unavailable | Wants heat, but the heat source switch doesn't report. |
| `no_reading_yet` | Waiting for a sensor reading | No valid reading since the start; after the sensor fault timeout it becomes a sensor fault. |
| `sensor_fault` | Sensor fault, valve follows the heat source | No valid reading; the valve is open while the heat source runs; no demand. |
| `season_off` | Heating season off | The heating season switch is off. |
| `sensor_fault_season_off` | Sensor fault (heating season off) | Both. |

### Global (device "Floor heating")

| Entity (name) | Shows / changes |
|---|---|
| `binary_sensor.floor_heating_heat_request` (Heat request) | The heat source request the integration wants (in shadow mode: the simulated one). While the heat source runs: attributes `on_since` and `on_duration` (minutes; not kept in the history). Both are left out while it is not running. |
| `sensor.floor_heating_mode` (Mode) | `normal` (`holiday` from v1.1, `failsafe` from v1.2). Attribute `shadow`: true while Control active is OFF. |
| `sensor.floor_heating_alerts` (Alerts) | Number of active alerts; attribute `alerts` lists them (`kind`, `zone_id`, `message`): sensor faults, switches not following, Shellys not answering (`watchdog_failed`), Shelly script parameters differing (`watchdog_params_mismatch`). |
| `switch.floor_heating_heating_season` (Heating season) | Heating season (default ON). OFF: no heating demand, heat source OFF and valves closed at once. |
| `switch.floor_heating_control_active` (Control active) | OFF = shadow mode (default after the first installation). See [Shadow mode](#shadow-mode). |
| `time.floor_heating_sensor_fault_reminder_time` (Sensor fault reminder time) | Time of the daily sensor fault reminder (default 08:00). Configuration category. |

### Global parameters (device "Floor heating", configuration category)

| Entity (name) | Range (default) | Used from |
|---|---|---|
| `number.floor_heating_heat_source_minimum_on_time` (Heat source minimum on time) | 30–180 min (60) | v1 |
| `number.floor_heating_heat_source_minimum_off_time` (Heat source minimum off time) | 30–180 min (60) | v1 |
| `number.floor_heating_sensor_fault_timeout` (Sensor fault timeout) | 15–240 min (60) | v1 |
| `number.floor_heating_manual_max_temperature` (Manual max temperature) | 18–30 °C (25) | v1 (heat spread limit); manual schedules from v1.1 |
| `number.floor_heating_manual_resume_difference` (Manual resume difference) | 0.2–3.0 °C (1.0) | v1.1 (manual schedules) |
| `number.floor_heating_holiday_temperature` (Holiday temperature) | 10–25 °C (18) | v1.1 (holiday) |
| `number.floor_heating_failsafe_delay` (Failsafe delay) | 1–72 h (24) | v1.2 (failsafe) |
| `number.floor_heating_off_season_valve_exercise_duration` (Off-season valve exercise duration) | 5–30 min (15) | v1.2 (valve exercise) |
| `number.floor_heating_long_run_alarm` (Long run alarm) | 2–48 h (12) | v1.2 (long run alarm) |

The minimum on/off times can never be set below 30 minutes: they protect the heat pump from short cycles. Temperatures are shown in your HA unit system.

## Upgrading from floorheat

Up to 0.7.0 the integration was called `floorheat`. From 0.7.5 on it is **Multizone Floor Heating Manager** with the domain `multizone_floor_heating_manager`. Home Assistant sees it as a new integration:

1. Rename the top-level key in `configuration.yaml` from `floorheat:` to `multizone_floor_heating_manager:`. Nothing else in the YAML changes.
2. Update through HACS (or copy the new folder `custom_components/multizone_floor_heating_manager`), then **delete the old folder** `custom_components/floorheat`.
3. Restart HA. The new entry, devices and entities appear.
4. Set your targets and parameters again: they start from the defaults (they were stored under the old name). Control active starts OFF (shadow mode).
5. Assign the zone devices to their areas, and update dashboards, automations and template sensors to the new entity ids (tables above).
6. The old `floorheat` entities are left as orphans: delete them in *Settings → Entities* (search "floorheat", select, *Delete*). You may also delete the file `.storage/floorheat`.
