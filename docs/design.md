# Floor Heating Zone Control — Design Document

> **Status: Spec rev. 1.2 — ground truth for implementation** (2026-09-27; rev. 1.1 of 2026-09-25 reviewed with the owner, see D-64…D-82)
> Phases: (1) functional spec ✅ → (2) technical design ✅ → (3) implementation with Claude Code
> "Spec rev." numbers this document; "v1 / v1.1 / v1.2" are the feature sets in §5.10, all released together as 1.0.0 (D-129).
> Integration: **Multizone Floor Heating Manager**, domain `multizone_floor_heating_manager` (D-127; the working name until P7b was `floorheat`, which older entries of this document still use)

---

## 🤖 FOR CLAUDE CODE — READ THIS FIRST

You are implementing a Home Assistant custom integration that controls an underfloor heating system zone by zone. This document is the **single source of truth**. Read all of it before writing code.

### How to work
1. **Mandatory rules in §0 override everything else.** Security and genericity are not negotiable.
2. **Follow the spec exactly.**
   - If something is ambiguous, contradictory or missing, **stop and ask the owner**. Do not guess.
   - Every decision made in a session must be written back into this document (§3–§5 and the decision log §7) in the same change (commit on `main`, D-83).
3. **Architecture (§5) is fixed:**
   - a pure-Python control core with no Home Assistant imports;
   - a thin HA adapter;
   - a reconcile loop;
   - Shelly watchdog scripts.
4. **Test first for the core.**
   - Every rule in §3 must be covered by unit tests with simulated time.
   - The acceptance scenarios in §6 are the minimum test set.
   - The core must never read the system clock itself; time is always passed in.
5. **Work in phases (§5.10, `docs/implementation-plan.md`).**
   - Implement one work phase (P0, P1, …) at a time, committing directly to `main` (D-83). No feature branches or pull requests.
   - `main` must stay green: run the local checks before every push, and fix a red CI run immediately.
   - At the end of a phase, summarise it for the owner (what was done, how it was verified, spec questions) and stop.
   - Do not start the next phase unasked.
6. **Keep the repo self-explaining:**
   - `CLAUDE.md` contains §0, the architecture summary and the working rules;
   - this spec lives in `docs/`;
   - user documentation is written alongside the code (§5.8).
7. **Environment.**
   - You may be running in a cloud sandbox without access to the owner's home network. Never ask for or store credentials to it.
   - Anything that needs real hardware is marked for the owner to verify (§8).

### First task (when starting from an empty repo)
Follow `docs/implementation-plan.md`, starting with work phase **P0** (D-82):
1. **P0:** create the repository skeleton (§5.9), `LICENSE`, `CLAUDE.md` (linking to the implementation plan), `.gitignore`, pre-commit secret scanning, the test setup and CI. This document already lives at `docs/design.md`; keep it there. *(Done: PR #1.)*
2. **P1–P3** follow one at a time, each started by the owner and committed directly to `main` (D-83): they implement the **v1 control core** (`core/`) with full unit tests, including the §6 scenarios that belong to v1. No HA code yet.
3. Every phase summary lists any spec questions that came up.

---

## ⚠️ 0. MANDATORY RULES — security & public release

> **These rules apply to every line of code, config and documentation, in every session (cloud and local).**

### 0.1 No secrets in the repository (D-49)
- **Never commit any secret to GitHub.** This includes:
  - passwords, API keys, tokens (HA long-lived tokens, healthchecks.io ping URLs/UUIDs, SMTP credentials, Shelly passwords);
  - Wi-Fi credentials;
  - IP addresses or hostnames of the private network;
  - email addresses, personal names.
- Secrets live only in HA (`secrets.yaml`, integration options) or on the devices. The repository contains only placeholders and examples (e.g. `secrets.example.yaml`).
- `.gitignore` covers secret files. Secret scanning is enabled on GitHub, plus a pre-commit secret scanner (e.g. gitleaks).
- The cloud development environment gets **no** credentials to the home system.
- If a secret is ever committed: rotate it immediately. Deleting the commit is not enough.

### 0.2 Built for other users, not only this house (D-50)
The integration may be published. Nothing specific to this installation may be hard-coded:
- **Zones:**
  - the number of zones is configurable (at least 1), not fixed at 5;
  - any zone can be marked "no valve".
- **Entities are user-mapped:** the temperature sensor, valve switch and heat source request switch are *any* HA entity of the right type (`sensor` with temperature device class; `switch`). No entity ID, device name or brand is assumed in the code.
- **Heat source agnostic:** the heat pump request is just a switch. Nothing Mitsubishi-specific is in the logic.
- **Parameters:** all have defaults and are user-changeable (§4).
- **Shelly scripts are generic:** role (valve / heat source), timeouts and failsafe window are set in a configuration block at the top of the script or in device storage, not edited in the logic.
- **Language and units:** code, comments, docs and entity names in English. Temperatures follow HA's unit system.
- **License: MIT (D-63).** The `LICENSE` file is added in the first commit. Copyright line: `Copyright (c) 2026 Vadász István (vadaszi)`. This is the only intended exception to the no-personal-names rule in §0.1. The name appears only here (needed to create `LICENSE`) and in `LICENSE`. `CLAUDE.md`, code and other docs must refer to "the copyright holder in `LICENSE`" and must not repeat the name.
- **Units (D-77):** the core computes in °C. The adapter converts to and from HA's unit system. Parameter defaults and ranges in §4 are in °C.
- **Hydraulics (D-80):** the logic assumes a flow path exists whenever the heat pump request is ON (unvalved zone, bypass, or buffer/hydraulic separator). This is a documented installation prerequisite (README). The integration logs a startup warning if every configured zone has a valve.
- **Installation data:** the values in §2 and the defaults in §4 describe **this installation**, not constants.

---

## 1. Goal

Room-temperature-based zone control for underfloor heating fed by an air-to-water heat pump, orchestrated by Home Assistant (HA). The reference installation has 4 valved zones and 1 unvalved zone.

Priorities:
1. comfort
2. long heat pump cycles (no short-cycling)
3. defined behaviour on sensor or HA failure
4. configurable schedules

---

## 2. Reference installation (as built)

*Context only. The code must not depend on any of this (§0.2).*

### 2.1 Heat source & hydraulics
| Item | Detail |
|---|---|
| Outdoor unit | Mitsubishi SUZ-SWM80VA |
| Indoor unit | Mitsubishi ERST30D-VM2EDR (cylinder unit, also produces domestic hot water) |
| Hot water | Scheduled in an afternoon window (≈ 14:00–18:00); a run takes ≈ 1–1.5 h. **The heat pump does not heat the floor water during a hot water run.** Whether the secondary pump keeps running meanwhile is unknown. |
| Flow temperature | Weather curve (trial) (D-33) |
| Buffer | Concept ZS buffer tank, used as hydraulic separator (flow-through, no coil), next to the heat pump |
| Secondary pump | DAB Evosta 2, next to the heat pump, feeds the manifold. **Switched by the heat pump:** runs only while the heat pump runs (D-29). |
| Circuits | 12 floor circuits, grouped into 5 zones |
| Zones 1–4 | 230 V normally-closed thermoelectric actuators, opening time a few minutes |
| Zone 5 | No valve, always open → guaranteed flow path; no valve lead/lag timing needed (D-03) |
| Hydraulic limitation | The 10–12 m main pipe from the pump to the manifold is undersized for 12 circuits. More open zones means less flow per circuit. This is the reason for the calling-zone priority (D-06). |
| Existing controls | Computherm thermostat system, to be removed at go-live (D-31) |

### 2.2 Control devices
| Function | Device | Qty | Notes |
|---|---|---|---|
| Valves zones 1–4 | Shelly Plus 2PM (SNSW-002P16EU), Gen2 | 2 | Switch profile; scripting + power metering (D-04) |
| Heat pump request | Shelly 1 Gen3 or Gen4, standard (to buy; Gen4 in Wi-Fi mode) | 1 | Potential-free contact on the heat pump terminals the Computherm uses today; scripting-capable (D-31, D-32) |
| Room temperature | Xiaomi LYWSD03MMC, pvvx firmware, BTHome | 5 | Already working in HA; one per zone |
| Controller | Home Assistant OS on Lenovo ThinkCentre M710q | 1 | Central in the house; built-in Bluetooth receives all sensors |
| Heat pump data | MELCloud integration | – | Unreliable / stale values: logging only, never used for control |

### 2.3 Installation
- The Shelly 1 sits next to the heat pump, in the Computherm's current place.
- The 2PMs go either next to the heat pump (driving the actuators via the existing long cables) or at the manifold (powered via those cables). Wi-Fi coverage decides.

---

## 3. Functional specification

### 3.1 Terms (per zone)
| Name | Meaning |
|---|---|
| `RoomTemp` | Zone temperature: sensor reading + per-zone calibration offset |
| `BaseSetPoint` | The user's normal desired temperature for the zone |
| `SetPoint` | Effective desired temperature at this moment, after holiday/schedule rules (§3.4) |
| `Hysteresis` | Allowed deviation around SetPoint |
| `StartTemp` | `SetPoint − Hysteresis`: at or below this the zone asks for heat |
| `StopTemp` | `SetPoint + Hysteresis`: at or above this the zone stops heating |
| `WaitTime` | Open-window filter: delay before a zone may start the heat pump |
| Heat pump request | The switch that asks the heat source for heat. "Heat pump running" in this document always means **the heat source switch actually reports ON** (D-66). This is feedback of the request only. The heat pump may still stop its compressor internally, and there is no compressor feedback. An unavailable switch counts as OFF while it is unavailable; when it reports again, D-95 decides whether it ever stopped. In shadow mode the commanded state stands in for the actual state (§5.5). |
| Cycle | The period from heat pump request ON to heat pump request OFF |
| Calling zone | The zone whose demand switched the heat pump request ON in the current cycle (at most one per cycle) |
| Unvalved zone | A zone configured without a valve (zone 5 in the reference installation) |

Example: SetPoint 22.0 °C, Hysteresis 0.2 °C → StartTemp 21.8 °C, StopTemp 22.2 °C.

### 3.2 Zone states
| State | Meaning | Valve |
|---|---|---|
| `IDLE` | No demand | closed |
| `WAITING` | RoomTemp ≤ StartTemp while the heat pump is off; WaitTime running | closed |
| `HEATING` | Thermostatic demand | open (also while the heat pump request is still held back by HpMinOffTime, D-64) |
| `FORCED` | Manual schedule active (§3.4) | open, except while at or above ManualMaxTemp (closed, no demand) |
| `SENSOR_FAULT` | No valid reading for > SensorFaultTimeout | Follows the house: open whenever the heat pump runs; creates no demand itself (D-27). Takes precedence over `FORCED` (D-70) |

Additional valve overrides:
- the min-ON rule (§3.5);
- failsafe (§3.6);
- valve exercise (§3.7).

Unvalved zones compute state normally; only their output is a no-op.

### 3.3 Zone logic
1. **Call with wait.** If the heat pump is off and RoomTemp ≤ StartTemp, the zone enters `WAITING` and its WaitTime starts.
2. **Single check at end of wait (D-05).** When WaitTime expires, only the current temperature is checked; readings during the wait are ignored.
   - RoomTemp ≤ StartTemp → `HEATING`. The valve opens immediately. The heat pump request goes ON (subject to §3.5), and the zone becomes the calling zone.
   - Otherwise → `IDLE`.
   - **SetPoint lowered during the wait (D-94):** if a SetPoint decrease leaves RoomTemp above the new StartTemp, the wait ends at once (`IDLE`). This mirrors rule 4; D-05 still applies to readings.
   - **Held back by HpMinOffTime (D-64):** the zone stays `HEATING` with its valve open until HpMinOffTime elapses, then the request goes ON. If RoomTemp reaches StopTemp in the meantime, the zone goes `IDLE` (rule 6) and nothing starts.
3. **Join while running (D-14).** If the heat pump is running, any zone with RoomTemp ≤ StartTemp goes to `HEATING` immediately, without WaitTime. This includes zones in `WAITING`. WaitTime exists only to avoid *starting* the heat pump because of a short window opening.
4. **SetPoint raised (D-26).** If a schedule change, holiday end or user change raises SetPoint so that RoomTemp ≤ StartTemp, the zone goes to `HEATING` immediately, without WaitTime.
   - HpMinOffTime (§3.5) still applies; the zone waits as in rule 2 (D-64).
   - If this starts the heat pump, the zone becomes the calling zone.
   - **Several candidates (D-65):** if more than one zone would become the calling zone in the same step (e.g. WaitTimes expire together, holiday end, a schedule raising several zones, or several zones waiting for HpMinOffTime), the zone with the largest `StartTemp − RoomTemp` becomes the calling zone. Ties go to YAML order. The other zones are simply `HEATING`.
   - A raise is detected by comparing the effective SetPoint with the one of the previous step, which is kept in the logic state. With no previous value (first start), rule 1 applies.
5. **Sync rule (D-06, D-15).** When the calling zone reaches its SetPoint (RoomTemp ≥ SetPoint), every `IDLE` or `WAITING` zone with a valid sensor and RoomTemp < StopTemp joins `HEATING`.
   - The rule fires **once per cycle**.
   - Rationale: because of the thin main pipe, the zone that called gets full flow first. The other zones are topped up afterwards, so every zone ends the cycle near StopTemp and none re-triggers shortly after.
   - **Cycle started by a manual schedule (D-44):** the forced zone is not a calling zone.
     - The first zone that joins by temperature during that cycle (rule 3) becomes the calling zone, and the sync rule applies normally.
     - If no zone joins, there is no sync rule in that cycle.
   - **Calling zone's sensor fails (D-28):** it counts as having reached SetPoint, so the sync rule fires.
   - **Cycle bookkeeping:** the calling zone and the "sync fired" flag are cleared whenever the request is OFF, including while demand is held back by HpMinOffTime. When HpMinOffTime elapses, the waiting `HEATING` zones compete as in D-65.
   - **No calling zone while the request is ON (D-92):** e.g. first start with the heat pump already running, or after the stored state was discarded. The `HEATING` zone with the largest `StartTemp − RoomTemp` (ties by YAML order) becomes the calling zone. D-44 applies the same rule to a manually started cycle.
6. **Switch-off.** `HEATING → IDLE` when RoomTemp ≥ StopTemp. This also applies after a SetPoint decrease.
7. **Heat pump request.** ON while any zone is `HEATING`, or `FORCED` with demand, subject to §3.5. OFF when none is.
   - "Heat pump running" in rules 1–3 and all HP timers use the actual switch state (D-66, §3.1).
8. **Unvalved zone.** Identical logic; it can create demand but its valve output is a no-op.

### 3.4 Modes & schedules
**Precedence for the effective SetPoint and valve control, highest first (D-16):**
1. Failsafe
2. Holiday
3. Manual schedule
4. Auto schedule
5. BaseSetPoint

**Holiday mode (D-09, D-17, D-59, D-133, D-137)**
- The user activates it, normally with an end date and time. It starts immediately on activation and can be stopped manually at any time. It is active while it is switched on and the current time is before the end (D-136).
- **Details (D-137):**
  - the end is a full date and time, so a holiday can last any number of days;
  - switched on without an end, holiday runs until it is switched off by hand;
  - switching it on with an end already in the past is refused with an error;
  - changing the end while holiday runs moves it: a later time extends it, a time in the past ends it at once;
  - every end, reached or switched off by hand, clears the end, so the next holiday starts from an empty end.
- While active, the effective SetPoint of **every** zone is **its own** `HolidayTemp` (per zone since D-133; each zone's value may be above or below its BaseSetPoint). BaseSetPoints are not modified, so "restore the previous setup" is automatic when holiday ends.
- Manual and auto schedules are suspended during holiday.
- No automatic preheat: the user sets the end time early enough.
- At holiday end, raised SetPoints start heating immediately (rule 3.3.4).

**Manual schedule (D-18, D-38, D-58)**
- A manual schedule covers one or more zones plus a time window. The zone is `FORCED`: valve open and heat demand regardless of RoomTemp.
- One-shot (date + window) or recurring (daily / selected weekdays + window).
- **Safety cap:** at RoomTemp ≥ `ManualMaxTemp` the forced zone closes and drops its demand. It resumes only when RoomTemp < ManualMaxTemp − `ManualResumeDelta`.
- While a manual schedule runs the heat pump, other zones with RoomTemp ≤ StartTemp join (rule 3.3.3).
- Manual schedules for the same zone may overlap; the result is the union of their windows.
- When the window ends, the zone returns to normal logic immediately.
- **Faulty sensor (D-70):** a zone in `SENSOR_FAULT` is not forced, because the safety cap cannot be checked. It stays `SENSOR_FAULT` (follows the house, no demand).
- **Details (D-130, D-131):**
  - a manual schedule has no temperature: the SetPoint below it (auto schedule, otherwise BaseSetPoint) stays in force, is shown as the effective SetPoint and applies again when the window ends;
  - a zone without any valid reading yet (D-93) is not forced either (the cap cannot be checked); it becomes `FORCED` with its first valid reading inside the window;
  - the cap engages at RoomTemp ≥ ManualMaxTemp, also when the window starts at that temperature; a capped zone stays closed during the D-20 spread, even below ManualMaxTemp, until it has resumed;
  - forced demand is subject to §3.5 like `HEATING` (held by HpMinOffTime with the valve open, D-64); no WaitTime applies;
  - when the window ends, the zone is `IDLE` and evaluated in the same step: it joins a running heat pump if RoomTemp ≤ StartTemp (rule 3), enters `WAITING` if the heat pump is off, otherwise stays `IDLE`;
  - a forced zone is never the calling zone (D-44). If the calling zone becomes forced mid-cycle, it loses the role; the `HEATING` zone with the largest `StartTemp − RoomTemp` takes it (D-92), otherwise the next zone that joins (D-131);
  - outside the heating season a zone in a manual window is `IDLE` (D-97); holiday suspends manual schedules.

**Auto schedule (D-19)**
- An auto schedule overrides the SetPoint for one or more zones during a time window.
- One-shot or recurring. Examples:
  - daily, zone 1, 13:00–17:00 → 23 °C;
  - Sunday, all zones, 10:00–16:00 → 21 °C.
- A new auto schedule that overlaps an existing auto schedule for the same zone is **rejected** at creation, with a clear error message.
- When the window ends, SetPoint returns to BaseSetPoint.
- The temperature range is BaseSetPoint's, 10–30 °C (D-132).

**Schedule times (D-57, D-132, D-134)**
- Local wall-clock time in HA's time zone, DST-aware.
- Windows may cross midnight (e.g. 22:00–02:00).
- One-shot schedules (manual and auto) are deleted automatically after their window ends.
- **Details (D-132):**
  - a window is half-open: 13:00–17:00 is active from 13:00 until just before 17:00; windows that touch (10:00–12:00 and 12:00–14:00) do not overlap;
  - an end before the start crosses midnight; start = end is rejected;
  - a recurring window belongs to the weekday it starts on ("Sunday 22:00–02:00" = Sunday night into Monday), a one-shot window to its date; "daily" = all seven weekdays;
  - the overlap check (D-19) compares local wall-clock times on the days both schedules apply;
  - a one-shot schedule whose window is already over is rejected at creation;
  - a schedule covers a list of zones or **all zones**; "all zones" is kept as such, so it also covers zones added to the YAML later. At startup a zone no longer configured is removed from every schedule, and a schedule left without zones is deleted, both with a logged warning;
  - DST (D-96, D-134): a time in the repeated autumn hour is its first occurrence; a time in the spring gap is shifted by the gap length (02:30 → 03:30), so a window keeps its wall-clock length. A window whose start ends up at or after its end is empty that day.

### 3.5 Heat pump protection (D-07, D-20, D-30, D-39, D-64, D-66, D-68, D-71, D-78)
- `HpMinOnTime` and `HpMinOffTime` are user-configurable (default 60 / 60 min, range 30–180 min). The purpose is to stop the heat pump switching on or off too often, so neither can be set below 30 min (D-81). The config validation and the number entities enforce this.
- Both timers count from the **actual** switch transitions (D-66). An unavailable switch counts as OFF, so a switch that becomes unavailable starts the OFF time.
- **Switch unavailable, then back (D-95):** a Wi-Fi glitch or router restart must never switch a working heat pump OFF.
  - While unavailable: the switch counts as OFF (no join by rule 3, min OFF counted from when it became unavailable). The calling zone and the sync flag are kept, because the heat pump may still run. Commands cannot reach the switch.
  - Back **ON** after being ON: it never stopped, because a Shelly that lost power restarts OFF (power-on default, §5.4). HpMinOnTime and the cycle continue unchanged.
  - Back **OFF** after being ON: it stopped when it became unavailable. That is the OFF time, and the cycle has ended.
  - Back ON after being OFF: a normal ON transition at that moment.
- **All zones satisfied before HpMinOnTime has elapsed (D-20, D-71):** the request stays ON and all valves open until HpMinOnTime elapses. Zones with RoomTemp ≥ ManualMaxTemp are excluded and stay closed. The remaining heat is spread over the house. Then the request goes OFF and valves follow normal logic.
- **A zone calls before HpMinOffTime has elapsed:** the request waits until HpMinOffTime has elapsed. The zone is `HEATING` with its valve open in the meantime (D-64).
- **WaitTime and HpMinOffTime run in parallel.** The request goes ON when both have elapsed and the WaitTime check passed.
- **Heating season switched OFF (D-68, D-97):** the request goes OFF and valves close immediately, even if HpMinOnTime has not elapsed. HpMinOffTime counts from that OFF when the season is switched ON again.
- **First start (D-78):** with no persisted last-OFF time, HpMinOffTime is not applied.
- **First start with the heat source already ON (D-91):** with no persisted last-ON time, HpMinOnTime counts from startup. If no zone has demand, the D-20 spread runs until it elapses.

### 3.6 Failure handling
**Sensor fault (D-08, D-21, D-27, D-28)**
- No valid reading for > `SensorFaultTimeout` (60 min) → the zone enters `SENSOR_FAULT`, regardless of its previous state.
  - "Valid" means a numeric, plausible value received within the timeout. The plausibility range is *config*, default 0–40 °C (D-77). It is checked against the raw sensor reading, before the per-zone offset (D-88). Implausible values are ignored, as if nothing had been received.
  - The last-report time is used, not the last-change time (§5.3).
  - Until the timeout, RoomTemp is the last valid reading (plus offset). A short dropout therefore changes nothing.
  - **No valid reading since startup (D-93):** with nothing persisted either, the timeout counts from startup. Until then the zone is `IDLE` with no demand (reason "Waiting for a sensor reading").
- While in fault, the zone follows the house and creates no demand.
- On a valid reading again → the zone returns to normal logic (`IDLE`, then evaluated in the same step).
- **Notifications:** when the fault starts, then a daily reminder at 08:00 while any sensor is faulty, and on recovery.
  - Outside heating season (D-75): fault start and recovery only; no daily reminder.
  - **Details (D-98):**
    - start and recovery are notified when the zone's state changes to or from `SENSOR_FAULT`. A fault already in the stored state is not notified again after a restart; a fault that began while HA was down is notified when detected.
    - They are sent in and outside the season, and also in shadow mode (only the D-67 alert is inactive there).
    - The reminder is **one** notification per local day listing every zone that has been faulty since an earlier local day. The first reminder therefore comes at 08:00 the day after the start notification; a fault that starts at 07:59 is not reminded one minute later.
    - It is due from SensorFaultReminder until local midnight and is sent at most once per day. If HA is not running at 08:00, or the season is switched ON later that day, it is sent late (catch-up) that day.

**Failsafe mode (D-12, D-22, D-35, D-36, D-37)**
- **Trigger** (either condition):
  - no valid temperature from *any* sensor for > 24 h; or
  - no HA heartbeat for > 24 h (detected on the Shellys).

  As long as at least one sensor is valid, normal control continues.
- **Action:** all valves open and heat pump request ON daily 10:00–15:00. Only in heating season.
- **Case 1 — HA alive, all sensors dead:** HA runs the failsafe through its normal control of the outputs. The notification is sent by HA.
- **Case 2 — HA dead:** each Shelly runs a local script that watches for the HA heartbeat. There is no device-to-device communication.
  - **Valve Shellys (2PM):** after `HeartbeatTimeout` (5 h) without a heartbeat, switch **all valves ON (open)** and keep them open. No clock or schedule is needed; an open valve with the heat pump off has no effect apart from the actuators' holding power.
    - "All valves" = every switch component of the device, unless the script's configuration lists the channels (D-104).
    - After a reboot or a script restart without a heartbeat, the start counts as the last heartbeat, as in D-72: the valves stay at the power-on default (closed) until HeartbeatTimeout has passed since the start (D-102).
  - **Heat source Shelly (Shelly 1):**
    - after `HeartbeatTimeout` (5 h) without a heartbeat, switch OFF;
    - the 5 h timeout is deliberately long: it gives the owner time to fix HA before the Shellys act (D-60);
    - after `FailsafeTrigger` (24 h) without a heartbeat, request heat daily 10:00–15:00 by its clock (NTP);
    - if the clock is invalid (power cut and no internet; Shellys have no backup clock), fall back to a cycle based on its own uptime: 5 h ON, 19 h OFF;
    - **after a reboot without heartbeat (D-72):** the time of the last heartbeat is lost, so the boot time counts as the last heartbeat. The output stays OFF (power-on default) until FailsafeTrigger has passed since boot. Then the clock window applies, or, if there is still no valid time, the uptime cycle starts with its 5 h ON phase at uptime = FailsafeTrigger;
    - it heats only if the last heartbeat said "heating season ON" (the flag is stored on the device). A flag that was never set counts as OFF (D-105).
  - **Timed out (D-103):** a script that has timed out re-asserts its safe outputs (valves ON, heat source OFF) at every check if something else switched them. When a heartbeat returns, it stops acting at once and switches nothing; the reconcile loop sets the outputs.
  - The timeouts, failsafe window and uptime cycle used by the scripts are set only in each script's configuration block or device storage (D-73). HA's own FailsafeTrigger/FailsafeWindow (§4) apply only to case 1.
  - Only the heat source Shelly has time logic, so no time alignment between devices is needed.
  - The notification comes from the external watchdog (healthchecks.io).
- **Exit:** as soon as a heartbeat or a valid reading is back, normal operation resumes immediately. The reconcile loop sets all outputs.
- The Computherm system is rejected as a fallback, because a window opening would trigger it.

**Notifications (D-23)**
| Event | Channels |
|---|---|
| Sensor fault started / daily 08:00 reminder / sensor recovered | push + email |
| Failsafe entered / left (case 1) | push + email |
| Heat pump request ON > `LongRunAlarm` | push + email |
| Shelly unreachable / watchdog script not running (§5.4) + recovery | push + email |
| Output not following command (§3.9) + recovery | push + email |
| Shelly script parameters differ from HA's expected values (§5.4) | push + email |
| HA or integration dead | email from healthchecks.io |

Push goes to the HA companion app; email via HA's SMTP notify. The notify targets are user-configured.

### 3.7 Season & maintenance
- **Heating season (D-24):** a manual on/off switch.
  - OFF: no heating demand at all.
  - The failsafe heats only in heating season (D-37).
  - Switching it OFF mid-cycle stops the heat pump request immediately, ignoring HpMinOnTime (D-68).
  - Manual and auto schedules create no heating while it is OFF.
  - **Details (D-97):**
    - sensor fault detection and SetPoint tracking keep running;
    - every zone not in `SENSOR_FAULT` is `IDLE`; a running WaitTime ends;
    - the calling zone and the sync flag are cleared, also while the heat source switch is unavailable;
    - the request is OFF and every valve is closed, faulty zones included; this overrides min ON and the D-20 spread;
    - the HP timers keep following the actual switch, so HpMinOffTime counts from the actual OFF;
    - reason texts: "Heating season off"; a faulty zone shows "Sensor fault (heating season off)";
    - switching it ON again is not a SetPoint raise (D-26): zones start from `IDLE` under the normal rules (a cold zone enters `WAITING`).
- **Valve exercise (D-10, D-25, D-37):** only outside the heating season.
  - Weekly, Monday 08:00; each valve opens for 15 min, one after another; heat pump off.
  - No flow is needed; the goal is mechanical movement of the actuators.

### 3.8 Restart behaviour (D-40)
- After an HA restart, the control state is restored.
- Output states (valves, heat pump request) are read back from the switch entities.
- Logic state lives only in HA and is persisted there, keyed by the zone `id` (D-76):
  - running timers;
  - time of the last heat pump request ON/OFF;
  - calling zone and whether the sync rule has fired;
  - holiday state and end time;
  - schedules (holiday and schedules are owned and stored by the adapter, like the UI settings, and passed to `step` as inputs, D-136);
  - FORCED cap state;
  - the values changed from the UI: parameters, heating season, Control active (D-106).
- **Persistence format (D-87):**
  - the stored data carries a schema version; datetimes are stored as ISO 8601 in UTC;
  - new fields get a default when missing, so adding one needs no version bump; a breaking change bumps the version and adds a migration;
  - data from a newer version (e.g. after a downgrade) or corrupt data is discarded with a logged warning, and the integration starts as on a first start (D-78);
  - stored state of zones that are no longer configured is dropped (with a warning), new zones start `IDLE`, and a calling zone that is no longer configured is cleared.
- **Adapter storage (D-106):** one `helpers.storage.Store` file holds the core state, the UI settings (parameters, heating season, Control active) and the switches still owed the final OFF (D-110). The adapter owns the settings; the entities only show and change them. Unusable settings fall back to their defaults with a warning. Saves are delayed and coalesced (at most one write per 30 s) and flushed when HA stops.

### 3.9 Monitoring
- **Output not following command (D-67):** notify if an output's actual state differs from the desired state, or the entity is unavailable, for `OutputMismatchAlert` consecutive reconcile intervals (default 3). Notify again on recovery. The reconcile loop keeps retrying with backoff instead of sending a command every interval. Not active in shadow mode.
  - **Counting (D-99):** the core counts reconcile ticks, not steps. The adapter marks the run started by the ReconcileInterval timer (`reconcile_tick`); runs on sensor updates or heat source changes do not count, and a repeated step at the same time counts once. Per output (heat source, each valve), on each tick:
    - unavailable → counts;
    - actual ≠ desired, with the same desired state as at the previous tick → counts (the command had a full interval);
    - actual ≠ desired right after the desired state changed → count 0 (a new command that has not been sent yet), so normal switching never alerts;
    - available and equal → count 0, and a recovery notification if the alert was sent.
  - The alert is sent once when the count reaches OutputMismatchAlert. The heat source counts while unavailable although D-95 keeps the cycle.
  - In shadow mode the counters are reset without notifications.
- **Long run alarm (D-42):** notify if the heat pump request is ON > 12 h.
- **Overshoot logging (D-13):** per zone, track the peak RoomTemp from switch-off until the zone next has demand (at most 6 h). Emit it as an HA event and expose the last value as a zone attribute. This is data for v2.

### 3.10 Hot water production (D-45)
- No logic change. The heat pump request may stay ON during a hot water run; the heat pump resumes space heating afterwards.
- **Known effects, accepted:**
  - zones in `HEATING` take longer to reach StopTemp;
  - HpMinOnTime also counts time spent on hot water.

### 3.11 Deferred to v2
- Overshoot learning: switching off early based on the logged overshoot.

---

## 4. Parameters

All are exposed as HA entities (changeable from the UI) unless marked *config* (YAML) or *script config* (the Shelly script's configuration block or device storage, D-73). Values are the defaults. Ranges are the number entity limits (°C; converted by the adapter, D-77). Validation checks the inclusive range only; the step is the number entities' UI granularity, and off-step values (e.g. 32 min) are accepted (D-86).

| Parameter | Scope | Default | Range / step | Notes |
|---|---|---|---|---|
| BaseSetPoint | per zone | 22.0 °C | 10–30 °C / 0.1 | climate entity target |
| Hysteresis | per zone | 0.2 °C | 0.1–1.0 °C / 0.1 | |
| WaitTime | per zone | 30 min | 0–120 min / 5 | |
| Sensor offset | per zone | 0.0 °C | −5–+5 °C / 0.1 | *config* |
| HpMinOnTime | global | 60 min | 30–180 min / 5 | Never below 30 min: protects the heat pump from short-cycling (D-81) |
| HpMinOffTime | global | 60 min | 30–180 min / 5 | Never below 30 min: protects the heat pump from short-cycling (D-81) |
| SensorFaultTimeout | global | 60 min | 15–240 min / 5 | |
| SensorFaultReminder | global | 08:00 daily | time of day | Not sent outside heating season (D-75) |
| Plausible temperature range | global | 0–40 °C | | *config* (D-77) |
| ManualMaxTemp | global | 25 °C | 18–30 °C / 0.5 | Also the D-20 exclusion limit (D-71) |
| ManualResumeDelta | global | 1.0 °C | 0.2–3.0 °C / 0.1 | |
| HolidayTemp | per zone | 18 °C | 10–25 °C / 0.5 | Same 10 °C floor as BaseSetPoint; per zone since D-133 (was global) |
| FailsafeTrigger | global | 24 h | 1–72 h / 1 | HA case 1 only; the Shelly value is *script config* (D-73) |
| FailsafeWindow | global | 10:00–15:00 | time of day | HA case 1 only; the Shelly value is *script config* (D-73) |
| HeartbeatTimeout | Shelly | 5 h | | *script config* (D-60, D-73, D-101); HA's expected value is *config* `heartbeat_timeout` (D-121) |
| Watchdog check interval (Shelly) | Shelly | 60 s | | *script config*; the timeout is acted on within one check (D-102); compared only if *config* `heartbeat_check_interval` is set (D-121) |
| FailsafeTrigger / FailsafeWindow / uptime cycle (Shelly) | Shelly | 24 h / 10:00–15:00 / 5 h ON, 19 h OFF | | *script config* (D-72, D-73) |
| HeartbeatInterval | global | 5 min | 60–3600 s | *config* `heartbeat_interval`; shorter than `heartbeat_timeout` |
| HeartbeatFailAlert | global | 3 consecutive failed calls (≈ 15 min) | ≥ 1 | *config* `heartbeat_fail_alert` |
| ReconcileInterval | global | 60 s | | *config* |
| OutputMismatchAlert | global | 3 consecutive reconcile intervals | | *config* (D-67) |
| WatchdogPingInterval | global | 5 min | | *config*; ping URL is a secret |
| ValveExercise | global | Mon 08:00, 15 min/valve | weekday, time, 5–30 min / 5 | |
| LongRunAlarm | global | 12 h | 2–48 h / 1 | |
| Heating season | global | ON | | switch |
| Control active (shadow mode) | global | OFF on first install | | switch, §5.5 |

---

## 5. Technical design

### 5.1 What runs where
| Where | What | Technology |
|---|---|---|
| HA (ThinkCentre M710q) | Control logic, entities, notifications, state persistence | Custom HA integration (Python): pure control core + thin HA adapter |
| HA (built-in) | Sensor reception | Bluetooth + BTHome integration (existing) |
| HA (built-in) | Output control | Shelly integration (local) via switch entities |
| Valve Shellys (2PM ×2) | Heartbeat watchdog → open all valves after 5 h without a heartbeat | Shelly script (JavaScript, on device) |
| Heat source Shelly (Shelly 1) | Heartbeat watchdog → OFF after 5 h; failsafe window after 24 h (uptime fallback) | Shelly script (JavaScript, on device) |
| healthchecks.io | "HA / integration dead" alert | Hosted push monitor (D-48), pinged by the integration |
| GitHub | Code, docs, releases; shared memory between Claude Code sessions | Public repo, HACS custom repository (D-46) |
| ThinkPad T14 (WSL) / Claude Code on the web | Development and tests | Python, pytest |

### 5.2 Why a custom integration (alternatives considered)
| Option | Verdict | Reason |
|---|---|---|
| HA automations (YAML) + helpers | Rejected | Calling zone, sync rule, precedence and persistence become hundreds of lines of YAML/templates; not unit-testable |
| Node-RED | Rejected | Extra add-on, logic ends up in JavaScript function nodes anyway, weak testing |
| AppDaemon (Python apps) | Fallback option | Real Python, but entities are not native (no state restore) and time simulation needs a custom harness |
| External service (MQTT) | Rejected | More moving parts; would still depend on HA for sensors and switches |
| **Custom integration (Python)** | **Chosen** | Native entities, services, state storage; core testable without HA; Python is HA's native language |

### 5.3 Integration structure
**Control core (`core/`) — pure Python, no HA imports:**
- configuration and state models (dataclasses);
- a single deterministic step function:
  `step(config, state, inputs, now) → (desired_outputs, new_state, events)`
  - `inputs`: per-zone temperature + last-report time, actual output states (unavailable = OFF; in shadow mode the adapter passes the commanded states, D-66), parameter values, schedules, holiday, season, control-active flag;
  - `inputs` also carry whether this run is a reconcile tick (D-99) and HA's time zone (D-96). `now` may be in any time zone; the core converts it for local wall-clock rules (the daily reminder, schedules). Local times are compared as aware datetimes: a time inside the spring DST gap is shifted by the gap length (02:30 → 03:30, D-134), and one in the repeated autumn hour takes effect at its first occurrence;
  - `inputs` also carry the schedules, whether holiday is switched on and its optional end, which the adapter owns and stores (D-136, D-137). `desired_outputs` report whether holiday is active and which one-shot schedules have ended; the adapter then switches holiday off and deletes them;
  - `desired_outputs`: per-zone valve on/off, heat pump request on/off, and the per-zone reason shown by the reason sensor (D-89): a fixed key (D-126) with the end of the timer it names (D-123);
  - `events`: notifications and log entries;
- time is always passed in; the core never reads the clock;
- fully covered by unit tests (§6).

**Reconcile loop:**
- Runs every `ReconcileInterval` (60 s) and on every sensor update.
- Calls `step`, then compares the desired outputs with the actual switch states and corrects any difference. A lost command or a device reboot heals itself within one interval.
- It is idempotent: running it twice changes nothing.
- If an output keeps differing from the desired state, retries back off, and the "output not following command" alert fires (D-67, §3.9).
- **Details (D-107 to D-109):**
  - runs start on the ReconcileInterval timer (the only reconcile ticks, D-99), on a state change of any mapped sensor or switch, and on a settings change; an `asyncio.Lock` serialises them;
  - every run calls `step` with the current actual states before any command, so no stale command is sent (e.g. an OFF to a heat source that returns from `unavailable`, D-95);
  - backoff (D-108): the first command for a desired state goes out at once; while the switch does not follow, retries follow after 1, 2, 4 and 8 min, then every 15 min. The count resets when the switch follows or the desired state changes. Nothing is sent or queued while a switch is unavailable; the first command after it returns goes out at once;
  - commands run as tasks with a 30 s timeout, outside the lock;
  - the loop starts once HA has started (`async_at_started`), so entities that are still loading neither get commands nor count for the mismatch alert (D-109).

**Adapter (HA side):**
- **Sensors:** reads sensor state and **`last_reported`** for staleness. `last_updated`/`last_changed` do not move when a value repeats, so a healthy sensor would look dead.
  - The reading is converted from the sensor's own `unit_of_measurement`; a sensor without a temperature unit gives no valid reading, and a warning is logged once (D-111).
  - A switch state other than `on`/`off` (unavailable, unknown, missing) is unavailable (D-66).
- **Outputs:**
  - calls `switch.turn_on/turn_off` only through the reconcile loop;
  - unvalved zones have no output;
  - in shadow mode, no output commands.
- **Persistence:** logic state in HA storage (`helpers.storage.Store`), restored at startup (§3.8).
- **Notifications:** through the notify services listed in the YAML config.
  - **Targets (D-117):** YAML `notify` lists `notify.<name>` targets. A legacy notify service of that name is called with title and message; otherwise the notify entity with that id gets `notify.send_message`. Calls run as tasks with a timeout; failures are logged and never stop the control. Titles (D-127): "Floor heating: sensor fault" / "sensor fault reminder" / "sensor recovered" / "switch not following command" / "switch following again" / "Shelly watchdog not answering" / "Shelly watchdog answering again" / "Shelly script parameters differ"; the message is the core event text. Targets that don't exist are reported after HA has started (warning + persistent notification). Without targets, events are only logged.
- **Heartbeat and watchdog:**
  - sends the heartbeat to the Shellys (§5.4);
  - pings healthchecks.io every `WatchdogPingInterval` (5 min).
  - The check on healthchecks.io is configured with period 5 min and grace ≈ 30 min (D-62). It alerts about 30–35 min after the last ping, so HA updates and restarts (typically 5–20 min) don't cause false alarms. healthchecks.io also notifies when pings resume.
- **Async only:** the adapter never blocks HA's event loop.

**Entities exposed:**
- **Per zone:**
  - climate entity (current temp = RoomTemp, target = BaseSetPoint, hvac_action heating/idle; hvac_modes: `heat` only, no per-zone off);
  - state sensor (`IDLE`/`WAITING`/…);
  - reason sensor (enum of fixed keys, shown as translated texts, D-126);
  - effective SetPoint sensor;
  - Hysteresis and WaitTime number entities.
- **Global:**
  - heat pump request binary sensor with ON-duration attribute;
  - mode sensor (normal / holiday / failsafe). Shadow mode is orthogonal: it is shown by the control-active switch and a `shadow` attribute on the mode sensor (D-79);
  - switches: heating season, control active;
  - number entities for the global parameters;
  - alerts sensor (count + list).
- **Details (D-114 to D-116):**
  - **devices and names (D-125, replaces the fixed ids of D-115):** one device per zone, "<zone name> floor heating", and one "Floor heating" device for the global entities (device type *service*). Entities follow HA's naming conventions: `has_entity_name`, the entity name names only the value ("Reason", "Hysteresis"), the climate entity has no name of its own (it is the zone device), names and state texts come from translations, settings carry the *config* entity category. HA generates the entity ids from device name + entity name when an entity is first registered (e.g. `climate.living_room_floor_heating`, `sensor.living_room_floor_heating_reason`, `switch.floor_heating_heating_season`); later renames don't change them. Unique ids are built from the zone id and the key (e.g. `living_room_reason`, `heat_request`). Zone devices are assigned to areas in the UI (no YAML key). Full list: [`configuration.md`](configuration.md#entities);
  - every §4 global parameter has its number entity from v1, including those whose features come in v1.1/v1.2; the docs say from which release each is used (D-114). SensorFaultReminder is a time entity;
  - the entities are views of the adapter's settings and state (D-106): unavailable until the first reconcile run, except the settings (switches, numbers, time), which can be changed at once;
  - climate `hvac_action` is *heating* while the heat source request is ON and the zone gets flow (valve open, or no valve), otherwise *idle*; attributes `zone_state`, `reason`, `valve` (desired), `calling_zone` (D-116);
  - the heat request binary sensor is ON with the desired request (in shadow mode the simulated one); attributes `on_since` (last actual, or in shadow mode commanded, ON) and `on_duration` in minutes, excluded from the recorder (D-116), both present only while the heat source runs (D-123);
  - **no per-minute countdowns (D-123):** no entity state changes every minute only because time passes. Reasons are fixed keys (D-126; e.g. `waiting` shown as "Waiting period", `held_by_minimum_off_time`, `spreading_heat`); the reason sensor has an `until` attribute (aware ISO timestamp, the end of the running wait, min OFF or min ON timer) only while such a timer runs. The climate entity's `reason` attribute holds the same key;
  - temperatures: climate, effective SetPoint and absolute temperature numbers are in °C and converted by HA; temperature differences (Hysteresis, ManualResumeDelta) are converted by the adapter to HA's unit system, because HA converts only absolute temperatures (D-77);
  - the alerts sensor derives its list from the core state (`active_alerts`): faulty zones and outputs whose mismatch alert was sent.
- **Holiday (D-79, D-137):** HolidayTemp number per zone (D-133), "Holiday end" date/time entity (empty until set, cleared when holiday ends), "Holiday" switch.
- **Schedules:** managed through integration services (add / delete / list) with validation. The list is exposed as a sensor attribute for the dashboard.
  - **Services (D-139):** `add_schedule` (type, zones as YAML zone ids or `all`, a date or weekdays `mon`…`sun`, start, end, temperature for auto schedules in HA's unit system; returns the new schedule), `delete_schedule` (the schedule number), `list_schedules` (response only). A rejected schedule raises a validation error naming the problem, and nothing is stored;
  - the "Schedules" sensor: state = number of schedules, attribute `schedules` with id, label, type, zones, date or weekdays, start, end and temperature (D-139);
  - every schedule gets a number (`#1`, `#2`, …) that is never reused and a label built from its content, e.g. `#3 Auto · Living room · Every day 13:00–17:00 · 23.0 °C`; there is no name field (D-138).
- **Schedule form entities (D-74):** the integration provides its own draft entities so the dashboard needs no user-created helpers: type (auto/manual) and zone selects, one-shot date or weekday selection, start/end time, temperature, an "Add schedule" button, plus a select of existing schedules and a "Delete schedule" button. The buttons call the same validated logic as the services, and errors (e.g. overlap, D-19) are shown as a persistent notification.
  - **Details (D-138):** the zone select offers "All zones" or one zone, the "Days" select offers once (on the form's date), every day, Monday–Friday, Saturday–Sunday or a single weekday; other combinations only through the service. The draft values stay after "Add schedule" (for adding a similar one) and return to their defaults after a restart (they are not stored). The "Existing schedule" select lists the labels.

### 5.4 Heartbeat mechanism
- Each Shelly script registers a small local HTTP endpoint. **Protocol v1 (D-100):** [`heartbeat-protocol.md`](heartbeat-protocol.md).
  - `POST /script/<id>/heartbeat` is the heartbeat (JSON body; the heat source's carries `season`); `GET` on the same path returns the status without counting as a heartbeat.
  - The response carries a protocol version `v`. Added fields never change it and both sides ignore unknown fields; a breaking change raises it.
  - A request that is not valid (e.g. a heat source heartbeat without a boolean `season`) gets `400` and is not a heartbeat (D-105).
- The integration calls every Shelly every `HeartbeatInterval` (5 min). For the heat source Shelly, the call carries the heating-season flag.
- **Shelly wiring from YAML (D-120):** the YAML key `shellys` lists every Shelly running a watchdog script: `host` (address), `script_id` (the script's slot id), `switches` (the mapped switches on it), optional `name` (default: the host) and `password`. There is no device-registry lookup and no `Script.List` (owner, 2026-09-29).
  - The role follows from the switches: the Shelly holding the heat source switch must report `role: "heat_source"` and hold no valve; every other listed Shelly must report `"valve"`.
  - Every mapped switch is on exactly one listed Shelly or in `no_watchdog` (D-118); anything else is a config error.
- **Heartbeat client (D-121):**
  - a `POST` to every listed Shelly every HeartbeatInterval, as tasks with a 10 s timeout; a Shelly whose previous call is still running is skipped. The heat source Shelly also gets one at once when the heating season changes;
  - a call **fails** on a connection error or timeout, an HTTP status other than 200, a body that is not a status, `v` ≠ 1 or a `role` other than expected. The alert (D-61) names the cause: unreachable, script not running (404), authentication failed (401) or an unusable answer;
  - on the first call after HA starts and after a failed call, the status is read with `GET` first: the `POST` answer always shows the state after the heartbeat. A `timed_out` state and a restart (`uptime_s` lower than at the previous answer) are **only logged**, never notified (owner, 2026-09-29);
  - the alert state per Shelly is persisted in the adapter's `Store` (like D-98), so a restart neither repeats an alert nor loses a recovery. Active alerts appear in the alerts sensor.
- **Liveness (D-122):** heartbeats go out only while the reconcile loop works, i.e. its last completed run is at most 3 ReconcileIntervals old. A broken integration (e.g. every run failing after an HA update) therefore lets the Shelly watchdogs act after HeartbeatTimeout, as if HA had stopped. The first missed heartbeat is logged.
- **Switches without a watchdog (D-118):** every mapped switch is expected to be a Shelly running the watchdog script, unless it is listed in the optional YAML key `no_watchdog` (a list of mapped switch entities; any other entry is a config error). A listed switch gets no heartbeat and raises no heartbeat alert; any other relay (or a D-113 stand-in) is used this way. There is no automatic detection and no check of the device type. If HA stops, a listed switch stays in its last state: no device failsafe (§3.6 case 2), only the external watchdog. The user docs state this consequence.
- If Shelly authentication is enabled, the password comes from `secrets.yaml` (`password: !secret …` in the `shellys` entry); HTTP digest auth with the user `admin`.
- **The Shelly script answers every heartbeat call** with a short status: script running, current watchdog state (normal / timed out / failsafe), stored season flag, and its configured parameter values (HeartbeatTimeout; for the heat source script also FailsafeTrigger, FailsafeWindow and uptime cycle). HA therefore checks two things with the same call: the device is reachable, **and** the watchdog script is running.
- **Script parameters (D-73):** the script's configuration block or device storage is authoritative. HA never pushes parameter values. HA has *config* entries for the values it expects and alerts once if the reported values differ.
  - **Details (D-121):** compared are `heartbeat_timeout_s` (expected `heartbeat_timeout`, default 18000) and `check_interval_s` only if `heartbeat_check_interval` is set; other parameters are ignored. The alert is sent once while a difference lasts and clears silently when the values match again (the §3.6 table has no recovery for it).
  - **Refined (D-101):** the configuration block at the top of each script is the only source of parameters. Device storage (KVS) holds only runtime state: the heat source's season flag, written only when it changes.
- **Timing (D-102):** each script checks the time since the last heartbeat every `check_interval_s` (60 s) against its uptime, so long timeouts (5 h, 24 h) need no long timers and no valid clock. The script start (boot or restart) counts as the last heartbeat for both scripts.
- **Heartbeat failure alert (D-61):** after `HeartbeatFailAlert` (3 consecutive failed calls, ≈ 15 min), notify "Shelly X unreachable" or "watchdog script not running on Shelly X". A single failed call (Wi-Fi hiccup) never alerts. Also notify when it recovers.
- The heartbeat is sent whenever the integration runs, **including shadow mode** (D-56).
- Shelly settings:
  - power-on default: OFF (D-95 relies on it for the heat source Shelly);
  - scripts enabled at boot;
  - valve channels in switch profile.
- Scripts live in the repo (`shelly_scripts/`) with a configuration block at the top: timeouts, channels, failsafe window, uptime fallback. The role is given by the script file (`valve_watchdog.js`, `heat_source_watchdog.js`), not by a configuration key (D-100).

### 5.5 Shadow mode (D-56)
- With "Control active" OFF, the integration reads everything, computes decisions, updates its entities and logs, but sends **no output commands**. Heartbeat and watchdog ping continue.
- **Feedback in shadow mode (D-66):** the real switches are not driven by the integration, so the core gets the commanded states as "actual" states. The simulated decisions stay self-consistent. The output mismatch alert (D-67) is inactive.
- **Switching Control active ON → OFF (D-69):** the integration sends one final safe command set (heat pump request OFF, all valves OFF) and then stops sending commands. Heartbeats keep the Shelly watchdogs quiet, so without this the heat pump could stay ON indefinitely.
  - **Delivery (D-110):** every switch that does not report OFF gets OFF, retried with the D-108 backoff (also across a restart) until it has reported OFF once. A switch that is unavailable at that moment gets it when it returns. After that, nothing more is sent. A switch already reporting OFF gets no command.
- **Switching OFF → ON:** the next reconcile sets all outputs to the desired state.
  - **Going live (D-112):** from then on the real switch states count. If shadow mode had the heat source ON and the real switch is OFF, that is a stop, so HpMinOffTime applies before the first real start. Accepted; noted in the go-live checklist.
- **Commanded feedback (D-109):** the commanded state is the last desired state. When it changes, `step` runs again at once with the same `now` (not a new reconcile tick), as if the switches had followed. After a restart the heat source starts from the stored last known state, the valves from OFF.
- **Trial without Shellys (D-113):** for a shadow-mode trial before the Shellys are installed, stand-in switches are Template switch helpers without a state template (optimistic, restored after a restart). The spec stays switch-only; going live means replacing the entity ids. A new Template switch is `unknown` until it is switched once, and `unknown` counts as unavailable (no commands, D-66), so each stand-in is switched OFF once after it is created (user guide: [`getting-started.md`](getting-started.md)). Stand-ins are listed in `no_watchdog` (D-118), so they get no heartbeat.
- It is OFF on first install. The owner ran it next to the existing controller before switching to live (live since P7b, D-129).
- **Manual control (D-119):** there is no separate manual mode. For pure manual control (e.g. heating one room outside the automatic logic, or heat pump maintenance) the user switches Control active OFF first, then switches the relays directly (Shelly app or HA). the integration sends nothing after the final OFF (D-110); no min ON/OFF, cap or other protection applies. The watchdog scripts keep running: shadow mode keeps sending heartbeats (D-56), so they act only if HA is down for HeartbeatTimeout. Switching Control active ON returns to automatic control from the real switch states (D-112). The user docs describe this.

### 5.6 Configuration (D-52, D-55)
- **YAML configuration** is used for setup, also for the public release (at least initially). A UI setup (config flow) is an optional later improvement.
- **Config entry from YAML (D-124):** at startup the validated YAML is imported into a single config entry, so the integration appears under *Devices & services* and can create devices (D-125). The YAML stays the only configuration: the entry holds no data (the parsed YAML stays in memory, so no password is copied into HA's entry storage), and the UI's "Add integration" step only points to the YAML. The entry follows the YAML at every start; a zone removed from the YAML loses its device. Without a YAML section the entry fails to load with a clear error, and nothing is deleted. Removing the entry keeps the stored settings and state (the `Store` file), so the entry can be removed to let HA regenerate entity ids after a rename.
- **YAML holds only the wiring:**
  - zones: stable `id` (D-76; key for persisted state, schedules and entity unique IDs, must never change), display `name`, sensor entity, valve switch entity or `none`, sensor offset;
    - the `id` is an HA-style slug: lowercase letters, digits and `_`, starting with a letter (e.g. `living_room`). An invalid id is rejected with a suggested slug (D-84);
    - zone names must be unique, compared case-insensitively after trimming spaces (D-85);
  - heat source switch entity;
  - notify targets;
  - the Shellys running the watchdog scripts (address, script id, switches; D-120) and `no_watchdog` (D-118);
  - `!secret` references for the watchdog ping URL and credentials.
- **All values are changed from the UI and stored by HA:** SetPoints, parameters, holiday, schedules, season.
- The config is validated at startup with clear error messages (unknown entity, duplicate zone id or name, etc.).
  - **Exact keys:** [`configuration.md`](configuration.md) (D-111). `valve` is required: a switch or `none`, so a zone without a valve is a deliberate choice. Each switch may be mapped only once. Keys for later phases (notify, Shelly, watchdog) are added with them; unknown keys are rejected.
  - **Units (D-111):** temperatures in the YAML (`sensor_offset`, plausible range) are in HA's unit system and converted to °C.
  - **Unknown entities (D-107):** structural errors fail the setup. An entity that is neither in the entity registry nor has a state is reported after HA has started (error log + persistent notification), and the integration keeps running with it treated as unavailable, so a sensor integration that fails to load once does not stop heating control.
- Startup warning (not an error) if every zone has a valve, pointing to the hydraulic prerequisite (D-80).

Illustrative example (exact keys defined in implementation):
```yaml
multizone_floor_heating_manager:
  heat_source_switch: switch.heat_pump_request
  watchdog_ping_url: !secret floor_heating_watchdog_url
  notify: [notify.mobile_app_phone, notify.email]
  zones:
    - id: living_room
      name: Living room
      sensor: sensor.living_room_temperature
      valve: switch.valve_living_room
      sensor_offset: -0.2
    - id: bathroom
      name: Bathroom
      sensor: sensor.bathroom_temperature
      valve: none
```

### 5.7 Dashboard requirements (D-53)
The visual design (card types, layout, styling) is left to implementation. The dashboard must **show and control**:
- **Per zone:** RoomTemp, SetPoint (adjustable), state, reason text (e.g. "Calling zone", "Waiting period"; the timer end is the reason sensor's `until` attribute, D-123), valve on/off.
- **Global:** heat pump request with running time, active mode, heating season switch, control active switch.
- **Alerts:** visible only when active.
- **Holiday:** temperature, end date/time, start/stop.
- **Schedules:** a simple list plus an add/delete form built from the integration's own form entities (D-74).
- **Parameters:** on a separate settings page, not on the daily view.

Use built-in HA cards only; no custom frontend code in v1/v1.1. An example dashboard YAML is shipped in the repo.

**Example dashboard (D-140):** a daily view and a settings view. The daily view has one history graph per zone (room temperature and effective target as lines; state, reason and the valve relay as bars on the same time axis) and a Markdown card that explains every zone state and every reason in plain words (owner, 2026-09-29). This card is the only place for these explanations: no explanation attribute, no hover text. Screenshots follow in P8.

### 5.8 User documentation (D-54)
The repository must contain **detailed instructions** so another user can install and run the integration without help:
- **README:** what it does, how the logic works in plain words, and its limitations and safety notes (a heating system is involved), including the hydraulic prerequisite (D-80).
- **Installation:** via HACS custom repository and manually.
- **Configuration reference:** every YAML key with type, default and example; every entity and service.
- **Shelly scripts:** which script goes on which device, how to upload it, how to configure it, and how to test it.
- **Example dashboard** YAML and screenshots (the YAML since P10, [`dashboard.md`](dashboard.md); screenshots in P8, D-140).
- **Shadow mode and go-live checklist.**
- **Troubleshooting:** sensor faults, heartbeat, failsafe, logs.
- **Update notes / changelog** per release.

Docs are updated in the same commit(s) as the code they describe.

### 5.9 Repository structure (proposal)
```
/
├── CLAUDE.md                   # §0 rules, architecture summary, working rules
├── README.md
├── LICENSE                     # MIT (D-63)
├── hacs.json
├── .gitignore  .pre-commit-config.yaml
├── custom_components/multizone_floor_heating_manager/
│   ├── manifest.json
│   ├── __init__.py             # setup, reconcile loop, heartbeat, watchdog
│   ├── core/                   # pure logic, no HA imports
│   ├── climate.py  sensor.py  binary_sensor.py  number.py  switch.py ...
│   └── services.yaml
├── shelly_scripts/             # valve_watchdog.js, heat_source_watchdog.js
├── tests/                      # core unit tests (+ adapter tests); tests/shelly/: JS tests of the scripts
├── package.json                # Node test runner + JS subset check (acorn) for shelly_scripts/
├── docs/                       # design.md (this file), heartbeat-protocol.md, user docs
├── examples/                   # configuration.example.yaml, dashboard.example.yaml, secrets.example.yaml
└── .github/workflows/          # tests; optional check against latest HA
```

### 5.10 Phasing
The three feature sets below (v1, v1.1, v1.2) are split into smaller **work phases** in `docs/implementation-plan.md` (D-82): P0–P7b = v1, P9–P10 = v1.1, P11–P12 = v1.2, and P8 (documentation and release preparation) runs last (D-129). Each work phase is committed directly to `main` and ends with a summary to the owner (D-83). The next phase starts only when the owner asks. The contents below are binding; the implementation plan only orders the work and must be updated if it drifts from this section.

**Releases (D-128, D-129, D-140):** nothing is tagged or released before 1.0.0, the first release. It contains all three feature sets and follows P8, after P9–P12. Until then, versions are 0.x.x and set in `manifest.json` only (P7b: 0.7.5, P10: 0.8.0) and the owner installs from the default branch. The owner already runs the integration live (since P7b), so there is no separate go-live step.

**v1 — replaces the existing controller:**
- zone logic (§3.3), min ON/OFF (§3.5), sensor fault (§3.6);
- heating season switch;
- entities, notifications, including the output mismatch alert (D-67);
- shadow mode;
- persistence and restart (§3.8);
- heartbeat;
- both Shelly scripts, with the valve script complete and the heat source script up to "OFF after HeartbeatTimeout";
- user docs for all of the above.

**v1.1:**
- auto and manual schedules, holiday;
- dashboard example.

**v1.2:**
- 24 h failsafe (HA case and Shelly 1 window with uptime fallback);
- valve exercise;
- long run alarm;
- healthchecks.io watchdog;
- overshoot logging.

### 5.11 Development & deployment
- **Repository and deployment:**
  - public GitHub repo from the start (D-46); the §0 rules keep it free of secrets;
  - deployment via HACS custom repository; updates from GitHub releases.
- **Where work happens (D-47):**
  - **Claude Code on the web (cloud):** core, tests, integration code, docs.
  - **Local (T14, WSL):** Shelly scripts on the bench, and anything needing the home network. The cloud sandbox cannot reach HA or the Shellys and gets no credentials.
- **Coordination (D-51):** the sessions don't talk to each other; the GitHub repo is the single shared memory (`CLAUDE.md`, `docs/`, code, commit history). Every session starts from the latest main.
- **HA update safety:**
  - Breakage is detected automatically: the heartbeat and watchdog ping come from the integration itself, so a broken integration triggers the Shelly watchdogs and healthchecks.io.
  - Use only long-standing, stable HA APIs, and watch for deprecation warnings.
  - Update routine: wait for the .1/.2 patch release, avoid major updates in deep winter, rely on HA's pre-update backup for rollback.
  - Optional CI: a GitHub Action runs the tests against the latest HA release.

---

## 6. Acceptance scenarios (minimum test set)

Defaults from §4 apply unless stated. All zones are valved unless stated. "HP" = heat pump request.

| # | Scenario | Expected |
|---|---|---|
| A1 | HP off; zone 1 drops to StartTemp at 06:00 and stays there | `WAITING` 06:00; `HEATING` + HP ON at 06:30; zone 1 = calling zone |
| A2 | As A1, but zone 1 is back above StartTemp at 06:30 (window closed) | `IDLE` at 06:30; HP stays OFF |
| A3 | As A1, but RoomTemp goes above and back below StartTemp during the wait; ≤ StartTemp at 06:30 | `HEATING` at 06:30 (only the expiry check counts) |
| A4 | HP ON for zone 1; zone 2 drops to StartTemp | zone 2 `HEATING` immediately, no wait |
| A5 | Zone 2 is `WAITING` when zone 1 starts the HP | zone 2 joins immediately |
| A6 | Zone 1 (calling) reaches SetPoint; zone 3 at SetPoint − 0.1 (above StartTemp) | zone 3 joins; sync rule doesn't fire again in this cycle |
| A7 | Sync fired; zone 3 reaches StopTemp | zone 3 `IDLE`; HP stays ON while any zone heats |
| A8 | All zones reach StopTemp 40 min after HP ON; zone 4 is at 25 °C (≥ ManualMaxTemp) | all valves except zone 4 open until 60 min; then HP OFF, valves by normal logic |
| A9 | HP went OFF at 08:00; zone 2 hits StartTemp at 08:10 | WaitTime 08:10–08:40; if the check at 08:40 passed: zone 2 `HEATING`, valve opens at 08:40, HP ON at 09:00 (min OFF); if zone 2 reaches StopTemp before 09:00 → `IDLE`, HP stays OFF |
| A10 | Auto schedule raises zone 1 SetPoint 22 → 23 at 13:00; RoomTemp 22.1 | `HEATING` immediately (no wait), subject to min OFF; zone 1 = calling zone if it starts the HP |
| A11 | Auto schedule ends; SetPoint back to 22; RoomTemp 22.5 | zone stops (≥ StopTemp) |
| A12 | New auto schedule overlaps an existing one for the same zone | rejected with error; nothing stored |
| A13 | Manual schedule zone 2, 04:00–06:00; all zones satisfied | zone 2 `FORCED`, HP ON; no calling zone; HP OFF at 06:00 (min ON satisfied) |
| A14 | As A13; zone 3 drops to StartTemp at 05:00 | zone 3 joins and becomes the calling zone; sync rule fires when zone 3 reaches SetPoint |
| A15 | Forced zone reaches 25 °C | valve closes, no demand; resumes below 24 °C within the window |
| A16 | Holiday active until Sunday 15:00 | all effective SetPoints 18 °C; schedules ignored; at 15:00 BaseSetPoints apply and zones below StartTemp start immediately |
| A17 | Zone 4 sensor silent for 60 min | `SENSOR_FAULT`; valve opens only when HP runs; no demand; notification; 08:00 reminder next day |
| A18 | Calling zone's sensor fails mid-cycle | counts as reached SetPoint → sync rule fires; zone becomes `SENSOR_FAULT` |
| A19 | All sensors silent for 24 h, HA alive, heating season ON | failsafe: all valves open + HP ON 10:00–15:00 daily; notification; exits on the first valid reading |
| A20 | Heating season OFF (also switched OFF 20 min into a cycle) | HP OFF and valves closed immediately, min ON ignored; no demand; no failsafe heating; valve exercise on Monday 08:00; sensor fault notified without daily reminder |
| A21 | Shadow mode | decisions and entities update using commanded states as feedback; no switch commands sent; heartbeat still sent; switching Control active ON → OFF sends one final HP OFF + valves OFF, then nothing |
| A22 | HA restart during `WAITING` (10 min left) and HP ON for 20 min | after restart: wait continues with ~10 min left; min ON counts from the original start |
| A23 | Unvalved zone drops to StartTemp | behaves like A1 (can start the HP); no output command |
| A24 | Schedule window 22:00–02:00 across a DST change | correct local start/end times |
| A25 | Actual valve state differs from desired (e.g. switched in the Shelly app) | corrected within one reconcile interval |
| A26 | HP off; zones 1 and 2 WaitTime expire in the same step; zone 1 at StartTemp − 0.1, zone 2 at StartTemp − 0.3 | both `HEATING`; zone 2 = calling zone (largest deficit); equal deficits → first in YAML order |
| A27 | A valve switch stays unavailable (or ignores commands) for 3 reconcile intervals | "output not following command" notified once; retries with backoff; recovery notified |
| A28 | Manual schedule active for zone 2, but zone 2 is in `SENSOR_FAULT` | zone 2 stays `SENSOR_FAULT` (follows the house, no demand); no HP start because of it |
| A29 | Heat source switch becomes unavailable while ON | counts as HP OFF: min OFF starts; zones do not join by rule 3; mismatch alert per A27. Back ON: it never stopped (min ON and the cycle continue); back OFF: OFF since it became unavailable (D-95) |
| A30 | First start, no persisted state | outputs read back; HpMinOffTime not applied; a zone at StartTemp enters `WAITING` and starts the HP after WaitTime |

**Shelly scripts (bench tests, shortened timeouts):**

| # | Scenario | Expected |
|---|---|---|
| S1 | Valve Shelly: heartbeat stops | all channels ON after HeartbeatTimeout |
| S2 | Heat source Shelly: heartbeat stops | OFF after HeartbeatTimeout; failsafe window after FailsafeTrigger (season ON) |
| S3 | Heat source Shelly: reboot, no heartbeat, no valid time | OFF until FailsafeTrigger after boot; then uptime cycle starting with 5 h ON, then 19 h OFF (season flag ON) |
| S4 | Heartbeat returns | scripts stop acting; HA's reconcile sets outputs |
| S5 | Last heartbeat said season OFF | heat source Shelly never heats in failsafe |
| S6 | Heartbeat call to a running script | answered with status (script running, watchdog state, season flag, configured parameter values); HA alerts if the values differ from its expected config |
| S7 | Script stopped / device offline for 3 calls | HA alerts once; alerts recovery when calls succeed again |

---

## 7. Decision log

| # | Decision |
|---|---|
| D-01 | SetPoints are per zone |
| D-02 | Unvalved zone: dummy output, full participation in logic |
| D-03 | No valve lead/lag timing (buffer + always-open zone) |
| D-04 | Shelly Plus 2PM (Gen2) for valves |
| D-05 | WaitTime: single temperature check at expiry |
| D-06 | Sync rule triggers when the calling zone reaches SetPoint |
| D-07 | Configurable HpMinOnTime / HpMinOffTime |
| D-08 | Sensor fault detection with notification |
| D-09 | No holiday preheat automation |
| D-10 | Valve exercise: yes |
| D-11 | *(withdrawn — merged into D-27)* |
| D-12 | Failsafe duty cycle after 24 h; Computherm fallback rejected |
| D-13 | Overshoot learning deferred to v2; v1 logs data |
| D-14 | No WaitTime when heat pump is already running |
| D-15 | Sync rule fires on the first calling zone, once per cycle |
| D-16 | Precedence: failsafe > holiday > manual > auto > base |
| D-17 | Holiday: one temperature for all zones; schedules suspended *(temperature per zone since D-133)* |
| D-18 | Manual schedule: ManualMaxTemp cap; other zones may join |
| D-19 | Overlapping auto schedules (same zone) rejected at creation |
| D-20 | All zones satisfied before min ON elapsed → all valves open until it elapses |
| D-21 | Sensor fault notification after 60 min |
| D-22 | Failsafe: all sensors invalid or HA lost > 24 h → all valves + HP 10:00–15:00; immediate return to normal |
| D-23 | Notifications: push + email; external watchdog (see D-48) |
| D-24 | Heating season: manual switch |
| D-25 | Valve exercise weekly, Monday 08:00 |
| D-26 | SetPoint raised → start immediately, no WaitTime |
| D-27 | Faulty-sensor zone follows the house; daily 08:00 reminder |
| D-28 | Sensor lost during HEATING → same fault rule; faulty calling zone fires the sync rule |
| D-29 | Secondary pump is switched by the heat pump (reference installation) |
| D-30 | HpMinOnTime 60 min, HpMinOffTime 60 min |
| D-31 | Computherm removed at go-live; Shelly 1 takes its terminals |
| D-32 | Shelly 1 Gen3 or Gen4 (standard, not mini) for heat pump request |
| D-33 | Heat pump flow temperature: weather curve (trial) |
| D-34 | Develop against the live HA (shadow mode first) |
| D-35 | Failsafe without HA: valve Shellys open all valves after heartbeat loss; only the heat source Shelly runs the time window (NTP, uptime fallback); no Shelly-to-Shelly sync |
| D-36 | On HeartbeatTimeout: heat source Shelly → OFF, valve Shellys → all valves open (timeout value: D-60) |
| D-37 | Failsafe only in heating season; valve exercise only outside it |
| D-38 | Manual schedule resumes below ManualMaxTemp − 1.0 °C |
| D-39 | WaitTime and HpMinOffTime run in parallel |
| D-40 | State restored after HA restart: outputs read back, logic state persisted in HA |
| D-41 | *(withdrawn, D-129)* |
| D-42 | Long run alarm at 12 h |
| D-43 | *(superseded by D-53)* |
| D-44 | Manually started cycle: first zone joining by temperature becomes the calling zone; otherwise no sync rule |
| D-45 | Hot water runs pause floor heating; no logic change, effects accepted |
| D-46 | Public GitHub repo from the start, deployment via HACS custom repository *(amended 2026-09-27; was: private repo)* |
| D-47 | Cloud Claude Code for core + integration; local for Shelly scripts and home-network work |
| D-48 | External "HA dead" watchdog: healthchecks.io |
| D-49 | No secrets in the repository (§0.1) |
| D-50 | Generic, publishable design (§0.2) |
| D-51 | GitHub repo is the shared memory between Claude Code sessions |
| D-52 | All daily functions via HA UI |
| D-53 | Dashboard: requirements fixed (§5.7), visual design left to implementation, built-in cards only |
| D-54 | Detailed user documentation in the repo (§5.8) |
| D-55 | YAML configuration also for public release (at least initially); config flow optional later |
| D-56 | Shadow mode: no output commands; heartbeat and watchdog ping continue |
| D-57 | Schedule times are local wall-clock (HA time zone, DST-aware); windows may cross midnight |
| D-58 | Manual schedules for the same zone may overlap (union); manual vs auto resolved by precedence |
| D-59 | Holiday overrides the effective SetPoint only; BaseSetPoints untouched; starts on activation, manual stop possible |
| D-60 | HeartbeatTimeout 5 h (was 1 h), same for valve and heat source Shellys: owner gets time to react on HA side; heartbeat interval stays 5 min. Not split (2026-09-27): HP OFF also stops the secondary pump, so valves and HP cannot be separated usefully, and running on keeps heat in the house during a longer outage |
| D-61 | Heartbeat calls are answered with script status; alert after 3 consecutive failures |
| D-62 | healthchecks.io: period 5 min, grace ≈ 30 min |
| D-63 | License: MIT; copyright line as given in §0.2 and `LICENSE` only |
| D-64 | Demand held back by HpMinOffTime: zone `HEATING`, valve opens immediately; HP request ON when min OFF elapses; `IDLE` if StopTemp reached meanwhile |
| D-65 | Several calling-zone candidates in one step: largest (StartTemp − RoomTemp) wins; ties by YAML order |
| D-66 | "HP running" = actual heat source switch state (request feedback, not compressor); unavailable = OFF; HP timers from actual transitions; shadow mode uses commanded state as feedback |
| D-67 | Alert "output not following command" after 3 reconcile intervals of mismatch/unavailability; reconcile retries with backoff |
| D-68 | Heating season OFF mid-cycle stops the HP request immediately, overriding min ON |
| D-69 | Control active ON → OFF: one final safe command set (HP OFF, valves OFF), then no commands |
| D-70 | `SENSOR_FAULT` takes precedence over `FORCED` |
| D-71 | D-20 spread excludes zones with RoomTemp ≥ ManualMaxTemp |
| D-72 | Heat source Shelly reboot without heartbeat: boot = last heartbeat; OFF until FailsafeTrigger; uptime cycle starts with 5 h ON at uptime = FailsafeTrigger |
| D-73 | Shelly-side parameters live only in the script config/KVS; reported in the heartbeat status; HA alerts on mismatch; HA's FailsafeTrigger/Window apply to case 1 only |
| D-74 | Schedule add/delete form entities are provided by the integration; no user helpers needed |
| D-75 | Outside heating season: sensor fault start/recovery notified, no daily reminder |
| D-76 | Zones have a stable YAML `id`; `name` is display only |
| D-77 | Core computes in °C; adapter converts units; plausibility range is config (default 0–40 °C) |
| D-78 | First start without persisted state: HpMinOffTime not applied |
| D-79 | Holiday UI: "Holiday active" switch + end date/time; shadow mode shown separately from the mode sensor |
| D-80 | Hydraulic prerequisite (flow path whenever HP request ON) documented; startup warning if every zone has a valve |
| D-81 | HpMinOnTime / HpMinOffTime configurable 30–180 min; never below 30 min (short-cycling protection) |
| D-82 | Releases are split into work phases P0–P12 (`docs/implementation-plan.md`); the first task is P0 (bootstrap) only, the v1 core follows in P1–P3 *(amended by D-83; was: one pull request each)* |
| D-83 | From P1 on, work is committed directly to `main`; no branches, pull requests or PR reviews. `main` stays green (local checks before push, CI on every push). Each phase still ends with a summary to the owner, and the next phase starts only when asked |
| D-84 | Zone `id` is an HA-style slug (`[a-z][a-z0-9_]*`); invalid ids are rejected with a suggested slug |
| D-85 | Duplicate zone names are detected case-insensitively after trimming |
| D-86 | Parameter validation checks the inclusive §4 range only (plus finite numbers); the §4 step is UI granularity |
| D-87 | Persisted core state is versioned; additive fields get defaults, breaking changes bump the version; newer-version or corrupt data is discarded and the integration starts as on a first start (D-78); state of removed zones is dropped |
| D-88 | The plausibility range (§3.6) is checked against the raw sensor reading, before the per-zone offset |
| D-89 | Per-zone reason texts are part of `step`'s outputs (current value per zone for the reason sensor), not events *(texts fixed by D-123; were countdowns like "Waiting, 12 min left"; fixed keys with translated texts since D-126)* |
| D-90 | Reading validity and the SENSOR_FAULT state machine are implemented with the zone logic in P2; P3 keeps fault notifications, season OFF and the mismatch counter |
| D-91 | First start with the heat source already ON and no persisted ON time: HpMinOnTime counts from startup |
| D-92 | Request ON without a calling zone: the HEATING zone with the largest deficit (ties by YAML order) becomes the calling zone |
| D-93 | No valid reading since startup and none persisted: SensorFaultTimeout counts from startup; the zone is IDLE with no demand until then |
| D-94 | A SetPoint decrease that leaves RoomTemp above the new StartTemp ends a running WaitTime at once (IDLE) |
| D-95 | Heat source switch unavailable, then back: back ON after ON means it never stopped (min ON and the cycle continue); back OFF means OFF since it became unavailable; the cycle is kept while unavailable. Refines D-66; relies on the Shelly power-on default OFF |
| D-96 | Time zone contract: `step` gets HA's time zone in its inputs and converts `now` itself for local wall-clock rules; `now` may be in any time zone. Local times in the DST gap take effect after the gap, repeated times at the first occurrence *(gap: shifted by the gap length, D-134)* |
| D-97 | Heating season OFF: zones without a fault are IDLE, the cycle ends, request OFF and all valves closed at once (faulty zones too); fault detection and SetPoint tracking continue; season ON is not a SetPoint raise (normal rules, WaitTime); HpMinOffTime counts from the actual OFF |
| D-98 | Sensor fault notifications: start/recovery on the state change (not repeated after a restart; also in shadow mode); one daily reminder per local day for zones faulty since an earlier day, due from SensorFaultReminder to midnight with catch-up, heating season only |
| D-99 | Output mismatch counted on reconcile ticks only (flag from the adapter; once per `now`); unavailable always counts, a differing state counts only if the desired state is unchanged since the previous tick; alert once at OutputMismatchAlert, recovery when following again; reset silently in shadow mode |
| D-100 | Heartbeat protocol v1 (`docs/heartbeat-protocol.md`): `POST /script/<id>/heartbeat` = heartbeat with a JSON body, `GET` = status only; JSON status with protocol version `v`, role, state (`normal`/`timed_out`; `failsafe` from v1.2), heartbeat age, uptime, season flag, switch outputs and `params`; additive changes keep `v`, both sides ignore unknown fields. The role is fixed by the script file |
| D-101 | Shelly script parameters live only in each script's CONFIG block; KVS holds only runtime state (the season flag), written only on change. Refines D-73 |
| D-102 | Scripts measure the heartbeat timeout with their uptime in a periodic check (60 s), not with long timers or the clock. Script start (boot or restart) counts as the last heartbeat for both scripts; outputs are not touched at start. Extends D-72 to the valve script |
| D-103 | A timed-out script re-asserts its safe outputs at every check (only if they differ); a returning heartbeat ends the timeout at once and the script switches nothing |
| D-104 | The valve script manages every switch component of its device by default; the CONFIG block can list the channels instead |
| D-105 | Heat source heartbeat must carry a boolean `season`, otherwise `400` and no heartbeat; a season flag never set counts as OFF and is reported as `null`; a heartbeat during the boot-time KVS read wins over the stored value |
| D-106 | The adapter owns the UI values (zone and global parameters, heating season, Control active) and persists them with the core state in its `Store` file; entities only show and change them. Defaults on a first install: season ON, Control active OFF |
| D-107 | Startup checks: structural YAML errors fail the setup; entities neither registered nor with a state are reported after HA start (error log + persistent notification) and treated as unavailable, the integration keeps running; a sensor without a temperature unit gives no valid reading (warning once) |
| D-108 | Command backoff: first command at once; retries after 1, 2, 4, 8 min, then every 15 min; reset when the switch follows or the desired state changes; nothing sent or queued while unavailable, first command at once when it returns |
| D-109 | Reconcile loop runs on the timer (ticks), on state changes of mapped sensors and switches, and on settings changes, serialised by a lock; it starts once HA has started. Shadow mode: commanded = last desired state; on a change `step` runs again at the same `now`; after a restart the heat source starts from the stored last known state |
| D-110 | Control active ON → OFF: the final OFF is repeated (D-108 backoff, persisted across restarts) until each switch has reported OFF once, then nothing more is sent; a switch already OFF gets no command. Refines D-69 |
| D-111 | YAML keys as in `docs/configuration.md`; `valve` is required (`none` for no valve); a switch may be mapped once; YAML temperatures are in HA's unit system; readings are converted from the sensor's own unit |
| D-112 | Going live after shadow mode: the real switch states count; a heat source that shadow mode had ON but that reads OFF counts as stopped, so HpMinOffTime applies before the first start. Accepted, noted in the go-live checklist |
| D-113 | A shadow-mode trial without Shellys uses Template switch helpers without a state template as stand-in switches; the spec stays switch-only. `unknown` counts as unavailable, so a new stand-in is switched OFF once after it is created |
| D-114 | Every §4 global parameter gets its number entity in v1 (P6), also those whose features follow in v1.1/v1.2; the docs name the release each is used from |
| D-115 | *(entity ids superseded by D-125)* Entity ids are fixed and built from the zone id (`<platform>.floorheat_<zone>_<key>`, global `<platform>.floorheat_<key>`); unique ids `floorheat_<key>`; display names use the zone name |
| D-116 | Climate `hvac_action` = heating while the request is ON and the zone gets flow (valve open or no valve); heat request binary sensor = desired request with `on_since` / `on_duration` (unrecorded) attributes *(present only while running, D-123)* |
| D-117 | Notify targets are `notify.<name>`: a legacy notify service, otherwise a notify entity via `notify.send_message`; one title per event kind; failures logged, never blocking; unknown targets reported after start |
| D-118 | Switches without a Shelly watchdog are listed explicitly in the YAML key `no_watchdog` (mapped switches only); they get no heartbeat and no heartbeat alert. Unlisted switches are expected to be Shellys with the script. No automatic detection or device-type check (owner, 2026-09-28). The user docs explain that a listed switch has no device failsafe when HA stops |
| D-119 | No manual mode: pure manual control is Control active OFF plus switching the relays directly, with no protection; the watchdog scripts keep running because heartbeats continue (owner, 2026-09-29). Documented in the user docs (P8) |
| D-120 | Shelly address, script id, switches, optional name and password come from the YAML key `shellys`; no device registry or `Script.List` (V3 dropped). The Shelly with the heat source switch must run the heat source script and hold no valve, every other one the valve script. Every mapped switch is on exactly one listed Shelly or in `no_watchdog`, otherwise a config error (owner, 2026-09-29) |
| D-121 | Heartbeat client: `POST` every HeartbeatInterval (also in shadow mode, and at once to the heat source on a season change); a failed call is a connection error/timeout (10 s), non-200, no status, `v` ≠ 1 or the wrong role; one alert after HeartbeatFailAlert failures naming the cause, recovery notified; parameter check of `heartbeat_timeout_s` (and `check_interval_s` if configured), alerted once and cleared silently; `GET` first after a start or a failure so `timed_out` and restarts can be logged (never notified); alert state persisted and shown in the alerts sensor (owner, 2026-09-29) |
| D-122 | Heartbeats only while the reconcile loop works: the last completed run is at most 3 ReconcileIntervals old, so a broken integration triggers the Shelly failsafe (owner, 2026-09-29) |
| D-123 | No per-minute countdowns in entity states: reason texts are fixed ("Waiting", "Held by min OFF", "Spreading heat (min ON)"); the reason sensor's `until` attribute holds the end of the running timer and is present only while one runs; the heat request's `on_since` / `on_duration` are present only while it runs (owner, 2026-09-29). Amends D-89 and D-116 |
| D-124 | The YAML is imported into a single config entry at startup (import flow, `single_config_entry`); the YAML stays the only configuration, the entry holds no data, the UI setup step only points to the YAML. No YAML section → the entry fails with a clear error, nothing deleted. Removing the entry keeps the stored settings and state (owner, 2026-09-29) |
| D-125 | Devices and names follow HA's conventions: a *service* device per zone ("<zone name> floor heating") and a "Floor heating" device; `has_entity_name`, translated names and states, *config* category for settings; entity ids generated by HA from device + entity name, no integration name in them; unique ids from zone id + key; areas assigned in the UI. No per-zone valve entity (the relay's own entity shows it while live). Supersedes the fixed ids of D-115 (owner, 2026-09-29) |
| D-126 | The reason sensor is an enum of fixed keys (`idle`, `waiting`, `calling_zone`, `heating`, `held_by_minimum_off_time`, `spreading_heat`, `too_warm_for_spreading`, `heat_source_unavailable`, `no_reading_yet`, `sensor_fault`, `season_off`, `sensor_fault_season_off`); the core returns the key, the texts are translations ("heat source" wording). Amends D-89 and D-123 (owner, 2026-09-29) *(keys `forced`, `forced_too_warm` added by D-135)* |
| D-127 | The integration is renamed from the working name `floorheat` to **Multizone Floor Heating Manager**, domain `multizone_floor_heating_manager` (YAML key, folder, storage file). Docs name it in full, then "the integration"; notification titles start with "Floor heating:". The Shelly scripts use the new name; the heat source script's KVS key becomes `multizone_floor_heating_manager_season` (owner, 2026-09-29) |
| D-128 | No tag or GitHub release before the first release, which is 1.0.0; until then versions are set in `manifest.json` only (P7b: 0.7.5) and installed from the default branch. Whether P9/P10 go into 1.0.0 is decided later (owner, 2026-09-29) *(settled by D-129)* |
| D-129 | Phase order after P7b: P9 → P10 → P11 → P12 → P8 → release 1.0.0 with v1, v1.1 and v1.2. P8 is documentation and release preparation only (the user docs keep a shadow-mode-to-live section); there is no go-live step, the owner is live since P7b. The actuator fault check by power measurement is removed: D-41 withdrawn, V1, ActuatorFaultThreshold and the `power_sensor` YAML key dropped (owner, 2026-09-29) |
| D-130 | Manual schedule details: the SetPoint below it (auto or base) stays in force and is shown; a zone without a valid reading yet is not forced (like D-70); the cap engages at ≥ ManualMaxTemp also at the window start, and a capped zone stays closed during the D-20 spread; forced demand is subject to §3.5 without WaitTime; at the window end the zone is IDLE and evaluated in the same step (owner, 2026-09-30) |
| D-131 | A calling zone that becomes FORCED mid-cycle loses the role; the HEATING zone with the largest deficit takes it (D-92), otherwise the next zone that joins (owner, 2026-09-30) |
| D-132 | Schedule windows: half-open, end before start crosses midnight, start = end rejected, weekday/date = start day, touching windows don't overlap, overlap checked on local wall-clock time, an already ended one-shot rejected, "daily" = all weekdays; auto temperature 10–30 °C; "all zones" is a flag that also covers zones added later; zones removed from the YAML are dropped from schedules, a schedule without zones is deleted, with warnings (owner, 2026-09-30) |
| D-133 | HolidayTemp is per zone (default 18 °C, 10–25 °C): holiday sets every zone to its own holiday temperature; the global value becomes every zone's starting value when the entities move to the zones (P10). Holiday still ends at its end date/time or when switched off. Amends D-17 (owner, 2026-09-30) |
| D-134 | DST: a local time in the spring gap is shifted by the gap length (02:30 → 03:30), for schedules as for the sensor fault reminder; a window keeps its wall-clock length and is empty if its start ends up at or after its end. Clarifies D-96 (owner, 2026-09-30) |
| D-135 | New reason keys `forced` ("Manual schedule", `until` = end of the running manual windows, their union) and `forced_too_warm` ("Manual schedule, paused: too warm"); a forced zone held by min OFF or with the heat source unavailable uses the existing keys; no holiday reason (the mode sensor shows holiday). Amends D-126 (owner, 2026-09-30) |
| D-136 | Schedules and the holiday end are owned and stored by the adapter and passed to `step` as inputs; holiday is active while `now` is before its end. The core reports `holiday_active` and the ended one-shot schedules; the adapter switches holiday off and deletes them. The core provides the creation check and the (de)serialisation (owner, 2026-09-30) |
| D-137 | Holiday end: a full date and time; switched on without an end, holiday runs until switched off by hand; an end in the past is refused when switching on; changing the end while it runs moves it (a past time ends it); every end (reached or by hand) clears the end. `Inputs.holiday_on` plus the optional `holiday_until`. Amends D-59, D-79 and D-136 (owner, 2026-09-30) |
| D-138 | Schedule form: the zone select is "All zones" or one zone; the "Days" select is once (date), every day, Monday–Friday, Saturday–Sunday or one weekday; other combinations via the service. Schedules are numbered (`#n`, never reused) with a generated label, no name field. Draft values stay after "Add schedule" and reset after a restart (owner, 2026-09-30) |
| D-139 | Schedule services `add_schedule` / `delete_schedule` / `list_schedules`; zones as YAML zone ids or `all`; temperatures in HA's unit system; rejected schedules raise a validation error and store nothing; a "Schedules" sensor lists them (owner, 2026-09-30) |
| D-140 | Example dashboard: one history graph per zone and the state/reason explanation card; screenshots in P8. Versions stay 0.x.x until the first real release (P10: 0.8.0). The integration does not clean up entities left over from earlier versions; the user deletes them, and P8 checks this for upgrading users before the release (owner, 2026-09-30) |
| – | Not adopted (2026-09-27): per-zone OFF mode; the climate entity offers `heat` only |

D-01 to D-63 dated 2026-09-25 (D-56 to D-59 added during that final review). D-64 to D-82 and the amendments to D-46, D-60 and D-63 were added in the 2026-09-27 owner review (Spec rev. 1.2). D-83 and the amendment to D-82 were added on 2026-09-27 after P0. D-84 to D-89 were added on 2026-09-27 during P1, D-90 to D-93 during P2, D-94 and D-95 after the P2 review, D-96 to D-99 during P3, D-100 to D-105 during P4 (owner answers on parameters, valve reboot, re-asserting and the JS subset check). D-106 to D-113 were added on 2026-09-27 during P5 (owner answers on the P5 plan), D-114 to D-117 during P6. D-118 was added on 2026-09-28 after the first shadow trial, D-119 on 2026-09-29, D-120 to D-123 on 2026-09-29 during P7, D-124 to D-128 on 2026-09-29 at the start of P7b, D-129 on 2026-09-29 after P7b, D-130 to D-136 on 2026-09-30 during P9 (owner answers on the P9 plan), D-137 and up on 2026-09-30 during P10 (owner answers on the P10 plan).

---

## 8. Items to verify on real hardware (owner)

| # | Item | Impact if negative |
|---|---|---|
| V2 | Shelly script HTTP endpoint for the heartbeat works on 2PM Gen2 and Shelly 1 Gen3/Gen4 | Alternative heartbeat transport needed |
| V3 | *(dropped, D-120)* Shelly device address can be derived from the HA device registry | Addresses and script ids are listed in YAML |
| V4 | Whether the secondary pump runs during hot water production | Documentation only |
| V5 | The heat pump reacts correctly to the Shelly 1 contact on the former Computherm terminals | Wiring check before go-live |
| V6 | BTHome/pvvx sensor entities update `last_reported` when the same value repeats | Sensor fault detection (§3.6) would misfire; needs another staleness source |

**Results so far:**
- **V2, 2PM (2026-09-28):** passed on two Shelly Plus 2PM Gen2 with firmware 1.7.5 (`GET` and `POST`, `400` on an invalid body, answers after a power loss; bench tests S1, S4, S6 and the reboot check passed too). Tested without authentication, so the `401` check is still open. The Shelly 1 part follows below (2026-10-01).
- **V2, Shelly 1 (2026-10-01):** passed on a Shelly 1 Gen4 with firmware 2.0.1 (the Wi-Fi + Matter variant; scripting works). `GET` and `POST` heartbeats work; after the timeout the output switched OFF and was forced OFF again when switched ON in the app; after heartbeats returned, the output followed the app again. Not yet tested: the power-loss reboot (power-on default OFF, script answers, season flag kept) and the `401` check (authentication off). Device settings: input detached, power-on default OFF.
- **V6, accepted (2026-09-29):** about half a day on the real pvvx/BTHome thermometers without a false `sensor_fault`. A direct check was not possible: the readings changed with every report, so no repeated value was seen. The owner accepts V6 as working; it is reopened only if a false sensor fault notification appears (a working thermometer reported as faulty).
- **P7 heartbeat, local check (2026-09-29):** floorheat 0.7.0 on the live HA sends heartbeats to the watchdog scripts on both Shelly Plus 2PM Gen2: `heartbeat_seen: true`, state `normal`, the heartbeat age resets every 5 min, no alert. S7 (script stopped → alert, started → recovery) on the real devices is still open.
- **Power-on default (2026-09-28, 2PM):** a real power loss restarts the outputs OFF; a software reboot keeps them as they were. Both fit D-95: after a reboot the relay really did not change.
