# Implementation Plan — `floorheat`

> Source of truth: `docs/design.md` (Spec rev. 1.2). If this plan and the spec disagree, the spec wins; fix the plan.
> Rules: one work phase at a time, committed directly to `main` (D-83); each phase ends with a summary to the owner, and the next phase starts only when the owner asks. Docs are updated in the same commit(s) as the code. `main` must stay green. The §0 rules apply everywhere.

## Overview
| Phase | Name | Release | Where | Main tests |
|---|---|---|---|---|
| P0 | Repository bootstrap | v1 | cloud | CI runs, secret scanning works |
| P1 | Core models, config validation, persistence format | v1 | cloud | unit tests |
| P2 | Core zone logic, sensor validity & HP protection | v1 | cloud | A1–A9, A18, A23, A26, A29, A30 + simulation |
| P3 | Core season, fault notifications, mismatch | v1 | cloud | A17, A20 (v1 part), A29 (alert) + simulation |
| P4 | Shelly scripts v1 + heartbeat protocol | v1 | local (bench) | JS unit tests, bench S1, S4, S6 |
| P5 | HA adapter: config, reconcile, outputs, persistence | v1 | cloud | A21, A22, A25, A27 (HA test harness) |
| P6 | HA entities & notifications | v1 | cloud | entity/notify tests |
| P7 | HA heartbeat client | v1 | cloud + local check | S7, mismatch alert, shadow heartbeat |
| P8 | v1 docs, release, shadow run, go-live | **v1 release** | local / owner | install test, V2–V6, go-live checklist |
| P9 | Core schedules & holiday | v1.1 | cloud | A10–A16, A24, A28 + DST/overlap tests |
| P10 | HA schedules, holiday, dashboard | **v1.1 release** | cloud | service/form-entity tests, dashboard check |
| P11 | Core failsafe & maintenance features | v1.2 | cloud | A19, A20 (exercise), actuator, long run, overshoot |
| P12 | Heat source failsafe script, watchdog ping, v1.2 release | **v1.2 release** | local + cloud | JS S2, S3, S5; bench; ping tests |

P4 can run in parallel with P5–P6 because it only depends on the protocol it defines. Everything else runs in order.

## Test strategy (all phases)
- **Core (`core/`)**: pytest, no HA imports. `now` is always passed in. Aware datetimes and a `zoneinfo` time zone are passed in, never read. Branch coverage ≥ 95 % is enforced in CI.
- **Scenario tests**: one test per §6 scenario, named after it (`test_a01_...`). A small DSL/fixture advances simulated time step by step and sets inputs.
- **Simulation harness** (`tests/sim/`): a simple thermal model per zone (heat-up while its valve is open and the HP is on, cool-down otherwise, optional disturbances such as a window opening). Multi-day runs check invariants:
  - HP request never changes state within HpMinOnTime/HpMinOffTime (except season OFF, D-68).
  - Every zone stays within SetPoint ± a tolerance.
  - The sync rule fires at most once per cycle, and there is at most one calling zone.
  - Idempotency: `step` repeated with the same inputs and time gives the same result.
- **Property tests** (hypothesis): schedule overlap detection vs a brute-force minute scan, precedence, and serialization round-trip.
- **Adapter**: `pytest-homeassistant-custom-component`, with mocked switch/sensor entities, service call capture and a frozen/advanced clock.
- **Shelly scripts**: Node test runner with a minimal mock of the Shelly script API (`Timer`, `HTTPServer`, `Shelly.call`, `KVS`, `Sys` uptime/time). Then bench tests on real devices with shortened timeouts (owner, local).
- **CI** (GitHub Actions): ruff, mypy (core strict), pytest (core + adapter), JS tests, gitleaks, hassfest + HACS validation.

---

## P0 — Repository bootstrap
- Initialise git and create the **public** GitHub repo (D-46). Commit `LICENSE` (MIT, line from §0.2), `.gitignore`, `README.md` stub, `hacs.json`, the skeleton from §5.9, `examples/secrets.example.yaml`.
- `CLAUDE.md`: §0 rules (no copyright name, D-63), architecture summary, working rules, and a link to this plan.
- `pyproject.toml` (ruff, mypy, pytest, coverage), `requirements_test.txt`, `.pre-commit-config.yaml` (gitleaks, ruff).
- CI workflow: lint, tests, gitleaks, hassfest, HACS action. GitHub secret scanning ON.
- **Tests:** a placeholder core test passes in CI; the pre-commit hook rejects a fake token in a scratch commit (checked manually, then discarded).
- **Done when:** CI is green and the owner has reviewed the repo layout. *(Done: PR #1, 2026-09-27.)*

## P1 — Core models, config validation, persistence format
- `core/config.py`: `ZoneConfig` (id, name, has_valve, offset), `GlobalParams`. Ranges from §4 (BaseSetPoint 10–30, HpMin On/Off 30–180 D-81, HolidayTemp 10–25, …). Validation errors are clear (duplicate id/name, out of range). Warning if every zone has a valve (D-80).
- `core/state.py`: per-zone state (state enum, wait start, cap state, fault since, last valid reading), global state (HP last ON/OFF, calling zone, sync fired, alerts). All serialisable with `to_dict`/`from_dict` and a schema version.
- `core/io.py`: `Inputs` (temps + last_reported, actual outputs, params, season, control_active) and `Outputs` / `Event` types.
- `core/units.py`: °C conversion helpers for the adapter (D-77).
- **Tests:** validation edge cases (every range boundary, including 29/30 min rejected/accepted); state serialisation round-trip (hypothesis); unknown schema version handled.
- Parameters without a §4 range (FailsafeWindow, ValveExercise weekday/time, ActuatorFaultThreshold) are added in P11 with their features. Alerts are derived from the state fields rather than stored separately. *(Done: 2026-09-27; decisions D-84…D-87; D-88/D-89 settled for P2/P3.)*

## P2 — Core zone logic, sensor validity & HP protection (`step`)
- `core/engine.py`: `step(config, state, inputs, now) -> (outputs, new_state, events)`.
- Implements the IDLE/WAITING/HEATING state machine (§3.2–3.3): single check at the end of the wait (D-05), join while running (D-14), SetPoint raise (D-26), sync rule once per cycle (D-06/15), calling zone and tie-break (D-65), switch-off, unvalved zone (no output).
- HP protection (§3.5): min ON/OFF from actual transitions (D-66), D-20 spread excluding zones ≥ ManualMaxTemp (D-71), demand held back by min OFF with the valve open (D-64), WaitTime ∥ min OFF (D-39), first start (D-78).
- Sensor validity (D-90): plausibility range on the raw reading before the offset (D-88), `last_reported` age, the last valid reading during a short dropout, timeout from startup without any reading (D-93); SENSOR_FAULT enter/exit (§3.6), a faulty calling zone fires the sync rule (D-28), the fault follows the house. First start with the HP ON (D-91), calling zone while the request is ON without one (D-92).
- Reason texts per zone ("Calling zone", "Waiting, 12 min left", "Held by min OFF, 8 min left"), returned in `Outputs` (D-89).
- **Tests:** A1–A9, A18, A23, A26, A29 (core part), A30, A22 (core part: persistence round trip); unit tests per rule; the simulation harness is introduced here with the invariants above (including a sensor dropout mid-run); idempotency is checked on every simulated step. *(Done: 2026-09-27; decisions D-90…D-93.)*

## P3 — Core season, fault notifications, mismatch
- Sensor validity and SENSOR_FAULT moved to P2 (D-90).
- Heating season OFF: no demand, immediate stop (D-68).
- Notification events: fault start / daily reminder at SensorFaultReminder / recovery; no reminder outside the season (D-75).
- Output mismatch detection in the core: the counter over N consecutive steps where actual ≠ desired or unavailable → alert event + recovery (D-67). Inactive in shadow mode.
- **Tests:** A17 (notification and reminder), A20 (season part), A29 (mismatch alert), the mismatch counter (A27 logic part); simulation with season changes.
- **Carried over (settle in the P3 plan):**
  - *Time zone contract* (review C): `step` needs HA's time zone for local wall-clock rules (the 08:00 reminder now, schedules in P9). Pass a `zoneinfo` time zone in `Inputs` and convert `now` in the core (see test strategy); DST tests.
  - *Heat source unavailable, then back* (review E, owner 2026-09-27): no grace period, unavailable counts as OFF (D-66), because a Shelly that lost power restarts OFF. But a Wi-Fi glitch or router restart must never lead to an OFF command to a Shelly that works and is ON. The core already never wants OFF once the switch reports ON again (tests `test_heat_source_wifi_glitch_*`). **Pending owner decision:** refine D-66 so that a switch coming back ON counts as never having stopped (min ON and the cycle continue), and one coming back OFF counts as OFF since it became unavailable. This removes the side effects of a glitch: min ON starting again, possibly with the D-20 spread, and the calling zone and sync being reset.
  - *Done after the P2 review (2026-09-27):* SetPoint decrease ends the wait (review A, D-94); unvalved reason text during the spread (review B); naive `last_reported` rejected (review D).
  - *Mismatch counting*: `step` also runs on sensor updates, so the D-67 counter must count reconcile intervals, not steps. Define how the core tells them apart.

## P4 — Shelly scripts v1 + heartbeat protocol (local, bench)
- `docs/heartbeat-protocol.md`: endpoint path, request (season flag), response JSON (script running, watchdog state, season flag, parameter values — D-73), auth notes.
- `shelly_scripts/valve_watchdog.js`: config block; opens all channels after HeartbeatTimeout (5 h); stops acting on heartbeat; answers status.
- `shelly_scripts/heat_source_watchdog.js`: config block; OFF after HeartbeatTimeout; stores the season flag in KVS; boot = last heartbeat (D-72); answers status. No failsafe window yet (P12).
- **Tests:** JS unit tests with the mock API and simulated time (timeouts, heartbeat reset, status content, KVS persistence across a simulated reboot). Bench (owner): S1, S4, S6 with timeouts shortened to minutes; verifies **V2** (HTTP endpoint on 2PM Gen2 and Shelly 1 Gen3/4).
- The docs section "Shelly scripts: upload, configure, test" is written here.

## P5 — HA adapter: config, reconcile, outputs, persistence
- `__init__.py`: YAML schema (voluptuous) per §5.6 including zone `id` (D-76). Startup validation with clear errors.
- Input collection: sensor state + `last_reported`, unit conversion, switch states (unavailable = OFF).
- Reconcile loop: every ReconcileInterval and on sensor updates, serialised by a lock. Calls `step`, commands differing outputs with backoff. Shadow mode sends no commands and feeds the commanded state as feedback (D-66). The ON→OFF transition sends one final safe command set (D-69).
- Persistence: `helpers.storage.Store`, saved on state change (debounced), restored at startup (§3.8).
- **Tests (HA harness):** A21 (no service calls in shadow; final safe command on switch-off), A22 (restart during WAITING and HP ON), A25 (manual valve change corrected), A27 (unavailable valve → alert once, backoff, recovery), bad YAML → clear errors, all-valved warning.
- **Carried over:**
  - *Reconcile on heat source change* (P2 summary): also run a reconcile when the heat source switch changes state, so HP transitions are seen without waiting up to one ReconcileInterval (the core scenario times assume this).
  - *Recompute before commanding* (review E): the reconcile loop always calls `step` with the current actual states before sending commands, and never re-sends an older desired state. Otherwise a switch returning from `unavailable` could be sent a stale OFF (owner requirement: a Wi-Fi glitch must never switch a working heat pump OFF).
  - *No commands to unavailable switches*: while a switch is unavailable, do not queue commands for it. Count it for the mismatch alert (D-67) and recompute when it returns. No grace period before `unavailable` counts as OFF (owner, 2026-09-27).

## P6 — HA entities & notifications
- Entities per §5.3: climate (heat only), state/reason/effective SetPoint sensors, Hysteresis/WaitTime numbers per zone; global HP binary sensor with ON-duration, mode sensor + shadow attribute (D-79), season and control-active switches, global number entities with §4 ranges, alerts sensor.
- Notifications through the configured notify services; message texts in English.
- **Tests:** entities created per zone with stable unique IDs based on the zone id; a number entity rejects values outside its range (e.g. HpMinOnTime 20); a changed parameter reaches the core; the climate target changes BaseSetPoint; events turn into notify calls (captured).

## P7 — HA heartbeat client
- Address from the device registry (the mapped switch's Shelly config entry), otherwise from YAML (V3). Credentials from `secrets.yaml`. Async aiohttp calls every HeartbeatInterval, always including in shadow mode (D-56).
- Parses the status: unreachable / script not running after 3 consecutive failures → one alert, recovery alert (D-61); script parameter values ≠ expected config → one alert (D-73).
- **Tests:** mocked HTTP: 2 failures give no alert, 3 give one, recovery is notified (S7); the parameter-mismatch alert; heartbeat still sent in shadow mode; timeouts never block the event loop. Local check (owner): heartbeat reaches the bench Shellys and **V3** is answered.

## P8 — v1 docs, release, shadow run, go-live
- Docs per §5.8 for all of v1: README (logic in plain words, limitations, safety, hydraulic prerequisite D-80), installation (HACS + manual), configuration reference, entities, Shelly guide, troubleshooting, shadow mode and go-live checklist, CHANGELOG. `examples/configuration.example.yaml`.
- Tag `v1.0.0` and publish a GitHub release; verify installation through HACS.
- **Owner (local):** install on the live HA in shadow mode; verify **V6** (`last_reported` moves for BTHome), **V4**, **V5** (wiring), **V1** (actuator power, needed later); run shadow mode for 1–2 weeks next to the Computherm and compare decisions (entity history). Then follow the go-live checklist: remove the Computherm, wire the Shelly 1, set Control active ON.
- **Carried over:**
  - *`iot_class`* (review F): currently `local_polling`. Re-check before the release (the integration polls the Shellys locally for the heartbeat and pings healthchecks.io; `calculated` would claim no own communication).
  - *First start with the heat source already ON* (D-91): with no demand, all valves stay open for up to HpMinOnTime after the first start. Accepted by the owner (2026-09-27); handled by the owner during the test phase, no code change.
- **Done when:** v1 is running live, with no open critical issues.

## P9 — Core schedules & holiday (v1.1)
- `core/schedule.py`: one-shot and recurring (daily / weekdays) windows in local time, crossing midnight, DST-aware using the passed-in time zone (D-57). Overlap check for auto schedules (D-19). Manual schedules combine as a union (D-58). One-shot schedules are deleted after they end.
- Precedence (D-16): holiday > manual > auto > base. FORCED state with the ManualMaxTemp cap and resume (D-38). Calling zone in a manually started cycle (D-44). SENSOR_FAULT beats FORCED (D-70). Holiday (D-59). A raised SetPoint at a schedule start or holiday end → immediate HEATING (D-26).
- **Tests:** A10–A16, A24, A28; DST spring/autumn for 22:00–02:00 and windows inside the skipped/repeated hour; overlap detection against a brute-force minute scan (hypothesis); simulation with a week of schedules keeps the P2 invariants.

## P10 — HA schedules, holiday, dashboard (v1.1 release)
- Services add/delete/list with validation errors. The schedule list is a sensor attribute. Schedules are persisted.
- Form entities (D-74): selects, date/time, weekdays, temperature, Add/Delete buttons; errors appear as a persistent notification.
- Holiday switch + end datetime + HolidayTemp (D-79). The mode sensor shows holiday.
- `examples/dashboard.example.yaml` (built-in cards only, §5.7), settings page, screenshots; docs and CHANGELOG. Tag `v1.1.0`.
- **Tests:** service validation (the A12 overlap is rejected and nothing stored); a form-entity add/delete round trip; schedules survive a restart; holiday on/off through entities; the dashboard YAML loads (lovelace config parse). Owner: dashboard check on the live HA.

## P11 — Core failsafe & maintenance features (v1.2)
- Failsafe case 1: no valid sensor for > FailsafeTrigger → all valves open + HP ON during FailsafeWindow, heating season only; exits on the first valid reading; notifications.
- Valve exercise: outside the season, Monday 08:00, valves one after another for 15 min each, HP off; aborted if the season turns ON.
- Actuator fault check (only if **V1** is positive, otherwise disabled with a doc note), long run alarm at 12 h, overshoot logging (event + attribute, max 6 h).
- Add the parameters deferred from P1 to `GlobalParams`: FailsafeWindow, ValveExercise weekday/time, ActuatorFaultThreshold.
- **Tests:** A19, A20 (exercise part); an actuator fault after 10 min < 0.5 W; the long run alarm fires once; overshoot peak tracking; simulation: all sensors die for 30 h and the failsafe schedule is correct.

## P12 — Heat source failsafe script, watchdog ping, v1.2 release
- `heat_source_watchdog.js`: after FailsafeTrigger, the daily window by NTP time; with no valid time, the uptime cycle (D-72); only with the season flag ON.
- HA side: healthchecks.io ping every WatchdogPingInterval (the URL is a secret); failsafe/exercise/actuator/long-run entities and notifications wired up; docs for healthchecks setup (period 5 min, grace 30 min, D-62).
- **Tests:** JS with simulated time: S2, S3 (reboot, no clock), S5 (season OFF never heats); adapter: the ping is sent on schedule and a failure never blocks. Bench (owner): S2, S3, S5 with shortened timeouts; stop HA for real and check that the healthchecks alert arrives. Tag `v1.2.0`.

## Owner checkpoints (hardware)
| Item | Phase |
|---|---|
| V2 heartbeat endpoint on both device types | P4 |
| V3 address from the device registry | P7 |
| V4, V5, V6 | P8 |
| V1 actuator power measurable | P8 (result used in P11) |
