# Configuration reference

floorheat is set up in `configuration.yaml`. The YAML holds only the **wiring**: which sensor and which switches belong to which zone. Every value you change in daily use (set points, parameters, heating season, Control active) is changed from the HA UI and stored by HA ([design §5.6](design.md#56-configuration-d-52-d-55)).

For a first installation, see [Getting started](getting-started.md).

## Example

```yaml
floorheat:
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

> **Upgrading from 0.6:** from 0.7 on, every switch must be either on a Shelly listed under `shellys` or listed in `no_watchdog` (see [Shelly watchdogs](#shelly-watchdogs)). Add these keys to your YAML **before** you update, otherwise the setup fails with "Invalid config" and floorheat does not run.

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
| `shellys` | list | none | The Shellys running a floorheat watchdog script; see [Shelly watchdogs](#shelly-watchdogs). |
| `no_watchdog` | list of `switch` entities | none | Mapped switches that are **not** Shellys running the watchdog script (e.g. stand-ins or another relay). They get no heartbeat. |
| `heartbeat_interval` | integer, seconds (60–3600) | 300 | How often every Shelly gets a heartbeat. Must be shorter than `heartbeat_timeout`. |
| `heartbeat_fail_alert` | integer ≥ 1 | 3 | Alert after this many failed heartbeats in a row (3 × 5 min ≈ 15 min). |
| `heartbeat_timeout` | integer, seconds | 18000 (5 h) | The `heartbeat_timeout_s` you expect in the scripts' CONFIG block. floorheat alerts if a script reports another value. |
| `heartbeat_check_interval` | integer, seconds | not checked | If set, the `check_interval_s` you expect in the scripts; otherwise it is not compared. |

### Per zone

| Key | Type | Default | Description |
|---|---|---|---|
| `id` | slug | required | Stable key of the zone: lowercase letters, digits and `_`, starting with a letter (e.g. `living_room`). The stored state and the entity ids are based on it. **Never change it** once the zone is in use. |
| `name` | string | required | Display name. Names must be unique (case and surrounding spaces are ignored). |
| `sensor` | `sensor` entity | required | The zone's temperature sensor. It must have a temperature unit (°C, °F or K). |
| `valve` | `switch` entity or `none` | required | The zone's valve actuator switch, or `none` for a zone without a valve. |
| `power_sensor` | `sensor` entity | – | Power measurement of the valve channel. Accepted now; used by the actuator fault check in a later release. |
| `sensor_offset` | number | 0 | Calibration added to every reading (range ±5 °C). |

### Rules
- Temperatures in the YAML (`sensor_offset`, `plausible_min`, `plausible_max`) are in **your HA unit system** (°C or °F). A sensor reading is converted from the sensor's own unit.
- Each switch may be mapped only once: two zones cannot share a valve switch, and a valve switch cannot also be the heat source switch.
- **At least one flow path.** floorheat assumes water can flow whenever the heat source request is ON: through a zone without a valve, a bypass or a buffer/hydraulic separator. If every zone has a valve, a warning is logged at startup. Make sure your installation has such a path.

### Shelly watchdogs

Each Shelly that switches valves or the heat source runs a watchdog script ([Shelly scripts](shelly-scripts.md)). It puts its outputs into a safe state if Home Assistant stops sending heartbeats. List every such Shelly under `shellys`:

| Key | Type | Default | Description |
|---|---|---|---|
| `name` | string | the `host` | Shown in notifications and the log. Names must be unique. |
| `host` | string | required | The Shelly's address as in its web UI URL, e.g. `192.0.2.11` or a host name, optionally with `:port`. No `http://`. |
| `script_id` | integer ≥ 1 | required | The id of the watchdog script on the device (the number in the web UI's script list). |
| `switches` | list of `switch` entities | required | The mapped switches (valves or the heat source) on this Shelly. |
| `password` | string | none | Only if authentication is on for the device; use `!secret`, e.g. `password: !secret floorheat_shelly_password`. The user name is always `admin`. |

Rules:
- **Every mapped switch** (the heat source and every valve) is either on exactly one Shelly under `shellys` or listed in `no_watchdog`. Anything else is a configuration error.
- The Shelly with the heat source switch runs the **heat source script** and must hold no valve; every other listed Shelly runs the **valve script**. floorheat checks this with each answer.
- floorheat does not detect device types: it trusts this list.

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

- **Reconcile loop.** Every `reconcile_interval`, and at once whenever a mapped sensor or switch changes state, floorheat computes the desired outputs from the current states and switches every output that differs. It starts only when HA has finished starting, so entities that are still loading neither get commands nor count as failures.
- **Retries.** A switch that does not follow gets its command again after 1, 2, 4 and 8 minutes, then every 15 minutes. Nothing is sent to an unavailable switch; when it returns, it is corrected at once. After `output_mismatch_alert` intervals the alert "output not following command" is notified.
- **Restart.** The logic state (timers, calling zone, sensor faults, heat pump ON/OFF times) and the UI settings are stored in HA's `.storage/floorheat` file, at most every 30 seconds and when HA stops. After a restart the switch states are read back and control continues where it stopped.
- **Unavailable heat source switch.** It counts as OFF while it is unavailable. If it comes back ON, the heat pump never stopped (a device that lost power restarts OFF), so it is not switched OFF because of a Wi-Fi glitch.
- **Heartbeat.** Every `heartbeat_interval` each listed Shelly gets a heartbeat (`POST http://<host>/script/<script_id>/heartbeat`, [protocol](heartbeat-protocol.md)), also in shadow mode. The heat source Shelly's heartbeat carries the heating season switch; it gets one at once when you switch the season. Each answer is the script's status:
  - after `heartbeat_fail_alert` failed heartbeats in a row, "Shelly … not answering" is notified once, naming the cause: unreachable, script not running (HTTP 404: stopped, or wrong `script_id`), authentication failed, or an unexpected answer (wrong script, unsupported protocol version). When it answers again, the recovery is notified. A single failed call (a Wi-Fi hiccup) never alerts;
  - if the script's `heartbeat_timeout_s` (or `check_interval_s`, if you set `heartbeat_check_interval`) differs from the expected value, that is notified once; it clears silently when the values match again;
  - only logged, never notified: a watchdog that had timed out (seen in the status read on the first call after HA starts and after a failed call) and a Shelly that restarted.
- **Heartbeat only while floorheat works.** Heartbeats go out only if the reconcile loop has completed a run within the last 3 reconcile intervals. If floorheat is broken (e.g. after an HA update), the Shellys stop getting heartbeats and act after their timeout, as if HA had stopped.

## Shadow mode

After the first installation **Control active is OFF** (shadow mode). floorheat then reads everything and makes its decisions, but it sends **no** switch commands. It treats its own decisions as if the switches had followed, so the decisions stay consistent. Use it to compare floorheat with your existing controller before going live.

- **Switching Control active OFF** (live → shadow) sends one final safe set: heat source OFF, all valves OFF. A switch that is unavailable at that moment, or does not follow, gets the OFF again (with the retries above) until it has reported OFF once, also across a restart. After that, nothing more is sent.
- **Switching Control active ON** (shadow → live) sets every output to the desired state at the next run.
- **Going live after shadow mode:** from then on the real switch states count. If shadow mode believed the heat source was running, the real switch reads OFF. That counts as a stop, so the minimum OFF time (default 60 min) runs before the heat source is first requested. The zones that need heat already open their valves.

## Notifications

Every notification goes to every `notify` target, with a title and a message:

| Title | When |
|---|---|
| floorheat: sensor fault | A zone has had no valid reading for longer than the sensor fault timeout. Also in shadow mode and outside the heating season. |
| floorheat: sensor fault reminder | Once a day at the reminder time (default 08:00), listing every zone faulty since an earlier day. Only in the heating season. |
| floorheat: sensor recovered | The zone's sensor reports again. |
| floorheat: output not following command | A switch has differed from its command, or been unavailable, for `output_mismatch_alert` reconcile intervals. Not in shadow mode. |
| floorheat: output recovered | That switch follows again. |
| floorheat: Shelly watchdog not answering | `heartbeat_fail_alert` heartbeats in a row to a Shelly failed; the message names the cause. Also in shadow mode. |
| floorheat: Shelly watchdog answering again | That Shelly answers again. |
| floorheat: Shelly script parameters differ | A script reports a `heartbeat_timeout_s` (or `check_interval_s`) other than expected. Once, until the values match again. |

A target that is a notify service (the companion app, SMTP) is called as `notify.<name>`; otherwise the notify entity of that id gets `notify.send_message`. A failing target is logged and never stops the control.

## Entities

The entity ids are fixed and built from the zone `id`, so they never change when you rename a zone. The display names use the zone `name`. Values changed through these entities are stored by floorheat and survive restarts.

### Per zone (`<zone>` = the zone id)

| Entity | Shows / changes |
|---|---|
| `climate.floorheat_<zone>` | Current temperature = the zone temperature (reading + offset); target = the zone's base set point (10–30 °C, step 0.1). Mode `heat` only. `hvac_action` is *heating* while the heat source request is ON and the zone gets flow (valve open, or no valve), otherwise *idle*. Attributes: `zone_state`, `reason`, `valve` (desired state; none without a valve), `calling_zone`. |
| `sensor.floorheat_<zone>_state` | `idle`, `waiting`, `heating`, `forced` (v1.1), `sensor_fault`. |
| `sensor.floorheat_<zone>_reason` | Why, as a fixed key, e.g. `calling_zone`, `waiting`, `held_by_minimum_off_time`. It never counts down, so the state changes only when the reason does. While a timer runs ("Waiting", "Held by min OFF", "Spreading heat (min ON)"), the attribute `until` holds its end time; otherwise there is no `until` attribute. |
| `sensor.floorheat_<zone>_setpoint` | Effective set point (the base set point until schedules and holiday arrive in v1.1). |
| `number.floorheat_<zone>_hysteresis` | 0.1–1.0 °C (default 0.2). StartTemp = set point − hysteresis, StopTemp = set point + hysteresis. |
| `number.floorheat_<zone>_wait_time` | 0–120 min (default 30). Open-window filter before the zone may start the heat source. |

### Global

| Entity | Shows / changes |
|---|---|
| `binary_sensor.floorheat_heat_request` | The heat source request floorheat wants (in shadow mode: the simulated one). While the heat source runs: attributes `on_since` and `on_duration` (minutes; not kept in the history). Both are left out while it is not running. |
| `sensor.floorheat_mode` | `normal` (`holiday` from v1.1, `failsafe` from v1.2). Attribute `shadow`: true while Control active is OFF. |
| `sensor.floorheat_alerts` | Number of active alerts; attribute `alerts` lists them (`kind`, `zone_id`, `message`): sensor faults, outputs not following, Shellys not answering (`watchdog_failed`), Shelly script parameters differing (`watchdog_params_mismatch`). |
| `switch.floorheat_heating_season` | Heating season (default ON). OFF: no heating demand, heat source OFF and valves closed at once. |
| `switch.floorheat_control_active` | OFF = shadow mode (default after the first installation). See [Shadow mode](#shadow-mode). |
| `time.floorheat_sensor_fault_reminder` | Time of the daily sensor fault reminder (default 08:00). |

### Global parameters (`number.floorheat_<parameter>`)

| Entity | Range (default) | Used from |
|---|---|---|
| `number.floorheat_hp_min_on_time` | 30–180 min (60) | v1 |
| `number.floorheat_hp_min_off_time` | 30–180 min (60) | v1 |
| `number.floorheat_sensor_fault_timeout` | 15–240 min (60) | v1 |
| `number.floorheat_manual_max_temp` | 18–30 °C (25) | v1 (heat spread limit); manual schedules from v1.1 |
| `number.floorheat_manual_resume_delta` | 0.2–3.0 °C (1.0) | v1.1 (manual schedules) |
| `number.floorheat_holiday_temp` | 10–25 °C (18) | v1.1 (holiday) |
| `number.floorheat_failsafe_trigger` | 1–72 h (24) | v1.2 (failsafe) |
| `number.floorheat_valve_exercise_duration` | 5–30 min (15) | v1.2 (valve exercise) |
| `number.floorheat_long_run_alarm` | 2–48 h (12) | v1.2 (long run alarm) |

The minimum ON/OFF times can never be set below 30 minutes: they protect the heat pump from short cycles. Temperatures are shown in your HA unit system.
