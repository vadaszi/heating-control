# Shadow-mode trial checklist (after P6)

Work through this after installing floorheat as described in [Getting started](getting-started.md). Tick each box and write what you saw under **Feedback**: errors, odd values, anything you'd like different. "OK" is enough when all is well.

> If this file goes back into the repository, keep private details out of it: no IP addresses, email addresses, personal names or tokens (design §0.1).

Installed version (Settings → Devices & services → floorheat, or HACS): ______
HA version: ______  Date: ______

---

## 1. Before you start
- [ ] A fresh HA backup exists (Settings → System → Backups).
- [ ] HA is 2026.9.0 or newer.

Feedback:

## 2. Install
- [ ] HACS → ⋮ → Custom repositories → repository added as *Integration*.
- [ ] "Floor Heating Zone Control" downloaded (HACS shows a commit as the version: there is no release yet).

Feedback:

## 3. Stand-in switches
- [ ] One Template switch helper per valve and one for the heat source, each with **Value template and both actions empty**.
- [ ] The helper form accepted the empty fields.
- [ ] **Each stand-in switched OFF once**, so none shows "unknown".

Feedback:

## 4. Configuration and restart
- [ ] `floorheat:` block added to `configuration.yaml` (zone ids chosen carefully: they are permanent).
- [ ] `notify:` lists your phone and email targets.
- [ ] Developer tools → YAML → *Check configuration* passes.
- [ ] HA restarted.

Feedback:

## 5. First look after the restart
- [ ] Settings → System → Logs, filtered for "floorheat": no errors (warnings are listed below).
- [ ] No persistent notification about unknown entities or unknown notify targets.
- [ ] If every zone has a valve: the warning "Every zone has a valve…" appears. That is expected when your installation has another flow path (bypass, buffer); otherwise note it here.
- [ ] Entities exist per zone: `climate.floorheat_<zone>`, `sensor.floorheat_<zone>_state`, `_reason`, `_setpoint`, `number.floorheat_<zone>_hysteresis`, `_wait_time`.
- [ ] Global entities exist: `binary_sensor.floorheat_heat_request`, `sensor.floorheat_mode`, `sensor.floorheat_alerts`, `switch.floorheat_heating_season` (ON), `switch.floorheat_control_active` (**OFF**), `time.floorheat_sensor_fault_reminder` (08:00), and nine `number.floorheat_…` parameters.
- [ ] `sensor.floorheat_mode` is `normal` with attribute `shadow: true`.
- [ ] Every stand-in switch is still OFF (shadow mode switches nothing).

Feedback:

## 6. Values make sense
- [ ] Each climate entity's current temperature matches its sensor (plus the offset, if you set one).
- [ ] Target temperatures set to what you want per zone. The zone state and reason change within seconds.
- [ ] Reason texts are understandable (e.g. "Idle", "Waiting, 25 min left", "Calling zone").
- [ ] Temperatures are shown in your unit (°C).

Feedback (anything unclear in the names, texts or layout):

## 7. Settings survive a restart
- [ ] Change one parameter (e.g. a zone's wait time) and one target temperature.
- [ ] Restart HA: both values are kept, and Control active is still OFF.

Feedback:

## 8. Notifications and alerts
- [ ] Take the battery out of one sensor (or move it out of Bluetooth range). After about 60–70 minutes:
  - [ ] the zone state is `sensor_fault`;
  - [ ] "floorheat: sensor fault" arrives on the phone;
  - [ ] …and by email;
  - [ ] `sensor.floorheat_alerts` shows 1 and lists the zone.
- [ ] Put the battery back: "floorheat: sensor recovered" arrives, and alerts is back to 0.

Feedback:

## 9. V6: steady sensors don't look dead (the most important check)
Let it run for at least **24 hours**, ideally over a night when temperatures are stable.
- [ ] No zone went into `sensor_fault` while its sensor was working (history of `sensor.floorheat_<zone>_state`; no unexpected "sensor fault" notification).
- [ ] If one did, note the sensor, the time, and whether its temperature stayed unchanged for over an hour before that.

Result (V6 passed / failed, details):

## 10. Decisions after a day
- [ ] History of `binary_sensor.floorheat_heat_request`: when would floorheat have requested heat, compared with your current controller?
- [ ] Zone states and reasons over the day look plausible (a cold zone waits, then heats; the calling zone; zones joining).
- [ ] Nothing floorheat decided looks wrong or dangerous.

Feedback (times and zones help):

## 11. Optional: watch it switch the stand-ins
Harmless: the stand-ins are virtual and your real heating is not touched.
- [ ] Switch `switch.floorheat_control_active` ON: the stand-ins follow floorheat's decisions (a valve stand-in turns ON for a heating zone). Note that if the simulated heat pump was running, the minimum OFF time (60 min) runs first.
- [ ] Switch it OFF again: every stand-in that was ON is switched OFF once, then left alone.
- [ ] Leave Control active **OFF** afterwards.

Feedback:

---

## General feedback
Anything else: things you missed, would name differently, or want changed before P7.
