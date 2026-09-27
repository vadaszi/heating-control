# Gemini Code Review & Architecture Log

> **Target Audience:** Claude Code / System Maintainers
> **Source of Truth:** `docs/design.md` (Spec rev. 1.2), `docs/implementation-plan.md`
> **Instructions for Claude Code:**
> - The **most recent review session** is always kept at the top of the log right below the Table of Contents.
> - Older sessions are archived below in reverse-chronological order.
> - Check the **"Action Items / Recommendations"** of the latest session before beginning the next implementation phase.

---

## Quick Navigation

- [⭐ **Latest Review: 2026-09-27 (Phase P4 / Commit `44098ab`)**](#review-session-2026-09-27--phase-p4-verification-commit-44098ab)
- [Review Session: 2026-09-27 (Phase P3 / Commit `c510a38`)](#review-session-2026-09-27--phase-p3-verification-commit-c510a38)
- [Review Session: 2026-09-27 (Phase P2 / Commit `b0376c2`)](#review-session-2026-09-27--phase-p2-verification-commit-b0376c2)
- [Review History & Session Index](#review-history--session-index)

---

## Review History & Session Index

| Date | Phase / Milestone | Commit | Status / Verdict | Link |
|---|---|---|---|---|
| **2026-09-27** | Phase P4 (Shelly watchdog scripts v1, heartbeat protocol) | `44098ab` | ✅ P4 Complete & Verified (305 Python tests, 94 JS tests, AST subset enforced) | [Jump to session](#review-session-2026-09-27--phase-p4-verification-commit-44098ab) |
| **2026-09-27** | Phase P3 (Season OFF, alerts, mismatch counter, v1 core complete) | `c510a38` | ✅ P3 Complete & Verified (305 tests, 100% core coverage, v1 core finished) | [Jump to session](#review-session-2026-09-27--phase-p3-verification-commit-c510a38) |
| **2026-09-27** | Phase P2 (Core zone logic, sensor validity, HP protection) | `b0376c2` | ✅ P2 Complete & Verified (249 tests, 100% core coverage, 6 observations noted) | [Jump to session](#review-session-2026-09-27--phase-p2-verification-commit-b0376c2) |

---

## Review Session: 2026-09-27 — Phase P4 Verification (Commit `44098ab`)

- **Scope:** Shelly watchdog scripts (`shelly_scripts/heat_source_watchdog.js`, `valve_watchdog.js`), protocol specs (`docs/heartbeat-protocol.md`, `docs/shelly-scripts.md`), test harness and runtime mock (`tests/shelly/`).
- **Target Specification:** Spec rev. 1.2 (`docs/design.md`), Implementation Plan Phase P4 (`docs/implementation-plan.md`), decisions D-100 through D-105.
- **Automated Verification:**
  - `npm test`: **94 passed** in 161ms across 16 test suites (`tests/shelly/**/*.test.mjs`).
  - AST language subset analysis (`tests/shelly/subset.mjs`): Acorn parser guarantees scripts adhere strictly to the supported mJS / Espruino subset (no `const`, no arrow functions, no closures/hoisting, no ES6+ constructs that could crash the Shelly firmware).
  - Runtime constraint verification (`tests/shelly/shelly_mock.mjs`): Simulates Gen2/Gen3 execution environment, verifying that scripts strictly obey hardware resource limits (max 5 timers, max 5 endpoints, max 5 RPCs in-flight, KVS key/value lengths).
  - `pytest --cov`: **305 passed** (100% statement and branch coverage on Python `core/`).
  - `mypy`: 0 errors across 22 source files.
  - `ruff`: Clean.

### 1. Executive Summary
Phase P4 is **complete, exceptionally well-tested, and fully verified**. The two watchdog scripts (`valve_watchdog.js` and `heat_source_watchdog.js`) implement the required failsafe behaviors with defensive hardware safeguards. The heartbeat protocol specification and bench testing guides are clear and actionable.

### 2. Verification of P4 Deliverables

1. **Valve Watchdog Script (`shelly_scripts/valve_watchdog.js`):**
   - Automatically probes switch components 0..7 or honors a user-specified `CONFIG.switch_ids`.
   - On timeout (`heartbeat_timeout_s`, default 5 h), sets all channels `ON` (open) while throttling in-flight RPCs (`MAX_PENDING_CALLS = 4`) to prevent Shelly RPC queue exhaustion.
   - Idempotently re-asserts `ON` at each check interval if an external command switches any channel `OFF` while timed out (D-103).
   - Once a valid heartbeat arrives, immediately transitions back to `normal` and ceases commanding outputs, letting Home Assistant resume control (S4).

2. **Heat Source Watchdog Script (`shelly_scripts/heat_source_watchdog.js`):**
   - On timeout (`heartbeat_timeout_s`, default 5 h), sets the heat request output `OFF`.
   - Idempotently re-asserts `OFF` at each check interval if an external command turns the output `ON` while timed out (D-103).
   - Validates that heartbeat `POST` requests include `{"season": true/false}`; rejects malformed or missing season flags with `400` so HA can detect failed calls.
   - Flash protection: writes the season flag to KVS only when it changes from the stored value, avoiding unnecessary flash wear.
   - On boot, reads stored season from KVS; correctly prioritizes any incoming heartbeat received while the KVS read is in-flight.

3. **Protocol & Documentation (`docs/heartbeat-protocol.md`, `docs/shelly-scripts.md`):**
   - Documented endpoints (`POST` for heartbeat + status, `GET` for status only; 405 for other methods).
   - Standardized JSON status schema (`v`, `role`, `script_version`, `running`, `state`, `heartbeat_seen`, `heartbeat_age_s`, `uptime_s`, `switches`, `params`).
   - Comprehensive bench test procedures provided for the owner (S1, S4, S6, reboot checks, and V2 hardware verification).
   - Strongly emphasizes setting device power-on defaults to `OFF` (critical for D-95 logic).

---

### 3. Considerations for Upcoming Phases (P5 & P7)

#### A. Phase P5 (Home Assistant Adapter - Reconcile Loop & Outputs)
- **Recompute before commanding:** The reconcile loop must always execute `step()` with actual observed switch states before sending commands. Stale commands must never be issued to switches returning from `UNAVAILABLE` (D-95).
- **Graceful coexistence with watchdogs:** While an output is `UNAVAILABLE`, do not queue commands. When it reconnects, allow the mismatch tracker and reconcile loop to bring it into alignment.

#### B. Phase P7 (Home Assistant Heartbeat Client)
- **Script ID discovery:** The endpoint URL requires `<script-id>` (`http://<device>/script/<id>/heartbeat`). P7 should define how HA obtains this ID: either via explicit YAML configuration (e.g. `script_id: 1`) or via automatic discovery using Shelly's `Script.List` RPC method.
- **Role verification:** HA must ensure the script's reported `role` matches the expected device role (`"valve"` for valve controllers, `"heat_source"` for the heat pump relay).
- **Parameter check (D-73):** HA will compare `params.heartbeat_timeout_s` against expected values and raise an alert if they diverge.

---

### 4. Readiness Checklist for Phase P5 (HA Adapter)
- [x] Control core v1 feature set complete (`core/`).
- [x] Shelly watchdog scripts & tests complete (`shelly_scripts/`, `tests/shelly/`).
- [ ] **Phase P5 Deliverables:**
  - [ ] Voluptuous YAML schema validation (`custom_components/floorheat/__init__.py`).
  - [ ] Input collection (`ZoneInput`, unit conversion, switch states).
  - [ ] Reconcile loop (`ReconcileInterval` timer tick + sensor change triggers, serialized with an `asyncio.Lock`).
  - [ ] Output commanding with backoff and shadow mode gating (D-66, D-69).
  - [ ] State persistence via `homeassistant.helpers.storage.Store`.
  - [ ] HA test harness scenarios: A21, A22, A25, A27.

---

## Review Session: 2026-09-27 — Phase P3 Verification (Commit `c510a38`)

- **Scope:** Full codebase review up to Phase P3 (`custom_components/floorheat/core/alerts.py`, `engine.py`, `state.py`, `io.py`, `tests/core/test_alerts.py`, `tests/sim/test_simulation.py`, `tests/core/test_scenarios.py`).
- **Target Specification:** Spec rev. 1.2 (`docs/design.md`), Implementation Plan Phase P3 (`docs/implementation-plan.md`).
- **Automated Verification:**
  - `pytest --cov`: **305 passed** in 10.34s (+56 tests from P2).
  - Core branch coverage: **100.00%** (722 stmts, 242 branches, 0 missed; minimum required: 95%).
  - `mypy`: 0 errors across 22 source files (strict typing enforced on all core modules).
  - `ruff`: Clean (all linter rules and formatting pass).
  - Purity guards (`test_core_purity.py`): AST checks verify zero HA imports and zero system clock reads in `core/`.

### 1. Executive Summary
Phase P3 is **complete and fully verified**. With this milestone, the **v1 control core** (`custom_components/floorheat/core/`) is **feature-complete**. All items identified in the previous review (P2 session) were addressed cleanly and formalized into spec decisions D-94 through D-99.

### 2. Verification of P3 Deliverables & Resolved Feedback

1. **Resolution of P2 Review Items:**
   - **D-94 (Review A):** Lowering SetPoint while in `WAITING` now immediately cancels the wait and transitions the zone to `IDLE` if `not zone.needs_heat()`.
   - **Unvalved reason text (Review B):** `_reason()` now explicitly accounts for unvalved zones during heat spreading (`if valve or not zone.config.has_valve`), correctly explaining that heat flows continuously.
   - **D-96 (Review C):** Explicit time zone contract established via `Inputs.time_zone: tzinfo`. The core converts `now` for local wall-clock rules; DST spring gap and autumn fold behaviors are comprehensively covered by unit tests.
   - **Defensive Timezone Guard (Review D):** `_check()` enforces timezone awareness on `ZoneInput.last_reported` and `now`, rejecting naive datetimes cleanly.
   - **D-95 (Review E):** Wi-Fi glitches / transient switch unavailabilities no longer turn off a running heat pump or reset `hp_last_on_at`. While unavailable, the cycle is preserved; when reporting `ON` again, the original `hp_last_on_at` is preserved; when reporting `OFF`, it counts as stopped from `hp_unavailable_since`.

2. **Heating Season OFF (D-68, D-97):**
   - Switching season OFF mid-cycle immediately stops the heat pump request and closes all valves, overriding `HpMinOnTime`.
   - Zones without faults enter `IDLE`; faulty zones retain `SENSOR_FAULT` but their valves are held closed and no demand is created.
   - When season turns back ON, normal thermostatic logic resumes from `IDLE` with `WaitTime` (rule 1).

3. **Notification Events & Daily Reminder (`alerts.py`, D-75, D-98):**
   - `SENSOR_FAULT_STARTED` and `SENSOR_FAULT_RECOVERED` fire strictly on state transition; stored faults do not repeat notifications on HA restart.
   - Daily 08:00 reminder (`SENSOR_FAULT_REMINDER`) lists all zones faulty since an earlier local day, sent once per day with catch-up logic if HA was down or season was OFF at 08:00. Suppressed outside heating season.

4. **Output Mismatch Counter (`alerts.py`, D-67, D-99):**
   - Counter advances strictly on `Inputs.reconcile_tick` (at most once per `now`), avoiding spurious counts on sensor updates or switch event loops.
   - First tick after a new desired state is not counted, giving physical actuators and relays a full interval to respond.
   - Emits `OUTPUT_MISMATCH` after `output_mismatch_alert` consecutive ticks and `OUTPUT_MISMATCH_RECOVERED` on return to commanded state.
   - Inactive in shadow mode (`control_active is False`), resetting tracking silently.

5. **Acceptance Scenarios & Multi-Day Thermal Simulation:**
   - Scenarios A17, A20, A27 (logic part), and A29 (alert and glitch parts) pass.
   - Multi-day simulation (`test_season_changes`) tests frequent mid-cycle season changes, sensor dropouts, and reminder catch-up across several days without invariant violations.

---

## Review Session: 2026-09-27 — Phase P2 Verification (Commit `b0376c2`)

- **Scope:** Full codebase review up to Phase P2 (`custom_components/floorheat/core/`, root files, `tests/`).
- **Target Specification:** Spec rev. 1.2 (`docs/design.md`), Implementation Plan Phase P2 (`docs/implementation-plan.md`).
- **Automated Verification:**
  - `pytest --cov`: 249 passed in 5.99s.
  - Core branch coverage: **100.00%** (minimum required: 95%).
  - `mypy`: 0 errors across 20 source files (strict typing enforced on `core/`).
  - `ruff`: Clean (formatting and all linter rules pass).
  - Purity guards (`test_core_purity.py`): AST checks verify zero HA imports and zero system clock reads in `core/`.

### 1. Executive Summary
Phase P2 is cleanly completed and matches spec requirements. Immutability via `dataclass(frozen=True)` is rigorously maintained, state serialization round-trips are proven via Hypothesis, and the multi-day thermal simulation harness proves short-cycling invariants under disturbances.

### 2. Specific Findings & Observations

#### A. State Machine & UX: SetPoint Decrease while `WAITING`
- **Location:** `custom_components/floorheat/core/engine.py` (`_transition`, lines 231–237)
- **Detail:**
  ```python
  started = zone_state.wait_started_at or now
  zone_state = dataclasses.replace(zone_state, wait_started_at=started)
  if now - started < zone.params.wait_time:
      return zone_state
  return _set_mode(zone_state, _HEATING if zone.needs_heat() else _IDLE)
  ```
  If a zone enters `WAITING` (e.g. 30 min timer) and the user decreases `BaseSetPoint` 5 minutes in (e.g., from 22.0 °C to 18.0 °C while room is 21.0 °C), `raised` is `False`. Because `now - started < wait_time`, the zone **remains in `WAITING`** for the remaining 25 minutes, showing `"Waiting, 25 min left"` on the dashboard, even though `RoomTemp > StopTemp`. It only transitions to `IDLE` after the full timer expires.
- **Actionable Note:** Spec Rule 6 defines `HEATING -> IDLE` on SetPoint decrease. Consider whether a SetPoint decrease should also cancel `WAITING` immediately if `not zone.needs_heat()`. *(Resolved in P3 as D-94)*

#### B. Unvalved Zone Reason Text during Heat Spreading (D-20 / D-71)
- **Location:** `custom_components/floorheat/core/engine.py` (`_valve` and `_reason`, lines 314–347)
- **Detail:** During heat spreading (`request.spreading == True`), if an unvalved zone's temperature reaches `ManualMaxTemp` (25.0 °C), `_valve` returns `False`, and `_reason` returns `"Idle, at or above ManualMaxTemp"`. However, an unvalved zone has no actuator—hot water continues to circulate through it as long as the heat pump runs.
- **Actionable Note:** Minor dashboard clarity consideration: for unvalved zones, should reason reflect that circulation is continuous, or remain as-is per D-02 ("Unvalved zone: dummy output, full participation in logic")? *(Resolved in P3)*

#### C. Timezone Contract for `step()` (Impacting P3 & P9)
- **Location:** `custom_components/floorheat/core/engine.py` (`_check`) & `custom_components/floorheat/core/io.py` (`Inputs`)
- **Detail:**
  - `_check()` verifies `now.utcoffset() is not None`.
  - In P3, `GlobalParams.sensor_fault_reminder` is `time(8, 0)` (local wall-clock time 08:00), and in P9, schedules (e.g. `22:00–02:00`) are local wall-clock times.
  - Currently, `Inputs` contains **no time zone information**, and test harness scenarios pass `now` as UTC.
  - If the HA adapter passes UTC `now` (`dt_util.utcnow()`) to `step()`, calling `now.time()` in `engine.py` evaluates UTC time, firing the 08:00 reminder at 09:00 or 10:00 local time (CET/CEST).
- **Actionable Note:** Establish the timezone contract before implementing P3: either mandate that `now` passed to `step()` must be localized to HA's configured time zone (i.e. `dt_util.now()`), or pass `time_zone: ZoneInfo` in `Inputs` so `engine` can safely convert `now.astimezone(inputs.time_zone)`. *(Resolved in P3 as D-96)*

#### D. Defensive Validation: Timezone Awareness on `ZoneInput.last_reported`
- **Location:** `custom_components/floorheat/core/engine.py` (`_read_sensor`, line 173)
- **Detail:** `reported = min(reported, now)` will raise an unhandled `TypeError: can't compare offset-naive and offset-aware datetimes` if an entity or test fixture supplies a naive datetime for `last_reported`.
- **Actionable Note:** Add a guard in `_check()` or `ZoneInput` asserting `last_reported.utcoffset() is not None`. *(Resolved in P3)*

#### E. Transient Switch Glitches & HP Protection Timers (P5 Reconcile Loop)
- **Location:** `custom_components/floorheat/core/engine.py` (`_heat_source_times`, lines 147–160)
- **Detail:** Per D-66, an `UNAVAILABLE` switch counts as `OFF`, starting `HpMinOffTime`. If the heat pump is running and the Shelly drops off Wi-Fi for 1 reconcile interval (60s), `running` becomes `False`. Upon reconnect 60s later, `running` becomes `True`, and `_heat_source_times` detects an `OFF -> ON` transition, restarting `HpMinOnTime` (60 min) from scratch.
- **Actionable Note for P5:** In Phase P5, decide whether the HA adapter should debounce momentary `UNAVAILABLE` switch states before passing them to the core. *(Resolved in P3 as D-95)*

#### F. Manifest Classification
- **Location:** `custom_components/floorheat/manifest.json`
- **Detail:** `iot_class` is `"local_polling"`.
- **Actionable Note:** Because `floorheat` coordinates existing entities rather than polling a device directly, `"calculated"` or `"local_push"` is more idiomatic for Home Assistant. *(Carried over to P8)*
