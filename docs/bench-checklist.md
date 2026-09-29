# Bench and real-switch checklist (after P6, before P7)

Work through this with the valve Shellys on the bench (nothing connected to the outputs) and floorheat running in HA. Tick each box and write what you saw under **Feedback**. "OK" is enough when all is well.

> Keep private details out of this file: no IP addresses, passwords, email addresses or personal names (design §0.1). Write `<shelly-address>` instead of the address.

Devices: Shelly Plus 2PM (Gen2) × 2. Firmware version(s): 1.7.5 (20260311-095847/1.7.5-g9979d16), device 1
floorheat version: 0.6.0  HA version: 2026.9.3  Date: ______

The detailed steps for B are in [Shelly scripts](shelly-scripts.md#bench-tests-shortened-timeouts).

---

## A. Preparation (Shelly web UI, both 2PMs)
- [x] Firmware updated; version noted above.
- [x] Profile: **Switch** (not Cover).
- [x] **Power-on default: OFF** on both channels.
- [x] No auto-ON/auto-OFF timers and no schedules on the channels.
- [x] SW inputs set to **detached**.
- [x] Wi-Fi signal OK where they will be mounted (roughly better than −70 dBm).
- [x] Authentication decided (on / off): off

Feedback:

## B. Watchdog script bench tests (2PMs only)
Switch **Control active OFF** first, so floorheat does not touch the channels during these tests.

Device 1 (script id ___):
- [x] `valve_watchdog.js` uploaded and started; the console shows "started".
- [x] **Run on startup** enabled (the script answered right after a power cycle).
- [x] `heartbeat_timeout_s: 120` and `check_interval_s: 5` set, script restarted; `params` show 120 and 5.
- [x] **S6:** `GET` answers `"running": true, "state": "normal"`; a `POST` heartbeat gives `"heartbeat_seen": true, "heartbeat_age_s": 0`; a bad body (`-d 'x'`) gives `400`. *(Passed with the default params.)*
- [x] **S1:** heartbeat loop running, all channels OFF → stop the loop → within about 2 min **all channels ON**, `"state": "timed_out"`. One channel switched OFF in the app → back ON within about 5 s. *(Passed: both ON after about 2 min; a channel switched OFF came back ON.)*
- [x] **S4:** loop started again → `"state": "normal"`, **no channel changes**. A channel switched OFF in the app **stays OFF**. *(Passed: state back to normal; channels switched in the app were left alone.)*
- [x] **Reboot:** heartbeat sent, channels ON → reboot → channels **OFF**, the script answers again on its own, `"heartbeat_seen": false`, small `uptime_s`. Without heartbeats all channels switch ON about 2 min after the boot. *(Power cycle (unplugged): channels OFF, script running, `heartbeat_seen: false`, `uptime_s` 20: passed. Switch ON 2 min after boot: passed. A software reboot from the web UI keeps the channels ON (checked twice); only a real power loss applies the power-on default OFF.)*
- [x] **V2:** `GET` and `POST` work; with authentication on: `401` without the password, `200` with it; still answers after a reboot. *(Passed without authentication; the 401/200 part was not tested because authentication is off.)*

Device 2 (script id ___):
- [x] Uploaded, started, Run on startup enabled.
- [x] Short timeouts set.
- [x] S6
- [x] S1
- [x] S4
- [x] Reboot
- [x] V2 *(passed without authentication, as device 1)*

**Afterwards (both devices):**
- [x] Timeouts back to the defaults (`18000` / `60`); `params` confirm it.
- [x] Script **stopped** and **Run on startup off** until P7 (HA sends no heartbeats yet; otherwise all channels switch ON after 5 h).

Feedback (device type, firmware, pass/fail per test; no addresses or passwords):

## C. floorheat with the real Shellys
**Control active ON**, the watchdog scripts stopped. Use the office and bathroom sliders to create the situations.
- [x] **Following:** office slider below StartTemp → "Waiting, N min left" → heating → the office channel turns ON. Slider up to StopTemp → idle → the channel turns OFF (unless min ON holds it).
- [x] **Window closed during the wait (A2):** slider down → waiting → slider back up before the wait ends → **idle** at the end of the wait; the heat pump stays off. *(Seen: the zone stays waiting when the slider goes back up; only the check at the end of the wait counts (D-05). Result at the end of the wait: idle, passed.)*
- [x] **Zone without a valve (A23):** bathroom slider down → the bathroom calls and starts the heat pump stand-in; no valve switches.
- [x] **Joining while running (A4):** while the heat pump runs, another zone below StartTemp joins **at once**, without the wait. *(Joined at once. Open question: the joining zone became the calling zone; to be explained.)*
- [x] **Sync rule (A6):** the calling zone reaches its set point → the other zones below StopTemp join, **once**. *(Passed with the heat pump stand-in ON. It cannot fire while min OFF holds the request: no cycle, no calling zone.)*
- [x] **Spreading heat (A8):** all zones satisfied before the heat pump has run 60 min → **all valves ON**, "Spreading heat (min ON)", until the 60 min are up. *(Passed; a zone above ManualMaxTemp stayed closed, D-71.)*
- [x] **Minimum OFF:** after the heat pump stops, a cold zone shows "Held by min OFF, N min left"; its valve opens at once, the heat pump waits. *(Passed with a set point raise (rule 4, D-26, D-64): the valve opened at once, the heat pump stand-in waited for min OFF.)*
- [x] **Correction (A25):** a channel switched the wrong way in the Shelly app → floorheat switches it back within about 1 min. *(Passed: switched back within about a second, because a state change of a mapped switch starts a run at once, D-109.)*
- [x] **Unavailable (A27):** a 2PM unplugged → after about 3 min "floorheat: output not following command" on the phone → plugged back → corrected, "floorheat: output recovered". *(Passed 2026-09-29: the valve switches turned unavailable in HA, the alert arrived, and the recovery notification came after the 2PM was plugged back in.)*
- [x] **Power-on default:** a 2PM power-cycled while a channel is ON → it comes back **OFF** → floorheat switches it ON again. *(Passed 2026-09-29, same run: the office channel was ON again without any manual action. The short OFF after the power-up was not watched separately; the power-on default itself was checked in section B.)*
- [x] **Heating season OFF:** all valves and the heat pump stand-in OFF **at once**, even within min ON. Then back ON.
- [x] **HA restart while valves are ON:** no burst of switching after the restart; the valves stay as they should. *(Passed with a full reboot of the HA machine: the relay states stayed, checked in the Shelly app.)*
- [x] **Control active ON → OFF:** every channel that is ON turns OFF **once**. A channel switched ON in the app afterwards **stays ON** (floorheat has let go).

Feedback:

## D. Still open from the first trial ([trial checklist](trial-checklist.md))
- [ ] **V6:** 24 h or more without a false `sensor_fault` on the real thermometers (history of `sensor.floorheat_<zone>_state`).
- [ ] **§10:** a day of decisions compared with the current controller.
- [ ] Optional: an email notify target.
- [ ] The momentary "valve wants" dip (seen once at 11:53) does not come back; if it does, note the time and what the Logbook shows for that minute.

Feedback:

## Not possible yet
- **V1** (actuator holding power): needs the actuators connected.
- **V3** (HA finds each Shelly's address): dropped in P7 (D-120); the address and script id come from the YAML.
- **Shelly 1** heat source tests (script, timeout, reboot, V2 for that model): needs the device.
- **V4 / V5** (secondary pump during hot water; the heat pump reacting to the contact): at go-live.

---

## General feedback
