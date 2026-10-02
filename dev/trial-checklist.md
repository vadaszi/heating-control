# Shadow-mode trial checklist (after P6)

Work through this after installing floorheat as described in [Getting started](../docs/getting-started.md). Tick each box and write what you saw under **Feedback**: errors, odd values, anything you'd like different. "OK" is enough when all is well.

> If this file goes back into the repository, keep private details out of it: no IP addresses, email addresses, personal names or tokens (design §0.1).

Installed version (Settings → Devices & services → floorheat, or HACS): Version 0.6.0
HA version: 2026.9.3  Date: ______

---

## 1. Before you start
- [x] A fresh HA backup exists (Settings → System → Backups).
- [x] HA is 2026.9.0 or newer.

Feedback:

## 2. Install
- [x] HACS → ⋮ → Custom repositories → repository added as *Integration*.
- [x] "Floor Heating Zone Control" downloaded (HACS shows a commit as the version: there is no release yet).

Feedback:

## 3. Stand-in switches
- [x] One Template switch helper per valve and one for the heat source, each with **Value template and both actions empty**.
- [x] The helper form accepted the empty fields.
- [x] **Each stand-in switched OFF once**, so none shows "unknown".

Feedback:

## 4. Configuration and restart
- [x] `floorheat:` block added to `configuration.yaml` (zone ids chosen carefully: they are permanent).
- [x] `notify:` lists your phone and email targets.
- [x] Developer tools → YAML → *Check configuration* passes.
- [x] HA restarted.

Feedback:

## 5. First look after the restart
- [x] Settings → System → Logs, filtered for "floorheat": no errors (warnings are listed below).
- [x] No persistent notification about unknown entities or unknown notify targets.
- [x] If every zone has a valve: the warning "Every zone has a valve…" appears. That is expected when your installation has another flow path (bypass, buffer); otherwise note it here.
- [?] Entities exist per zone: `climate.floorheat_<zone>`, `sensor.floorheat_<zone>_state`, `_reason`, `_setpoint`, `number.floorheat_<zone>_hysteresis`, `_wait_time`.
- [?] Global entities exist: `binary_sensor.floorheat_heat_request`, `sensor.floorheat_mode`, `sensor.floorheat_alerts`, `switch.floorheat_heating_season` (ON), `switch.floorheat_control_active` (**OFF**), `time.floorheat_sensor_fault_reminder` (08:00), and nine `number.floorheat_…` parameters.
- [x] `sensor.floorheat_mode` is `normal` with attribute `shadow: true`.
- [x] Every stand-in switch is still OFF (shadow mode switches nothing).

Feedback: I have a two zone system, here are all my entities, check wheter is it ok. type: entities
title: floorheat
entities:
  - binary_sensor.floorheat_heat_request
  - climate.floorheat_living_room
  - climate.floorheat_bedroom
  - number.floorheat_living_room_hysteresis
  - number.floorheat_living_room_wait_time
  - number.floorheat_bedroom_hysteresis
  - number.floorheat_bedroom_wait_time
  - number.floorheat_hp_min_on_time
  - number.floorheat_hp_min_off_time
  - number.floorheat_sensor_fault_timeout
  - number.floorheat_manual_max_temp
  - number.floorheat_manual_resume_delta
  - number.floorheat_holiday_temp
  - number.floorheat_failsafe_trigger
  - number.floorheat_valve_exercise_duration
  - number.floorheat_long_run_alarm
  - sensor.floorheat_living_room_state
  - sensor.floorheat_living_room_reason
  - sensor.floorheat_living_room_setpoint
  - sensor.floorheat_bedroom_state
  - sensor.floorheat_bedroom_reason
  - sensor.floorheat_bedroom_setpoint
  - sensor.floorheat_mode
  - sensor.floorheat_alerts
  - switch.floorheat_heating_season
  - switch.floorheat_control_active
  - time.floorheat_sensor_fault_reminder

## 6. Values make sense
- [x] Each climate entity's current temperature matches its sensor (plus the offset, if you set one).
- [x] Target temperatures set to what you want per zone. The zone state and reason change within seconds.
- [x] Reason texts are understandable (e.g. "Idle", "Waiting" (the end time is the `until` attribute), "Calling zone").
- [x] Temperatures are shown in your unit (°C).

Feedback (anything unclear in the names, texts or layout): I may want to change wordings later, but now nothing. Some stuff seems unnatural, but without using I dont know what would be better. Leave it like this for now.

## 7. Settings survive a restart
- [x] Change one parameter (e.g. a zone's wait time) and one target temperature.
- [x] Restart HA: both values are kept, and Control active is still OFF.

Feedback:

## 8. Notifications and alerts
- [x] Take the battery out of one sensor (or move it out of Bluetooth range). After about 60–70 minutes:
  - [x] the zone state is `sensor_fault`;
  - [x] "floorheat: sensor fault" arrives on the phone;
  - [ ] …and by email;
  - [x] `sensor.floorheat_alerts` shows 1 and lists the zone.
- [x] Put the battery back: "floorheat: sensor recovered" arrives, and alerts is back to 0.

Feedback: I did not get email, but it seems normal as I did not setup my email anywhere.

## 9. V6: steady sensors don't look dead (the most important check)
Let it run for at least **24 hours**, ideally over a night when temperatures are stable.
- [ ] No zone went into `sensor_fault` while its sensor was working (history of `sensor.floorheat_<zone>_state`; no unexpected "sensor fault" notification).
- [ ] If one did, note the sensor, the time, and whether its temperature stayed unchanged for over an hour before that.

Result (V6 passed / failed, details): I did not let it run for 24 hours. I'll let you know if there is anything later.

## 10. Decisions after a day
- [ ] History of `binary_sensor.floorheat_heat_request`: when would floorheat have requested heat, compared with your current controller?
- [ ] Zone states and reasons over the day look plausible (a cold zone waits, then heats; the calling zone; zones joining).
- [ ] Nothing floorheat decided looks wrong or dangerous.

Feedback (times and zones help): I didnt check it, I'll let you know if there is anything later.

## 11. Optional: watch it switch the stand-ins
Harmless: the stand-ins are virtual and your real heating is not touched.
- [x] Switch `switch.floorheat_control_active` ON: the stand-ins follow floorheat's decisions (a valve stand-in turns ON for a heating zone). Note that if the simulated heat pump was running, the minimum OFF time (60 min) runs first.
- [x] Switch it OFF again: every stand-in that was ON is switched OFF once, then left alone.
- [x] Leave Control active **OFF** afterwards.

Feedback: I left Control Active ON, to see the simalated template switch

---

## General feedback
Anything else: things you missed, would name differently, or want changed before P7.
