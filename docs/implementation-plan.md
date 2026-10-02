# Implementation Plan — `floorheat`

> Source of truth: `docs/design.md` (Spec rev. 1.2). If this plan and the spec disagree, the spec wins; fix the plan.
> Rules: one work phase at a time, committed directly to `main` (D-83); each phase ends with a summary to the owner, and the next phase starts only when the owner asks. Docs are updated in the same commit(s) as the code. `main` must stay green. The §0 rules apply everywhere.

## Overview
| Phase | Name | Feature set / release | Where | Main tests |
|---|---|---|---|---|
| P0 | Repository bootstrap | v1 | cloud | CI runs, secret scanning works |
| P1 | Core models, config validation, persistence format | v1 | cloud | unit tests |
| P2 | Core zone logic, sensor validity & HP protection | v1 | cloud | A1–A9, A18, A23, A26, A29, A30 + simulation |
| P3 | Core season, fault notifications, mismatch | v1 | cloud | A17, A20 (v1 part), A29 (alert) + simulation |
| P4 | Shelly scripts v1 + heartbeat protocol | v1 | local (bench) | JS unit tests, bench S1, S4, S6 |
| P5 | HA adapter: config, reconcile, outputs, persistence | v1 | cloud | A21, A22, A25, A27 (HA test harness) |
| P6 | HA entities & notifications | v1 | cloud | entity/notify tests |
| P7 | HA heartbeat client | v1 | cloud + local check | S7, mismatch alert, shadow heartbeat |
| P7b | HA naming conventions, config entry, rename (0.7.5) | v1 | local + owner check | config entry/device/naming tests, live check |
| P9 | Core schedules & holiday | v1.1 | cloud | A10–A16, A24, A28 + DST/overlap tests |
| P10 | HA schedules, holiday, dashboard | v1.1 | cloud | service/form-entity tests, dashboard check |
| P11 | Core failsafe & maintenance features | v1.2 | cloud | A19, A20 (exercise), long run |
| P12 | Heat source failsafe script, watchdog ping | v1.2 | local + cloud | JS S2, S3, S5; bench; ping tests |
| P8 | Documentation and release preparation (runs last, D-129) | **1.0.0 release** (v1 + v1.1 + v1.2) | local | docs complete, install test |

P4 can run in parallel with P5–P6 because it only depends on the protocol it defines. Everything else runs in order. **Order after P7b (owner, 2026-09-29, D-129):** P9 → P10 → P11 → P12 → P8 → release 1.0.0. There is no release before 1.0.0 (D-128) and no go-live step: the owner runs the integration live since P7b.

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
- Parameters without a §4 range (FailsafeWindow, ValveExercise weekday/time) are added in P11 with their features. Alerts are derived from the state fields rather than stored separately. *(Done: 2026-09-27; decisions D-84…D-87; D-88/D-89 settled for P2/P3.)*

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
- Output mismatch detection in the core: the counter over N consecutive reconcile ticks where actual ≠ desired or unavailable → alert event + recovery (D-67, D-99). Inactive in shadow mode.
- **Tests:** A17 (notification and reminder), A20 (season part), A29 (mismatch alert), the mismatch counter (A27 logic part); simulation with season changes. *(Done: 2026-09-27; decisions D-96…D-99.)*
- **Carried over (settle in the P3 plan):**
  - *Time zone contract* (review C): done in P3 as D-96 (`Inputs.time_zone`; the core converts `now`; DST tests).
  - *Done after the P2 review (2026-09-27):* SetPoint decrease ends the wait (review A, D-94); unvalved reason text during the spread (review B); naive `last_reported` rejected (review D); heat source unavailable, then back (review E, D-95; owner: no grace period, a Wi-Fi glitch must never switch a working heat pump OFF).
  - *Mismatch counting*: done in P3 as D-99 (`Inputs.reconcile_tick`).

## P4 — Shelly scripts v1 + heartbeat protocol (local, bench)
- `docs/heartbeat-protocol.md`: endpoint path, request (season flag), response JSON (script running, watchdog state, season flag, parameter values — D-73), auth notes.
- `shelly_scripts/valve_watchdog.js`: config block; opens all channels after HeartbeatTimeout (5 h); stops acting on heartbeat; answers status.
- `shelly_scripts/heat_source_watchdog.js`: config block; OFF after HeartbeatTimeout; stores the season flag in KVS; boot = last heartbeat (D-72); answers status. No failsafe window yet (P12).
- **Tests:** JS unit tests with the mock API and simulated time (timeouts, heartbeat reset, status content, KVS persistence across a simulated reboot). Bench (owner): S1, S4, S6 with timeouts shortened to minutes; verifies **V2** (HTTP endpoint on 2PM Gen2 and Shelly 1 Gen3/4).
- The docs section "Shelly scripts: upload, configure, test" is written here.
- *(Done: 2026-09-27; decisions D-100…D-105. `docs/heartbeat-protocol.md`, `docs/shelly-scripts.md`; JS tests in `tests/shelly/` run with `npm test` and in CI. The mock enforces the documented script limits; an acorn AST check keeps the scripts inside the engine's language subset. Bench S1, S4, S6 and V2 are for the owner.)*
- **Before P4 (review A of the P3 review):** the output mismatch recovery event carries `{"output": ...}` like the alert.

## P5 — HA adapter: config, reconcile, outputs, persistence
- `__init__.py`: YAML schema (voluptuous) per §5.6 including zone `id` (D-76). Startup validation with clear errors.
- Input collection: sensor state + `last_reported`, unit conversion, switch states (unavailable = OFF).
- Reconcile loop: every ReconcileInterval and on sensor updates, serialised by a lock. Calls `step`, commands differing outputs with backoff. Shadow mode sends no commands and feeds the commanded state as feedback (D-66). The ON→OFF transition sends one final safe command set (D-69).
- Persistence: `helpers.storage.Store`, saved on state change (debounced), restored at startup (§3.8).
- **Tests (HA harness):** A21 (no service calls in shadow; final safe command on switch-off), A22 (restart during WAITING and HP ON), A25 (manual valve change corrected), A27 (unavailable valve → alert once, backoff, recovery), bad YAML → clear errors, all-valved warning.
- *(Done: 2026-09-27; decisions D-106…D-113. Modules `schema.py`, `inputs.py`, `outputs.py`, `storage.py`, `controller.py`; tests in `tests/adapter/`; YAML reference in `docs/configuration.md`. Every carried-over item below is implemented and tested. Core events are logged only; notifications follow in P6.)*
- **Carried over (all done in P5):**
  - *Reconcile on heat source change* (P2 summary): also run a reconcile when the heat source switch changes state, so HP transitions are seen without waiting up to one ReconcileInterval (the core scenario times assume this).
  - *Recompute before commanding* (review E): the reconcile loop always calls `step` with the current actual states before sending commands, and never re-sends an older desired state. Otherwise a switch returning from `unavailable` could be sent a stale OFF (owner requirement: a Wi-Fi glitch must never switch a working heat pump OFF).
  - *Reconcile tick* (D-99): `Inputs.reconcile_tick` is True only for the run started by the ReconcileInterval timer; runs on sensor updates or heat source changes pass False. Pass a valve state for every valved zone (unavailable when the entity is missing).
  - *Time zone* (D-96): pass HA's configured time zone (`zoneinfo`) as `Inputs.time_zone`; follow changes of HA's time zone setting.
  - *No commands to unavailable switches*: while a switch is unavailable, do not queue commands for it. Count it for the mismatch alert (D-67) and recompute when it returns. No grace period before `unavailable` counts as OFF (owner, 2026-09-27).

## P6 — HA entities & notifications
- Entities per §5.3: climate (heat only), state/reason/effective SetPoint sensors, Hysteresis/WaitTime numbers per zone; global HP binary sensor with ON-duration, mode sensor + shadow attribute (D-79), season and control-active switches, global number entities with §4 ranges, alerts sensor.
- Notifications through the configured notify services; message texts in English.
- **Tests:** entities created per zone with stable unique IDs based on the zone id; a number entity rejects values outside its range (e.g. HpMinOnTime 20); a changed parameter reaches the core; the climate target changes BaseSetPoint; events turn into notify calls (captured).
- *(Done: 2026-09-27; decisions D-114…D-117. Platforms `climate`, `sensor`, `binary_sensor`, `number`, `switch`, `time` loaded from YAML by discovery; `entity.py`, `notifications.py`; `active_alerts` in `core/alerts.py`. Tests in `tests/adapter/` (entities, °F, notifications, a shadow trial with stand-ins, and HA's own Template switches as stand-ins). User docs: `docs/getting-started.md`, entities and notifications in `docs/configuration.md`. Every carried-over item below is done.)*

- **Carried over (all done in P6):**
  - *Settings through the controller* (D-106): the number, switch and climate entities read and change `FloorheatController.settings` (`async_set_zone_params`, `async_set_global_params`, `async_set_heating_season`, `async_set_control_active`); they do not restore their own state. Temperatures are converted to and from HA's unit system (D-77). Update the entities from `async_add_listener`.
  - *Notify targets*: add the `notify` YAML key (list of notify services) to the schema and `docs/configuration.md`; turn the core events from `async_add_event_handler` into notify calls.
  - *Trial with stand-in switches* (D-113, owner request): the owner installs after P6 on the live HA in shadow mode with the real sensors and Template switch helpers (no state template) as stand-ins for the valves and the heat source, with no Shellys. Make sure that works (nothing in P5/P6 may need a Shelly), and write the user docs for it: installation (manual copy of `custom_components/multizone_floor_heating_manager`, HACS custom repository), creating the stand-in helpers, a YAML example, what to look at during the shadow run, how to swap in the real switches later.
  - *Notifications* (P3, D-98): turn the core events into notify calls. The sensor fault reminder is one event for all faulty zones (`zone_id` None, `data.zone_ids`); start/recovery and mismatch events carry the zone id. The alerts sensor shows the active ones.

## P7 — HA heartbeat client
- Address from the device registry (the mapped switch's Shelly config entry), otherwise from YAML (V3). Credentials from `secrets.yaml`. Async aiohttp calls every HeartbeatInterval, always including in shadow mode (D-56).
- Parses the status: unreachable / script not running after 3 consecutive failures → one alert, recovery alert (D-61); script parameter values ≠ expected config → one alert (D-73).
- **Tests:** mocked HTTP: 2 failures give no alert, 3 give one, recovery is notified (S7); the parameter-mismatch alert; heartbeat still sent in shadow mode; timeouts never block the event loop. Local check (owner): heartbeat reaches the bench Shellys and **V3** is answered.
- *(Done: 2026-09-29; decisions D-120…D-123. Commits: fixed reason texts with `until` (D-123); the heartbeat client: `core/heartbeat.py` (status, parameter check, failure counting), `heartbeat.py` (HTTP client, liveness), YAML `shellys` / `no_watchdog` / heartbeat keys in `schema.py`, alert state in the `Store`. Tests in `tests/core/test_heartbeat.py` and `tests/adapter/test_heartbeat.py`. The address comes from YAML, so V3 no longer applies (first bullet above superseded by D-120). Owner answers during P7: `GET` before `POST` after a start or a failure to see `timed_out`; an uncovered switch is a config error; heartbeats only while the reconcile loop works (D-122). Local check (owner): re-enable the scripts on both 2PMs and see the heartbeats arrive.)*
- **Owner decisions (2026-09-29), write them into design.md (§5.4, §5.3, §7) with the P7 code:**
  - *Shelly address and script id:* both come from the YAML config, per Shelly. No device-registry lookup and no `Script.List`. The first bullet above and V3 no longer apply; update §5.4 and §8 accordingly.
  - *Watchdog acted / device rebooted:* a status with `state: "timed_out"` or a small `uptime_s` is only logged, no notification.
  - *No per-minute countdowns in entity states:* no state may change every minute just because time passes. Reason texts become fixed ("Waiting", "Held by min OFF", …, including any countdown in the min ON spread text); the end time of the running timer goes into an `until` attribute of the reason sensor (aware timestamp; absent when no timer runs), shown nowhere by default. The climate entity's `reason` attribute follows automatically. `binary_sensor.floorheat_heat_request` keeps its on/off state and `on_duration` (already an unrecorded attribute), but `on_since` and `on_duration` are left out while it is not running, instead of showing "Unknown" (owner, from a screenshot). Same for `until`: present only while a timer runs. Changes D-89 (texts) and the §5.7 example "Waiting, 12 min left"; new D-number. Update core reason texts, tests, `docs/configuration.md` and the trial checklist texts.
- **Carried over (from P5):**
  - *Switches without a Shelly* (D-113, decided as D-118): optional YAML key `no_watchdog`, a list of mapped switches (anything else is a config error). Listed switches get no heartbeat and raise no heartbeat alert; unlisted ones are expected to be Shellys with the script. No automatic detection, no device-type check. Document it in `configuration.md` and `getting-started.md` (stand-ins go into the list; remove them when the Shellys are swapped in), with a clear warning that a listed switch has no device failsafe if HA stops (owner request).
- **Carried over (from P4):**
  - *Protocol:* speak v1 as in `docs/heartbeat-protocol.md` (D-100): `POST` with `{"v": 1}` (valve) or `{"v": 1, "season": <heating season>}` (heat source). A non-200 answer, a timeout or a body that is not the status JSON counts as a failed call (D-61). Alert when `v` is not supported.
  - *Script id:* the endpoint path contains the device's script slot id. Decide how HA finds it: a YAML key per Shelly, or `Script.List` by script name (needs the RPC to be reachable with the same credentials).
  - *Which device is which:* HA must know which Shellys run the valve script and which the heat source script (from the mapped switch entities' devices, V3), and expect the matching `role`.
  - *Parameter check (D-73, D-101):* compare `params.heartbeat_timeout_s` (and `check_interval_s` if configured) with the expected *config* values; alert once on a difference. Ignore params HA has no expectation for.
  - *Optional:* a response with `state: "timed_out"` or a small `uptime_s` shows that the watchdog acted or the device rebooted; log it (a notification is a P7 decision).

## P7b — HA naming conventions, config entry, domain rename (0.7.5)
Inserted before P8 on 2026-09-29 (owner): entity ids must be settled before the first release. Decisions D-124 to D-128.
- Rename the integration to **Multizone Floor Heating Manager**, domain `multizone_floor_heating_manager` (folder, YAML key, storage file, texts; the Shelly scripts' messages and KVS key) (D-127). Docs: the full name, then "the integration".
- Import the YAML into a single config entry; the YAML stays the only configuration; removing the entry keeps the stored settings (D-124).
- Devices per zone ("<zone name> floor heating") and "Floor heating"; `has_entity_name`, translations (`translations/en.json`), icons (`icons.json`), *config* category for settings; HA generates the entity ids (D-125). Names as reviewed by the owner: "heat source" wording; "Manual max temperature", "Off-season valve exercise duration", reason `waiting` shown as "Waiting period"; no valve entity, no YAML `area` key.
- The reason sensor becomes an enum of fixed keys returned by the core (D-126).
- Version 0.7.5 in `manifest.json`; no tag, no release (D-128).
- **Tests:** one entry from the YAML (also after a restart), the UI step aborts, devices and generated entity ids, *config* categories, reload and unload leave nothing running, removing the entry keeps the settings, a missing YAML section fails the entry without deleting anything, a removed zone loses its device, every reason key and state has a text; all existing adapter tests on the new ids.
- **Owner (live):** switch over to 0.7.5 (YAML key, HACS, delete the old folder, re-enter settings, areas, dashboards) and check the names in the real UI. Touch-ups go out as 0.7.6 and up.
- *(Done: 2026-09-29, commits f0c1eb1, 84405fa, c007382, cc5ee78; 0.7.5. New: `config_flow.py`, `runtime.py`, `translations/en.json`, `icons.json`, `tests/adapter/test_config_entry.py`; `docs/configuration.md` has the devices, the new entity ids, the reason texts and "Upgrading from floorheat". hassfest needs `config.step` in the translations although the UI step only aborts. The owner runs 0.7.5 live and kept the wording as is.)*

## P9 — Core schedules & holiday (v1.1)
- **Carried over (from P6):** the number entities for ManualResumeDelta and HolidayTemp already exist (D-114); use their values from `GlobalParams`. The zone state sensor already lists `forced`.
- `core/schedule.py`: one-shot and recurring (daily / weekdays) windows in local time, crossing midnight, DST-aware using the passed-in time zone (D-57). Overlap check for auto schedules (D-19). Manual schedules combine as a union (D-58). One-shot schedules are deleted after they end.
- Precedence (D-16): holiday > manual > auto > base. FORCED state with the ManualMaxTemp cap and resume (D-38). Calling zone in a manually started cycle (D-44). SENSOR_FAULT beats FORCED (D-70). Holiday (D-59). A raised SetPoint at a schedule start or holiday end → immediate HEATING (D-26).
- **Tests:** A10–A16, A24, A28; DST spring/autumn for 22:00–02:00 and windows inside the skipped/repeated hour; overlap detection against a brute-force minute scan (hypothesis); simulation with a week of schedules keeps the P2 invariants.
- *(Done: 2026-09-30; decisions D-130…D-136 from the owner's answers to the P9 plan. `core/schedule.py` (windows, targets, creation check, (de)serialisation); `Inputs.schedules` / `holiday_until`, `Outputs.holiday_active` / `ended_schedules`, reason keys `forced` / `forced_too_warm` (texts in `translations/en.json` and `docs/configuration.md`); per-zone `ZoneParams.holiday_temp` (D-133). Tests: `tests/core/test_schedule.py`, P9 scenarios in `test_scenarios.py`, rules in `test_engine.py`, a one-week simulation with schedules and holiday. No HA entities or services yet.)*

## P10 — HA schedules, holiday, dashboard (v1.1)
- **Owner decision (2026-09-29):** the example dashboard gets a built-in Markdown card that explains every zone state and every reason text in plain words (what it means, why it happens). It is the only place for these explanations: no explanation attribute, no hover text (not possible without custom frontend code). Users read it while learning and may delete the card later.
- **Carried over (from P6):** the mode sensor shows `holiday` (its options already include it); update the "Used from" column in `docs/configuration.md#entities`.
- **Carried over (from P9):**
  - *Holiday temperature per zone* (D-133): a "Holiday temperature" number per zone (`ZONE_PARAM_SPECS["holiday_temp"]`, add it to `number.ZONE_KEYS`); remove the global one (`number.GLOBAL_KEYS`, `GlobalParams.holiday_temp`, its spec and translation) and, when loading stored settings that still hold the global value, copy it into every zone (since P9 the store already holds a per-zone default of 18 °C, so the presence of the global key marks the migration; it is gone after the next save). Clean up the removed entity on the owner's live system (entity registry).
  - *Schedules and holiday in the adapter* (D-136): store them in the `Store` with the settings (`schedules_to_list` / `load_schedules`, log its warnings; the holiday end as ISO 8601 UTC); pass `Inputs.schedules` / `holiday_until`; after each run delete `Outputs.ended_schedules` and switch holiday off when `Outputs.holiday_active` is false; reject new schedules with `check_new_schedule` (and `ConfigError` from `Schedule`); the mode sensor shows `holiday` from `Outputs.holiday_active`.
  - *Reason `until`* for `forced` (D-135) needs no adapter change; the dashboard explanation card (owner decision above) covers the two new keys.
- Services add/delete/list with validation errors. The schedule list is a sensor attribute. Schedules are persisted.
- Form entities (D-74): selects, date/time, weekdays, temperature, Add/Delete buttons; errors appear as a persistent notification.
- Holiday switch + end datetime + HolidayTemp (D-79). The mode sensor shows holiday.
- `examples/dashboard.example.yaml` (built-in cards only, §5.7), settings page, screenshots; docs.
- **Tests:** service validation (the A12 overlap is rejected and nothing stored); a form-entity add/delete round trip; schedules survive a restart; holiday on/off through entities; the dashboard YAML loads (lovelace config parse). Owner: dashboard check on the live HA.
- *(Done: 2026-09-30; decisions D-137…D-140 from the owner's answers to the P10 plan; version 0.8.0. Core: holiday without an end (`Inputs.holiday_on`). Adapter: per-zone holiday temperature with the migration of the stored global value; schedules, schedule counter and holiday in the `Store` settings; `schedules.py` (labels, list view), `services.py` + `services.yaml` (`add_schedule` / `delete_schedule` / `list_schedules`), `form.py` and the platforms `select`, `date`, `datetime`, `button` (holiday switch/end, schedule form); mode `holiday`; "Schedules" sensor. `examples/dashboard.example.yaml` with a history graph per zone and the explanation card; `docs/dashboard.md`. Tests: `tests/adapter/test_schedules.py`, `test_services.py`, `test_form.py`, `test_dashboard.py`. Screenshots moved to P8. The old global holiday entity is deleted by the owner, not by the integration (D-140).)*

### P10 follow-up: owner feedback on 0.8.0 → 0.8.1
Collected from the owner's live use of 0.8.0 (since 2026-10-01). Nothing is implemented until the owner has sent all feedback; then everything goes out together as **0.8.1**, with new D-numbers for spec changes.
*(Done: 2026-10-01 as **0.8.1**; D-141 heat source sensor (item 1), D-142 holiday end as date + time (item 8), D-143 dashboard (items 2, 3, 7). Items 4–6 unchanged by the owner's choice. Owner: delete the orphaned `datetime.floor_heating_holiday_end` after the update.)*
1. **Heat source status sensor** (owner, 2026-10-01, option 3 of 3): the House card cannot tell "request OFF, no demand" from "request OFF, but a zone waits for the minimum off time" — the hold is visible only in the zones' reasons. Add a global enum sensor "Heat source" on the "Floor heating" device that says what the heat source does and why, e.g. off / running / waiting for minimum off time / running for minimum on time (spreading heat) / unavailable (exact states and texts to be confirmed with the owner when implementing), with an `until` attribute while a timer runs (D-123: no countdown in the state). The core already knows these facts (`_Request` held / spreading, min ON/OFF ends). Add it to the dashboard's House card and the explanation card, `configuration.md`, and §5.3 of the spec.
2. **No header toggle** (owner, 2026-10-01): the entities cards with several switches (House: Heating season + Control active; Holiday) show a "toggle all" header switch, which is ON by default and switches them all with one tap. Set `show_header_toggle: false` on them (example dashboard + owner-local copy).
3. **"At a glance" header** (owner, 2026-10-01): view badges at the top of the daily view. Owner wants: heat request, the new heat source status (item 1), the heat pump relay, alerts count, per zone reason and state, the wanted valve state and the valve relay. Proposed in addition: mode, temperature in the zone badge; **all badges always shown**, none conditional ("if I do not see it, I don't know why"); the wanted valve state as plain text in the zone badge is enough (no per-zone entity); the zone badge also shows the reason (owner's original list). No holiday end badge (owner: it belongs on the Holiday card, item 7).
4. **Schedules UI** (owner, 2026-10-01): "not the best, but manageable and OK for now". No change in 0.8.1; revisit later.
5. **Holiday on the thermostat card** (owner, 2026-10-01): holiday worked (target changed, ended at its end), but the thermostat card keeps showing the base set point, as specified (§5.3: climate target = BaseSetPoint). Owner: **leave it as it is** for now.
6. **Relays in the zone graphs** (owner, 2026-10-01): the graphs are good; the owner wants to see the relay state to check that the relays follow. The zone graph already has the valve relay ("Valve"). Owner: **good as it is**, no change.
7. **Holiday end as text on the Holiday card** (owner, 2026-10-01): show what is actually stored, e.g. "Ends: Fri 2026-10-02 15:00" or "No end date: runs until you switch it off". Not in the glance header.
8. **Holiday end entry is a trap → split it** (bug found 2026-10-01, fix agreed by the owner): HA's frontend date-time row saves the date and the time separately. Changing the date saves it with 00:00; changing the time while the entity is empty saves nothing (`new Date("unknown")`). With D-137 (an end in the past ends a running holiday at once; every end clears the field), picking today's date while holiday runs saved today 00:00, ended the holiday at once and cleared the end; the time then saved nothing, and a later switch-on started a holiday without an end. Fix: replace `datetime.floor_heating_holiday_end` with a **"Holiday end date"** (date entity) and a **"Holiday end time"** (time entity, never empty, keeps its last value, default 12:00); end = date + time; **empty date = no end**; when holiday ends only the date is cleared. The rest of D-137 stays (changing the end while on moves it; switching on with a past end is refused). Spec change (amends D-137, new D-number); stored `holiday_end` migrates to date + time. Workaround on 0.8.0: with Holiday **off**, set the date first, then the time, then switch Holiday on.
- *Confirmed working on 0.8.0 (owner, 2026-10-01):* adding and deleting schedules; holiday on and the target change; the history graphs with states. *Correction (owner):* holiday did **not** end at its end time in the first test (cause: item 8).

## P11 — Core failsafe & maintenance features (v1.2)
- **Carried over (from P6):** the number entities for FailsafeTrigger, ValveExercise duration and LongRunAlarm already exist (D-114); wire them into the features. The mode sensor shows `failsafe`. New alert kinds go into `active_alerts` and `notifications.TITLES`.
- Failsafe case 1: no valid sensor for > FailsafeTrigger → all valves open + HP ON during FailsafeWindow, heating season only; exits on the first valid reading; notifications.
- Valve exercise: outside the season, Monday 08:00, valves one after another for 15 min each, HP off; aborted if the season turns ON.
- Long run alarm at 12 h. ~~Overshoot logging~~ dropped from 1.0.0 (owner, 2026-10-02, D-151).
- Add the parameters deferred from P1 to `GlobalParams`: FailsafeWindow, ValveExercise weekday/time.
- **Tests:** A19, A20 (exercise part); the long run alarm fires once; simulation: all sensors die for 30 h and the failsafe schedule is correct.
- *(Done: 2026-10-02 as **0.9.0**; 0.9.1 renames the failsafe settings to "failsafe operation delay / start / stop" (D-152); decisions D-147…D-151 from the owner's answers to the P11 plan. Core: `core/failsafe.py` (trigger from the newest reading, window across midnight, DST), `core/exercise.py` (stateless slots from the due time), long run and failsafe events in `core/alerts.py`, `Outputs.mode` / `valve_exercise`, reason and heat source keys `failsafe_heating` / `failsafe_waiting`, reason `valve_exercise`, `GlobalParams` failsafe window and valve exercise day/time. Adapter: time entities for the window and the exercise time, the exercise day select, mode `failsafe`, notification titles, exercise log lines. Tests: `tests/core/test_failsafe.py`, `test_exercise.py`, long run in `test_alerts.py`, A19/A20 in `test_scenarios.py`, a 34 h dead-sensor simulation, `tests/adapter/test_maintenance.py`. Overshoot logging is not in 1.0.0, D-151.)*

## P12 — Heat source failsafe script, watchdog ping (v1.2)
- `heat_source_watchdog.js`: after FailsafeTrigger, the daily window by NTP time; with no valid time, the uptime cycle (D-72); only with the season flag ON.
- **Carried over (from P4):**
  - Add the `failsafe` state in `computeState()` and its output in `targetOutput()` (the hooks exist); new CONFIG keys and `params` for FailsafeTrigger, the window and the uptime cycle (additive, protocol stays `v: 1`, D-100).
  - Season flag: heat only with `true`; never set (`null`) counts as OFF (D-105).
  - The time comes from `Shelly.getComponentStatus("sys")` (`unixtime`/`time` are `null` without NTP); decide how a clock that becomes valid during the uptime cycle is handled (spec question for P12).
  - Extend the mock with a settable clock (`sys.unixtime`, `sys.time`) for S2, S3, S5.
- HA side: healthchecks.io ping every WatchdogPingInterval (the URL is a secret); docs for healthchecks setup (period 5 min, grace 30 min, D-62). (The failsafe/exercise/long-run entities and notifications were done in P11.)
- **Tests:** JS with simulated time: S2, S3 (reboot, no clock), S5 (season OFF never heats); adapter: the ping is sent on schedule and a failure never blocks. Bench (owner): S2, S3, S5 with shortened timeouts; stop HA for real and check that the healthchecks alert arrives.
- **Carried over (P11, D-152):** the owner calls it "failsafe operation" instead of "failsafe window"; use the same wording for the heat source script's failsafe in `docs/shelly-scripts.md`, `docs/heartbeat-protocol.md` and the script's CONFIG comments (keys may stay).
- **Carried over (Gemini P7/P7b review, finding 1; owner 2026-09-29):** a season change while a heartbeat to the heat source Shelly is in flight is skipped by `_send()` (`_busy`) and only sent after the next reconcile run (≤ 1 ReconcileInterval later, not 5 min as the review says). Send it once the running call finishes (e.g. a pending-season flag checked in the task's `finally`). Test: season flipped during a slow heartbeat reaches the Shelly right after it. The review's finding 3 (a 3 min floor for the D-122 liveness limit) was rejected by the owner; D-122 stays.
- *(Done: 2026-10-02 as **0.10.0**; decisions D-153…D-155 from the owner's answers to the P12 plan. Heartbeat: a season change during a running call is sent right after it; a `failsafe` prior state is logged. `heat_source_watchdog.js` 1.1.0: failsafe operation by the device clock (`sys.time`) or the uptime cycle, new CONFIG keys `failsafe_trigger_s`, `failsafe_start` / `failsafe_stop`, `uptime_on_s` / `uptime_off_s`, `min_on_s` / `min_off_s` (failsafe only), status field `time`; the mock has a device clock; tests S2, S3, S5 and the edge cases. Adapter: `watchdog.py` (ping), YAML `watchdog_ping_url` / `watchdog_ping_interval`, shared liveness `FloorheatController.loop_alive`; `tests/adapter/test_watchdog.py`. Docs: `shelly-scripts.md` (failsafe operation, bench S2/S3/S5, accepted edge cases D-154), `heartbeat-protocol.md`, `configuration.md` and `getting-started.md` (external watchdog). Owner checks are in the to-do list below.)*
- **Gemini P9–P12 review (2026-10-02, `docs/gemini-review.md`), verdicts (owner, 2026-10-02): no code change.**
  - *Finding 1* (`except TypeError, ValueError:` in `storage.py` "breaks Python 3.12/3.13"): not relevant. The syntax is valid from Python 3.14, the integration requires 3.14.2 (HA 2026.9, minimum in `hacs.json`). Not parenthesised either (owner).
  - *Finding 2* (schedule form `KeyError` after a zone rename): not reachable. The YAML is read only at HA start, and every start creates a new controller with a fresh form ("All zones"); the zone select accepts only its current options.
  - *Finding 3* (3 min floor for the liveness check): the same as finding 3 of the P7 review, rejected on 2026-09-29; D-122 stays.
  - *Finding 4* (bench S2, S3, S5): already in the owner's to-do list.

## P8 — Documentation and release preparation (runs last, D-129)
- **To discuss at the P8 start (owner, 2026-10-02):** the development documents (`design.md`, this plan) are not released; at the release the user manual is the only truth. The Shelly scripts already refer only to the manual (test in `tests/shelly/subset.test.mjs`). Open: how far the rule reaches. The manual pages link `design.md` and cite D-numbers, and so do the integration's code comments. Also open: whether `heartbeat-protocol.md` belongs to the manual.
- **Carried over (from P10, D-140):**
  - *Climate entity of a zone without a valve* (owner, 2026-10-01: "an important note because could be confusing"): explain prominently in the user manual (README / troubleshooting, and the climate row in `configuration.md`) that the climate state is always "Heat" (the only mode), and that "Current action: Heating" means warm water flows through the zone, not that the zone wants heat (D-116). A zone without a valve therefore shows "Heating" whenever the heat source runs for another zone, while its zone state and reason say "Idle". Its `valve` attribute shows "Unknown" (no valve). Behaviour stays as it is.
  - *Dashboard screenshots* for `docs/dashboard.md` (§5.8): the owner takes 2–3 from the live HA; check them for §0.1 (no IP addresses or personal names) before committing.
  - *Upgrades in release mode:* check how removed or renamed entities (e.g. the global holiday temperature removed in 0.8.0) and stored data are handled for users who upgrade, and document it in the changelog. Until the first release the integration does not clean up old entities; the owner deletes them by hand. Also: on the owner's live HA, the per-zone holiday temperature added in 0.8.0 got the id `number.bedroom_bedroom_floor_heating_holiday_temperature` on the "Bedroom floor heating" device (name correct, other zones fine, cause unknown; the owner renamed it). Find out how HA generates ids for entities added to an existing device, and whether the integration should suggest the id.
Runs after P12; then the owner releases **1.0.0** (v1 + v1.1 + v1.2). No go-live step: the owner runs the integration live since P7b. The reference docs (`configuration.md`, `getting-started.md`, `shelly-scripts.md`) grow with each phase anyway; P8 adds and finishes the overview material, written once from the finished state.
- Docs per §5.8: README (what it does, the logic in plain words, limitations, safety notes, hydraulic prerequisite D-80), installation (HACS + manual), a check of the configuration reference and the Shelly guide, troubleshooting (sensor faults, heartbeat, failsafe, logs), CHANGELOG, `examples/configuration.example.yaml`.
- *Shadow mode to live* (for other users; the docs must be correct): switching Control active ON; the real switch states count from then on, so HpMinOffTime may apply before the first start while the zones that need heat open their valves (D-112).
- *Manual control* (D-119): Control active OFF first, then switch the relays directly; leave the watchdog scripts running; Control active ON returns to automatic (min OFF may apply, D-112).
- *First start with the heat source already ON* (D-91): with no demand, all valves stay open for up to HpMinOnTime; document it as expected behaviour.
- `iot_class` stays `local_polling` (owner, 2026-09-29; the heartbeat polls the Shellys locally).
- Version 1.0.0, tag `v1.0.0` and a GitHub release when the owner says so; verify the installation through HACS.
- **Done when:** the docs describe the finished integration and 1.0.0 is released.

## Owner to-do: checks and tests
The owner's open checks in one list (2026-10-02). Results go into design.md §8. Tick an item when done.

**Live tests of 0.9.x (P11)** — safe while the heat source relay is not wired to the heat pump and the valve relays drive no actuators:
- [ ] **Long run alarm:** set *Long run alarm* to 2 h and raise one zone's target so the heat source runs. After 2 h: notification "Floor heating: heat source long run" and an entry in the alerts. Lower the target; when the heat source switches off: "Floor heating: heat source back to normal". Set it back to 12 h.
- [ ] **Failsafe:** set *Failsafe operation delay* to 1 h, *Sensor fault timeout* to 15 min, and *Failsafe operation start/stop* to a time about 1.5 h ahead and 30 min after that. Disable every zone's temperature sensor entity (Settings → Entities → disable; template sensors too). Expect: sensor fault notifications after 15 min; 1 h after the last reading the mode is "Failsafe", reason "Failsafe, waiting for operation start" and the notification "failsafe started"; at the start time the heat source relay and every valve relay switch ON, at the stop time OFF (after the minimum on time). Enable one sensor again: "failsafe ended", mode Normal. Enable the rest and restore 24 h, 60 min and 10:00–15:00.
- [ ] **Valve exercise** (optional): heating season OFF, *Off-season valve exercise day* = today, *time* = a few minutes ahead, *duration* = 5 min. The valve relays switch ON one after another for 5 min each (YAML order, the bathroom has no valve), the heat source stays OFF; the reasons show "Valve exercise"; no notification. Restore Monday 08:00 / 15 min and the heating season.

**P12 (0.10.0):**
- [x] **Update the heat source script:** *(done 2026-10-02: 1.1.0 runs, state normal, season kept, the device time is right, default params.)*
- [ ] **Bench S2, S5, S3** with shortened values, steps in [`shelly-scripts.md`](shelly-scripts.md#bench-tests-shortened-timeouts) ("Heat source Shelly: failsafe operation"). Take the Shelly out of `shellys` for the test as described there. S3 needs a start without a valid time (unreachable time server, or the router offline): note whether `"time"` really is `null` after the power cut.
- [ ] **External watchdog:** healthchecks.io check (period 5 min, grace 30 min), `watchdog_ping_url` in the YAML, restart; the check shows pings *(done 2026-10-02)*. Still open: stop HA for longer than the grace time → "down" email; start it → "up" email.

**Shelly and hardware (open since P4/P7):**
- [ ] **S7 on the real devices:** stop the watchdog script on one Shelly → after 3 failed heartbeats (about 15 min) "Floor heating: Shelly watchdog not answering"; start it again → "answering again".
- [ ] **Shelly 1 power-loss reboot:** unplug the heat source Shelly; after power returns the relay is OFF (power-on default), the script runs and answers, and the stored season flag is kept.
- [ ] **401 check (rest of V2):** switch on Shelly authentication with the password in `secrets.yaml`: heartbeats still work; a wrong password gives the "authentication failed" alert.
- [ ] **V5:** the heat pump reacts correctly to the Shelly 1 contact on the former Computherm terminals (before using the integration as the main controller).
- [ ] **V4:** whether the secondary pump runs during hot water production (documentation only; when convenient).

| Item | Phase |
|---|---|
| V2 heartbeat endpoint on both device types | P4 (passed; 401 check open, above) |
| ~~V3 address from the device registry~~ (dropped, D-120) | – |
| V4 (secondary pump during hot water), V5 (Shelly 1 wiring) | owner, when convenient; not blocking |
| V6 | accepted (2026-09-29) |
