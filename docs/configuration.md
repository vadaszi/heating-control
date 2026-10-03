# Configuration reference

Multizone Floor Heating Manager is set up in `configuration.yaml`. The YAML holds only the **wiring**: which sensor and which switches belong to which zone. Every value you change in daily use (set points, parameters, heating season, shadow mode) is changed from the HA UI and stored by the integration.

For a first installation, see [Getting started](getting-started.md); for what the settings do, see [How it works](how-it-works.md).

## Example

```yaml
# Multizone Floor Heating Manager: every key, each with a short comment.
# Copy it into configuration.yaml, use your own entity ids and addresses, and delete what
# you don't need. Active lines are required or show the default value; lines starting
# with # are optional: remove the # to use them.
multizone_floor_heating_manager:
  # The switch that asks the heat source for heat, e.g. a relay on the heat pump's room
  # thermostat terminals. Required.
  heat_source_switch: switch.heat_pump_request

  # The zones, in priority order: on a tie, the first one becomes the calling zone.
  # Required, at least one.
  zones:
    - id: living_room                         # required; never change it once in use
      name: Living room                       # required; display name, unique
      sensor: sensor.living_room_temperature  # required; the zone's temperature sensor
      valve: switch.valve_living_room         # required; the valve switch, or none
      sensor_offset: 0                        # added to every reading; default 0 (±5 °C)
    - id: kitchen
      name: Kitchen
      sensor: sensor.kitchen_temperature
      valve: switch.valve_kitchen
    - id: bedroom
      name: Bedroom
      sensor: sensor.bedroom_temperature
      valve: switch.valve_bedroom
    - id: bathroom
      name: Bathroom
      sensor: sensor.bathroom_temperature
      valve: none  # no valve: always open, the flow path when every valve is closed

  # Shelly devices running the watchdog script (docs/shelly-scripts.md). Every switch
  # above (the heat source and every valve) is in exactly one of the two lists: here, or
  # under relays_without_watchdog. Default: none.
  shellys_with_watchdog:
    - name: Valves              # shown in notifications; default: the host
      host: 192.0.2.11          # required; the Shelly's address, no http://
      script_id: 1              # required; the watchdog script's id on the device
      # password: !secret floor_heating_shelly_password  # only if authentication is on
      switches:                 # required; the mapped switches on this Shelly
        - switch.valve_living_room
        - switch.valve_kitchen
        - switch.valve_bedroom
    - name: Heat pump           # the heat source switch needs a Shelly of its own
      host: 192.0.2.12
      script_id: 1
      switches:
        - switch.heat_pump_request

  # Every other relay, without the watchdog script: one switch entity per entry (a
  # multi-channel relay is one entry per channel). Move a switch here from the list
  # above. Default: none.
  # relays_without_watchdog:
  #   - switch.valve_bedroom

  # Where notifications go: notify services or notify entities. Default: none (log only).
  # notify:
  #   - notify.mobile_app_phone
  #   - notify.email

  # External watchdog: the ping URL, e.g. a healthchecks.io check; keep it in
  # secrets.yaml. Default: none (no ping).
  # watchdog_ping_url: !secret floor_heating_watchdog_url
  watchdog_ping_interval: 300   # s (60–3600); how often the URL is pinged

  # Readings outside this range are ignored. Default: 0 to 40 °C, in any unit system.
  # If you set them, use your HA unit system: with °F e.g. 32 and 104.
  # plausible_min: 0
  # plausible_max: 40

  reconcile_interval: 60        # s (10–300); how often the outputs are checked
  output_mismatch_alert: 3      # reconcile intervals before "switch not following command"
  heartbeat_interval: 300       # s (60–3600); how often every Shelly gets a heartbeat
  heartbeat_fail_alert: 3       # failed heartbeats in a row before an alert
  heartbeat_timeout: 18000      # s; the scripts' heartbeat_timeout_s (5 h)
  # heartbeat_check_interval: 60  # s; the scripts' check_interval_s. Default: not compared
```

The entity ids and addresses are examples: use your own. The same example is in [`examples/configuration.example.yaml`](../examples/configuration.example.yaml); the tables below describe every key.

## Config entry and devices

The integration reads only the YAML. At startup it imports it into a single entry under *Settings → Devices & services* ("Multizone Floor Heating Manager"), so it can create devices:

- one device per zone, named "<zone name> floor heating" (e.g. "Living room floor heating");
- one device "Floor heating" for the global entities (heat request, heating season, shadow mode, parameters, alerts).

You can assign each zone device to its area in the device's settings; nothing in the YAML is needed for that. When you change the YAML and restart, the entry and devices follow: a new zone gets a device, a removed zone's device and entities are removed, a renamed zone's device gets the new name. *Add integration* in the UI only points to the YAML.

Without a `multizone_floor_heating_manager:` section the entry fails to load with an error saying so; nothing is deleted. Deleting the entry keeps the stored settings (targets, parameters, switches) in `.storage/multizone_floor_heating_manager`: with the YAML still in place, the entry comes back at the next restart with them.

## Keys

### Top level

| Key | Type | Default | Description |
|---|---|---|---|
| `heat_source_switch` | `switch` entity | required | The switch that requests heat from the heat source (e.g. a relay on the heat pump's thermostat terminals). |
| `zones` | list | required | At least one zone; see below. The order matters: on a tie, the zone listed first becomes the calling zone. |
| `plausible_min` | number | 0 °C | Readings below this are ignored as implausible. Must be below `plausible_max`. |
| `plausible_max` | number | 40 °C | Readings above this are ignored as implausible. |
| `reconcile_interval` | integer, seconds (10–300) | 60 | How often the outputs are checked and corrected. |
| `output_mismatch_alert` | integer ≥ 1 | 3 | Alert after this many reconcile intervals in which a switch does not follow its command or is unavailable. |
| `notify` | list of `notify.<name>` | none | Where notifications go: a notify service (e.g. `notify.mobile_app_phone` from the companion app, or an SMTP `notify.email`) or a notify entity. Without targets, events are only written to the log. |
| `shellys_with_watchdog` | list | none | The Shelly devices running a watchdog script, each with its address and the switches on it; see [Shelly watchdogs](#shelly-watchdogs). |
| `relays_without_watchdog` | list of `switch` entities | none | Every other mapped switch, one by one: any relay without the watchdog script, or a template switch for a try-out. A multi-channel relay (e.g. a Sonoff 4CH Pro) is one entry per channel; its address plays no role. They get no heartbeat. |
| `heartbeat_interval` | integer, seconds (60–3600) | 300 | How often every Shelly gets a heartbeat. Must be shorter than `heartbeat_timeout`. |
| `heartbeat_fail_alert` | integer ≥ 1 | 3 | Alert after this many failed heartbeats in a row (3 × 5 min ≈ 15 min). |
| `heartbeat_timeout` | integer, seconds (1–604800) | 18000 (5 h) | The `heartbeat_timeout_s` you expect in the scripts' CONFIG block. The integration alerts if a script reports another value. |
| `heartbeat_check_interval` | integer, seconds (1–604800) | not checked | If set, the `check_interval_s` you expect in the scripts; otherwise it is not compared. |
| `watchdog_ping_url` | URL (`http://` or `https://`) | none | The external watchdog's ping URL, e.g. a healthchecks.io check; see [External watchdog](#external-watchdog). Without it, no ping is sent. |
| `watchdog_ping_interval` | integer, seconds (60–3600) | 300 | How often the URL is pinged. Set the check's period on the external service to the same value. |

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

Each Shelly that switches valves or the heat source runs a watchdog script ([Shelly scripts](shelly-scripts.md)). It puts its outputs into a safe state if Home Assistant stops sending heartbeats. List every such Shelly under `shellys_with_watchdog`:

| Key | Type | Default | Description |
|---|---|---|---|
| `name` | string | the `host` | Shown in notifications and the log. Names must be unique. |
| `host` | string | required | The Shelly's address as in its web UI URL, e.g. `192.0.2.11` or a host name, optionally with `:port`. No `http://`. |
| `script_id` | integer ≥ 1 | required | The id of the watchdog script on the device (the number in the web UI's script list). |
| `switches` | list of `switch` entities | required | The mapped switches (valves or the heat source) on this Shelly. |
| `password` | string | none | Only if authentication is on for the device; use `!secret`, e.g. `password: !secret floor_heating_shelly_password`. The user name is always `admin`. |

Rules:
- **Every mapped switch** (the heat source and every valve) is in exactly one of the two lists: on one Shelly under `shellys_with_watchdog`, or in `relays_without_watchdog`. Anything else is a configuration error.
- The Shelly with the heat source switch runs the **heat source script** and must hold no valve; every other listed Shelly runs the **valve script**. The integration checks this with each answer.
- The integration does not detect device types: it trusts this list.

> ⚠️ **A switch in `relays_without_watchdog` has no device failsafe.** If Home Assistant stops, it stays as it was, e.g. a heat source request ON, until someone switches it. Only the [external watchdog](#external-watchdog) tells you that HA is down. With Shelly relays and the watchdog scripts the house goes into a safe state on its own ([Shelly scripts](shelly-scripts.md)).

### External watchdog

The Shelly watchdogs protect the house when Home Assistant stops, but nothing tells **you**. For that, the integration pings an external monitoring service, e.g. [healthchecks.io](https://healthchecks.io) (free for a few checks), every `watchdog_ping_interval` (5 min). When the pings stop, the service alerts you (email, app, …); it also tells you when they resume.

```yaml
multizone_floor_heating_manager:
  watchdog_ping_url: !secret floor_heating_watchdog_url
  watchdog_ping_interval: 300   # optional; default 300 s (5 min), 60–3600
```
```yaml
# secrets.yaml
floor_heating_watchdog_url: https://hc-ping.com/<your-check-uuid>
```

- **The URL is a secret.** Anyone who has it can keep the check "up" while your HA is dead, or send false alarms. `!secret` keeps it out of `configuration.yaml`, which people often share (forum posts, backups on GitHub). Writing the URL directly in `configuration.yaml` works the same; the integration never writes it to the log.
- Any `http://` or `https://` URL is called with `GET`, so other services with a push/heartbeat URL work too (e.g. Uptime Kuma's push monitor).
- **Settings on healthchecks.io:** period = `watchdog_ping_interval` (5 min), grace about 30 min. The alert then comes about 35 minutes after the last ping, so an HA update or restart (typically 5–20 min) does not raise a false alarm. Setup steps: [Getting started](getting-started.md#9-external-watchdog).
- The ping goes out also in shadow mode, but only while the integration works (as the heartbeat, see [How it runs](#how-it-runs)): a broken integration triggers the alert too.
- A failed ping (e.g. your internet is down, or a wrong URL that answers HTTP 404) is only logged: a warning at the first failure and an info line when it works again. There is no notification: the external service alerts you when its pings stop. A check that has never received a ping does not alert on healthchecks.io, so after the setup make sure the check shows the first ping.

## Checks at startup

- A structural error (missing key, wrong entity domain, duplicate zone id or name, invalid id, value out of range, unknown key) stops the setup. HA shows "Invalid config" and the log names the problem. For an invalid id, a valid one is suggested.
- An entity that HA does not know (neither registered nor reporting a state) is logged as an error and shown as a persistent notification once HA has started. The integration still runs:
  - an unknown sensor gives no reading, and the zone goes into sensor fault after the timeout;
  - an unknown switch counts as unavailable, which is OFF.
- A sensor without a temperature unit is logged once as a warning; its readings are ignored.
- A `notify` target that is neither a notify service nor a notify entity is logged as a warning and shown as a persistent notification once HA has started.
- A mapped switch that is in neither `shellys_with_watchdog` nor `relays_without_watchdog`, a switch listed twice, the same Shelly (`host` and `script_id`) listed twice, a heat source Shelly holding a valve, a `heartbeat_interval` not shorter than `heartbeat_timeout`, or a `plausible_min` not below `plausible_max` stops the setup.

## How it runs

- **Reconcile loop.** Every `reconcile_interval`, and at once whenever a mapped sensor or switch changes state, the integration computes the desired outputs from the current states and switches every output that differs. It starts only when HA has finished starting, so entities that are still loading neither get commands nor count as failures.
- **Retries.** A switch that does not follow gets its command again after 1, 2, 4 and 8 minutes, then every 15 minutes. Nothing is sent to an unavailable switch; when it returns, it is corrected at once. After `output_mismatch_alert` intervals the alert "output not following command" is notified.
- **Restart.** The logic state (timers, calling zone, sensor faults, heat source ON/OFF times) and the UI settings are stored in HA's `.storage/multizone_floor_heating_manager` file, at most every 30 seconds and when HA stops. After a restart the switch states are read back and control continues where it stopped.
- **Unavailable heat source switch.** It counts as OFF while it is unavailable. If it comes back ON, the heat pump never stopped (a device that lost power restarts OFF), so it is not switched OFF because of a Wi-Fi glitch.
- **Heartbeat.** Every `heartbeat_interval` each listed Shelly gets a heartbeat (`POST http://<host>/script/<script_id>/heartbeat`, [protocol](heartbeat-protocol.md)), also in shadow mode. The heat source Shelly's heartbeat carries the heating season switch; it gets one at once when you switch the season. Each answer is the script's status:
  - after `heartbeat_fail_alert` failed heartbeats in a row, "Shelly … not answering" is notified once, naming the cause: unreachable, script not running (HTTP 404: stopped, or wrong `script_id`), authentication failed, or an unexpected answer (wrong script, unsupported protocol version). When it answers again, the recovery is notified. A single failed call (a Wi-Fi hiccup) never alerts;
  - if the script's `heartbeat_timeout_s` (or `check_interval_s`, if you set `heartbeat_check_interval`) differs from the expected value, that is notified once; it clears silently when the values match again;
  - only logged, never notified: a watchdog that had timed out (seen in the status read on the first call after HA starts and after a failed call) and a Shelly that restarted.
  - only logged, never notified: a heat source watchdog that was running its failsafe operation (no heartbeat for 24 h, [Shelly scripts](shelly-scripts.md#failsafe-operation-heat-source)).
- **Heartbeat and watchdog ping only while the integration works.** Heartbeats and the [watchdog ping](#external-watchdog) go out only if the reconcile loop has completed a run within the last 3 reconcile intervals. If the integration is broken (e.g. after an HA update), the Shellys stop getting heartbeats and act after their timeout, as if HA had stopped, and the external watchdog alerts you.

## Shadow mode

After the first installation the **Shadow mode** switch is **ON**. The integration then reads everything and makes its decisions, but it sends **no** switch commands. It treats its own decisions as if the switches had followed, so the decisions stay consistent. Use it to check the integration's decisions before it takes over; the steps are in [Getting started](getting-started.md#7-shadow-run).

- **Switching Shadow mode ON** (live → shadow) sends one final safe set: heat source OFF, all valves OFF. A switch that is unavailable at that moment, or does not follow, gets the OFF again (with the retries above) until it has reported OFF once, also across a restart. After that, nothing more is sent.
- **Switching Shadow mode OFF** (shadow → live) sets every output to the desired state at the next run.
- **Going live after shadow mode:** from then on the real switch states count. If shadow mode believed the heat source was running, the real switch reads OFF. That counts as a stop, so the minimum OFF time (default 60 min) runs before the heat source is first requested. The zones that need heat already open their valves.

## Schedules and holiday

Schedules and holiday change a zone's target for a while. They are stored by the integration (they survive restarts) and changed from the dashboard or with the [services](#services). What is in force, highest first: **holiday**, then a **manual schedule**, then an **auto schedule**, then the zone's **base set point** (the climate entity's target).

### Auto schedules
An auto schedule sets the target of one or more zones during a time window, e.g. every day 13:00–17:00 Living room 23 °C, or Sunday 10:00–16:00 all zones 21 °C (10–30 °C).
- Two auto schedules for the same zone may not overlap: the second one is rejected with an error, and nothing is stored.
- When the window ends, the target returns to the base set point. A target raised at the window start starts heating at once, without the wait time (the heat source minimum off time still applies).

### Manual schedules
A manual schedule forces one or more zones to heat during a window: valve open and heat demand, whatever the temperature (state `forced`, reason "Manual schedule").
- Safety cap: at the **manual max temperature** (default 25 °C) the zone closes and stops asking for heat ("Manual schedule, paused: too warm"). It resumes below the manual max temperature minus the **manual resume difference** (default 1.0 °C).
- A manual schedule has no temperature of its own: the target below it (auto schedule or base set point) stays in force and applies again when the window ends.
- Manual schedules for the same zone may overlap; together they cover the union of their windows.
- Other zones that get cold while a manual schedule runs the heat source join it.
- A zone with a sensor fault, or without any reading yet, is not forced (the cap cannot be checked).
- Outside the heating season, and during holiday, manual schedules do nothing.

### Times
- Windows are local wall-clock times in HA's time zone. 13:00–17:00 runs from 13:00 until just before 17:00, so 10:00–12:00 and 12:00–14:00 don't overlap.
- An end before the start crosses midnight: 22:00–02:00. Start and end must differ.
- **One-shot** schedules run once, on a date; they are deleted automatically when their window is over. A one-shot schedule whose window is already over is rejected. **Recurring** schedules run on selected weekdays (every day = all seven); a window belongs to the day it starts on ("Sunday 22:00–02:00" runs Sunday night into Monday).
- Daylight saving time: a time in the repeated autumn hour means its first occurrence; a time in the skipped spring hour moves by an hour (02:30 → 03:30), so the window keeps its length.
- A schedule covers a list of zones or **all zones**. "All zones" also covers zones you add to the YAML later. A zone removed from the YAML is removed from every schedule at the next start, and a schedule left without zones is deleted (both logged as warnings).
- Every schedule gets a number (`#1`, `#2`, …), which is never reused. Its label is built from its content, e.g. `#3 Auto · Living room · Every day 13:00–17:00 · 23.0 °C`.

### Holiday
While holiday is on, every zone's target is its own **holiday temperature** (a number per zone, default 18 °C, 10–25 °C; it may be above or below the base set point), and schedules are suspended. The base set points are not changed, so everything returns to normal when holiday ends. The mode sensor shows `holiday`.
- Holiday starts when you switch it on. Its end is an **end date** and an **end time** (two entities), set before or while it runs, so it can last any number of days. **Without an end date it runs until you switch it off.** A date alone ends at the end time shown (default 12:00).
- What you enter is not checked when you enter it, e.g. you may set the end on 1 October for 5 October. It is checked when you switch holiday on: switching it on with an end in the past is refused with an error.
- Changing the end while holiday runs moves it: a later end extends it; an end in the past ends holiday at once (within a minute). Make sure the end you enter is still ahead.
- Holiday ends at its end or when you switch it off; either way the end date is cleared for the next holiday, and the end time is kept.
- There is no automatic preheat: set the end early enough for the house to warm up. At the end, zones below their start temperature begin heating at once.

## Services

The schedule services act like the schedule form on the dashboard, but they also take several zones or any combination of weekdays in one schedule. Use them in automations, scripts or *Developer tools → Actions*.

| Service | Fields | Result |
|---|---|---|
| `multizone_floor_heating_manager.add_schedule` | `kind`: `auto` or `manual`; `zones`: a list of zone ids from the YAML, or `all`; either `date` (one-shot, the day the window starts) or `weekdays` (recurring: any of `mon` `tue` `wed` `thu` `fri` `sat` `sun`); `start`, `end` (local time); `temperature` (auto only, 10–30 °C, in your unit system) | Adds the schedule and returns it (`schedule`, as in the Schedules sensor). A schedule that is not valid, or an auto schedule that overlaps another one for the same zone, is rejected with an error naming the problem, and nothing is stored. |
| `multizone_floor_heating_manager.delete_schedule` | `schedule_id`: the number from the label (`3` or `#3`) | Deletes the schedule. |
| `multizone_floor_heating_manager.list_schedules` | none | Returns `schedules`, the list in the Schedules sensor's attribute. It only returns data, so call it with a response (e.g. *Developer tools → Actions*, or `response_variable` in a script). |

Example: every Monday, Wednesday and Friday 13:00–17:00, Living room and Kitchen at 23 °C:

```yaml
action: multizone_floor_heating_manager.add_schedule
data:
  kind: auto
  zones: [living_room, kitchen]
  weekdays: [mon, wed, fri]
  start: "13:00"
  end: "17:00"
  temperature: 23
```

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
| Floor heating: failsafe started | No sensor has sent a valid reading for longer than the failsafe operation delay (heating season only): every valve opens and the heat source runs daily from failsafe operation start to stop. Also in shadow mode. |
| Floor heating: failsafe ended | A sensor reports again (normal control resumes), or the heating season was switched off. |
| Floor heating: heat source long run | The heat source has been running for longer than the long run alarm. Also in shadow mode. |
| Floor heating: heat source back to normal | After a long run alarm, the heat source switch reports OFF. |

The weekly valve exercise is not notified; it only writes log lines.

A few problems are shown as a **persistent notification** in HA (the bell in the sidebar) instead, because they need you, not the heating:

| Title | When |
|---|---|
| Floor heating: unknown entities | A sensor or switch in the YAML is unknown to HA after it has started (typo, or its integration did not load). It counts as unavailable. |
| Floor heating: unknown notify targets | A `notify` target is neither a notify service nor a notify entity. |
| Floor heating: schedule not added | The schedule form's draft was rejected; the message names the problem. |
| Floor heating: schedule not deleted | The schedule chosen in the form no longer exists. |

A target that is a notify service (the companion app, SMTP) is called as `notify.<name>`; otherwise the notify entity of that id gets `notify.send_message`. A failing target is logged and never stops the control.

## Entities

The entities follow Home Assistant's naming conventions. Each belongs to a zone device ("<zone name> floor heating") or to the "Floor heating" device, and its name names only the value (e.g. "Reason"). Home Assistant generates each entity id from the device name and the entity name when the entity is first created, e.g. `sensor.living_room_floor_heating_reason`. After that the id is kept, also when you rename the zone; you can change it yourself in the entity settings. The ids below are the generated ones, for a zone named "Living room" (`<zone>` = `living_room`). Values changed through these entities are stored by the integration and survive restarts.

Settings (the parameter numbers, the times of day and the valve exercise day) have the *configuration* category: HA shows them under "Configuration" on the device page and leaves them out of automatically generated dashboards.

### Per zone (device "<zone name> floor heating")

| Entity (name) | Shows / changes |
|---|---|
| `climate.<zone>_floor_heating` (named like the device) | Current temperature = the zone temperature (reading + offset); target = the zone's base set point (10–30 °C, step 0.1). Mode `heat` only. `hvac_action` is *heating* while the heat source request is ON and the zone gets flow (valve open, or no valve), otherwise *idle*. Attributes: `zone_state`, `reason` (the keys below), `valve` (desired state; none without a valve), `calling_zone`. |
| `sensor.<zone>_floor_heating_state` (State) | `idle` (Idle), `waiting` (Waiting), `heating` (Heating), `forced` (Forced), `sensor_fault` (Sensor fault). |
| `sensor.<zone>_floor_heating_reason` (Reason) | Why the zone is in its state; the table below. The state is a fixed key, shown as its text; it never counts down, so the state changes only when the reason does. While a timer runs (`waiting`, `held_by_minimum_off_time`, `spreading_heat`, `forced`, `failsafe_heating`, `failsafe_waiting`, `valve_exercise`), the attribute `until` holds its end time; otherwise there is no `until` attribute. |
| `sensor.<zone>_floor_heating_effective_target_temperature` (Effective target temperature) | The set point in force: the zone's holiday temperature while holiday is on, otherwise the running auto schedule's temperature, otherwise the base set point. A manual schedule keeps the set point below it. |
| `number.<zone>_floor_heating_hysteresis` (Hysteresis) | 0.1–2.0 °C (default 0.2). StartTemp = set point − hysteresis, StopTemp = set point + hysteresis. |
| `number.<zone>_floor_heating_wait_time` (Wait time) | 0–120 min (default 30). Open-window filter before the zone may start the heat source. |
| `number.<zone>_floor_heating_holiday_temperature` (Holiday temperature) | 10–25 °C (default 18). The zone's target while holiday is on; it may be above or below the base set point. |

> **Zone without a valve:** its climate entity shows *Heating* whenever the heat source runs for any zone, because warm water then flows through it. *Heating* on a climate entity means "warm water flows here", not "this zone wants heat". The zone's state and reason say what the zone itself wants (e.g. Idle). The climate mode is always *Heat*: it is the only mode, there is no per-zone off.

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
| `forced` | Manual schedule | A manual schedule runs: valve open and heat demand regardless of the temperature (`until`: when the manual windows end). |
| `forced_too_warm` | Manual schedule, paused: too warm | A manual schedule runs, but the zone reached the manual max temperature: closed, no demand, until it is below the manual max temperature minus the manual resume difference. |
| `failsafe_heating` | Failsafe heating | No sensor has sent a valid reading for longer than the failsafe operation delay; between failsafe operation start and stop every valve is open and the heat source runs (`until`: the operation stop). |
| `failsafe_waiting` | Failsafe, waiting for operation start | Failsafe, outside the daily operation time (`until`: the next operation start). |
| `valve_exercise` | Valve exercise | Outside the heating season, the weekly valve exercise has this zone's valve open (`until`: when it closes). |

### Global (device "Floor heating")

| Entity (name) | Shows / changes |
|---|---|
| `binary_sensor.floor_heating_heat_request` (Heat request) | The heat source request the integration wants (in shadow mode: the simulated one). While the heat source runs: attributes `on_since` and `on_duration` (minutes; not kept in the history). Both are left out while it is not running. |
| `sensor.floor_heating_heat_source` (Heat source) | What the heat source does and why: `heating` (Heating), `idle` (Off, no heat demand), `held_by_minimum_off_time` (Waiting for minimum off time: a zone wants heat, but the heat source stopped less than its minimum off time ago), `spreading_heat` (Running for minimum on time (spreading heat): no zone needs heat, but the minimum on time has not passed), `season_off` (Heating season off), `unavailable` (Switch unavailable), `failsafe_heating` (Failsafe heating), `failsafe_waiting` (Failsafe, waiting for operation start). If several apply, the first in this order wins: unavailable, season off, waiting, spreading, failsafe heating, failsafe waiting, heating, off. While waiting or spreading, the attribute `until` holds the end of that timer, in the failsafe the operation stop or the next operation start; otherwise there is no `until`. In shadow mode it shows the simulated heat source. |
| `sensor.floor_heating_mode` (Mode) | `normal`; `holiday` while holiday is on; `failsafe` from when no sensor has sent a valid reading for longer than the failsafe operation delay until the first one reports again (heating season only; failsafe wins over holiday). Attribute `shadow`: true while Shadow mode is ON. |
| `sensor.floor_heating_schedules` (Schedules) | Number of schedules; attribute `schedules` lists them, each with `id`, `label` (e.g. `#3 Auto · Living room · Every day 13:00–17:00 · 23.0 °C`), `kind`, `zones` (zone ids or `all`), `date` (one-shot) or `weekdays` (`mon` … `sun`), `start`, `end`, `temperature` (auto, in your unit system). See [Schedules and holiday](#schedules-and-holiday). |
| `sensor.floor_heating_alerts` (Alerts) | Number of active alerts; attribute `alerts` lists them (`kind`, `zone_id`, `message`): sensor faults (`sensor_fault_started`), switches not following (`output_mismatch`), Shellys not answering (`watchdog_failed`), Shelly script parameters differing (`watchdog_params_mismatch`), the failsafe (`failsafe_started`) and a long run (`long_run`). |
| `switch.floor_heating_heating_season` (Heating season) | Heating season (default ON). OFF: no heating demand, heat source OFF and valves closed at once. |
| `switch.floor_heating_shadow_mode` (Shadow mode) | ON = shadow mode: decisions only, no switch commands (default after the first installation). OFF = the integration switches the relays. See [Shadow mode](#shadow-mode). |
| `time.floor_heating_sensor_fault_reminder_time` (Sensor fault reminder time) | Time of the daily sensor fault reminder (default 08:00). Configuration category. |
| `switch.floor_heating_holiday` (Holiday) | Holiday on/off; see [Holiday](#holiday). Switching it on with an end in the past is refused; it turns off by itself at the end. |
| `date.floor_heating_holiday_end_date` (Holiday end date) | The day holiday ends. Empty (unknown) = no end. Cleared when holiday ends. |
| `time.floor_heating_holiday_end_time` (Holiday end time) | The time of day holiday ends on the end date (default 12:00). Never empty; kept when holiday ends. |

### Schedule form (device "Floor heating")

These entities are a form for adding and deleting schedules from the dashboard, so you need no helpers of your own. Fill in the draft, then press **Add schedule**. If the schedule is rejected (e.g. it overlaps another auto schedule), a persistent notification "Floor heating: schedule not added" names the problem and nothing is stored. The draft keeps its values after adding, so a similar schedule is quick to add; after a restart it starts from the defaults.

| Entity (name) | Shows / changes |
|---|---|
| `select.floor_heating_schedule_type` (Schedule type) | `auto` (sets the target temperature) or `manual` (forces heating). Default auto. |
| `select.floor_heating_schedule_zone` (Schedule zone) | "All zones" or one zone (default all zones). For several zones in one schedule use the `add_schedule` service. |
| `select.floor_heating_schedule_days` (Schedule days) | Once (on the schedule date), every day, Monday to Friday, Saturday and Sunday, or one weekday (default every day). Other combinations: the `add_schedule` service, or one schedule per day. |
| `date.floor_heating_schedule_date` (Schedule date) | The day of a one-shot ("Once") schedule. Default today. |
| `time.floor_heating_schedule_start` (Schedule start), `time.floor_heating_schedule_end` (Schedule end) | The window, local time (default 06:00–08:00). An end before the start crosses midnight. |
| `number.floor_heating_schedule_temperature` (Schedule temperature) | The target of an auto schedule, 10–30 °C (default 22). Ignored for manual schedules. |
| `button.floor_heating_add_schedule` (Add schedule) | Adds the draft as a new schedule. |
| `select.floor_heating_existing_schedule` (Existing schedule) | The schedules by label (e.g. `#3 Auto · Living room · Every day 13:00–17:00 · 23.0 °C`); the one chosen here is deleted by the button below. |
| `button.floor_heating_delete_schedule` (Delete schedule) | Deletes the schedule chosen in Existing schedule. |

### Global parameters (device "Floor heating", configuration category)

| Entity (name) | Range (default) |
|---|---|
| `number.floor_heating_heat_source_minimum_on_time` (Heat source minimum on time) | 30–180 min (60) |
| `number.floor_heating_heat_source_minimum_off_time` (Heat source minimum off time) | 30–180 min (60) |
| `number.floor_heating_sensor_fault_timeout` (Sensor fault timeout) | 15–240 min (60) |
| `number.floor_heating_manual_max_temperature` (Manual max temperature) | 18–30 °C (25) |
| `number.floor_heating_manual_resume_difference` (Manual resume difference) | 0.1–10.0 °C (1.0) |
| `number.floor_heating_failsafe_operation_delay` (Failsafe operation delay) | 1–72 h (24) |
| `time.floor_heating_failsafe_operation_start` (Failsafe operation start), `time.floor_heating_failsafe_operation_stop` (Failsafe operation stop) | local time (10:00–15:00) |
| `select.floor_heating_off_season_valve_exercise_day` (Off-season valve exercise day) | Monday–Sunday (Monday) |
| `time.floor_heating_off_season_valve_exercise_time` (Off-season valve exercise time) | local time (08:00) |
| `number.floor_heating_off_season_valve_exercise_duration` (Off-season valve exercise duration) | 5–30 min (15) |
| `number.floor_heating_long_run_alarm` (Long run alarm) | 2–48 h (12) |

The minimum on/off times can never be set below 30 minutes: they protect the heat pump from short cycles. Temperatures are shown in your HA unit system.

**Failsafe** (no valid reading from any sensor): it starts when the newest reading of any sensor is older than the failsafe operation delay and every zone is in sensor fault (or has had no reading yet), in the heating season only. Daily from failsafe operation start to stop every valve opens and the heat source runs; the minimum on and off times still apply. The operation time may cross midnight (e.g. 22:00–03:00); start and stop must differ. The first valid reading ends the failsafe at once. This is the case where HA still runs; if HA itself stops, the Shelly watchdog scripts act instead ([Shelly scripts](shelly-scripts.md)).

**Valve exercise** (heating season off): once a week on the set day and time, the valves open one after another in the order of the zones in the YAML, each for the exercise duration, with the heat source off (zones without a valve are skipped). If HA is not running at that time, the run is skipped until the next week; after a restart during a run, the remaining valves continue. Switching the heating season on ends it.

## Updating

- Update through HACS (or copy the new `custom_components/multizone_floor_heating_manager` folder) and restart HA. Read the [changelog](../CHANGELOG.md) first: it lists every entity that was added, renamed or removed, so you know which dashboard cards and automations to check.
- Your settings, schedules and the control state are kept: they are stored by zone id, not by entity.
- **Entities a new version no longer provides are removed** at the start, one log line each. Home Assistant remembers a removed entity's id and settings for a while and gives them back if the entity returns (e.g. after going back to an older version).
- **Ids of new entities:** Home Assistant builds a new entity's id from the device's **area**, the device name and the entity name. An entity added by an update to a zone device that you have already placed in an area gets the area in its id, e.g. `number.bedroom_bedroom_floor_heating_holiday_temperature` for a zone "Bedroom" in the area "Bedroom". Entities that existed before keep their ids. Rename such an id in the entity settings if you like.
