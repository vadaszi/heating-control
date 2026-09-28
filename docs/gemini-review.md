# Gemini Code Review & Architecture Log

> **Target Audience:** Claude Code / System Maintainers
> **Source of Truth:** `docs/design.md` (Spec rev. 1.2), `docs/implementation-plan.md`
> **Instructions for Claude Code:**
> - The **most recent review session** is always kept at the top of the log right below the Table of Contents.
> - Older sessions are archived below in reverse-chronological order.
> - Check the **"Action Items / Recommendations"** of the latest session before beginning the next implementation phase.

---

## Quick Navigation

- [⭐ **Latest Review: 2026-09-27 (Phases P5 & P6 / Commit `7f58b1a`)**](#review-session-2026-09-27--phases-p5--p6-verification-commit-7f58b1a)
- [Review Session: 2026-09-27 (Phase P4 / Commit `44098ab`)](#review-session-2026-09-27--phase-p4-verification-commit-44098ab)
- [Review Session: 2026-09-27 (Phase P3 / Commit `c510a38`)](#review-session-2026-09-27--phase-p3-verification-commit-c510a38)
- [Review History & Session Index](#review-history--session-index)

---

## Review History & Session Index

| Date | Phase / Milestone | Commit | Status / Verdict | Link |
|---|---|---|---|---|
| **2026-09-27** | Phases P5 & P6 (HA adapter, entities, notifications, shadow mode, storage) | `7f58b1a` | ✅ P5-P6 Complete & Verified (411 Python tests, 94 JS tests, 100% core coverage) | [Jump to session](#review-session-2026-09-27--phases-p5--p6-verification-commit-7f58b1a) |
| **2026-09-27** | Phase P4 (Shelly watchdog scripts v1, heartbeat protocol) | `44098ab` | ✅ P4 Complete & Verified (305 Python tests, 94 JS tests, AST subset enforced) | [Jump to session](#review-session-2026-09-27--phase-p4-verification-commit-44098ab) |
| **2026-09-27** | Phase P3 (Season OFF, alerts, mismatch counter, v1 core complete) | `c510a38` | ✅ P3 Complete & Verified (305 tests, 100% core coverage, v1 core finished) | [Jump to session](#review-session-2026-09-27--phase-p3-verification-commit-c510a38) |
| **2026-09-27** | Phase P2 (Core zone logic, sensor validity, HP protection) | `b0376c2` | ✅ P2 Complete & Verified (249 tests, 100% core coverage, 6 observations noted) | [Jump to session](#review-session-2026-09-27--phase-p2-verification-commit-b0376c2) |

---

## Review Session: 2026-09-27 — Phases P5 & P6 Verification (Commit `7f58b1a`)

- **Scope:** Complete Home Assistant adapter layer (`custom_components/floorheat/`), platforms (`climate`, `sensor`, `binary_sensor`, `switch`, `number`, `time`), notifications (`notifications.py`), reconcile loop (`controller.py`), output commanding (`outputs.py`), storage persistence (`storage.py`), user documentation (`docs/configuration.md`, `docs/getting-started.md`), and adapter integration tests (`tests/adapter/`).
- **Target Specification:** Spec rev. 1.2 (`docs/design.md`), Implementation Plan Phases P5 & P6 (`docs/implementation-plan.md`), decisions D-106 through D-117.
- **Automated Verification:**
  - `pytest --cov`: **411 passed** in 20.40s (+106 adapter tests).
  - Core branch coverage: **100.00%** (735 stmts, 252 branches, 0 missed; minimum required: 95%).
  - `npm test`: **94 passed** in 207ms (Shelly watchdog tests remain 100% green).
  - `mypy`: 0 errors across 45 source files (strict typing enforced throughout `custom_components/floorheat/`).
  - `ruff`: Clean (formatting and all linter rules pass across 47 files).
  - Architecture purity: Pure core isolation maintained; HA imports strictly confined to adapter package.

### 1. Executive Summary
Phases P5 and P6 are **complete, robust, and fully verified**. The integration provides a production-grade Home Assistant adapter around the pure core engine. Key architectural mandates (asynchronous safety, command backoff, shadow-mode feedback, and persistence) are implemented cleanly with thorough automated test coverage.

### 2. Verification of P5 & P6 Deliverables

1. **Configuration & Startup Validation (`schema.py`, D-107, D-111):**
   - Two-phase validation: Voluptuous validates structure, zone IDs/names, and prevents duplicate switch assignments. Temperatures in YAML are converted to °C.
   - Fault-tolerant startup: Missing or unregistered entities are detected after Home Assistant finishes starting (`async_at_started`), issuing an error log and persistent notification while allowing the integration to run safely with those entities marked unavailable (D-107).

2. **Reconcile Loop & Concurrency (`controller.py`, D-109):**
   - Serialized execution via an `asyncio.Lock`.
   - Distinguishes periodic timer runs (`Inputs.reconcile_tick = True`) from sensor updates, switch state changes, and UI setting adjustments (`reconcile_tick = False`), ensuring output mismatch counters advance only on real ReconcileInterval ticks (D-99).
   - In live mode, outputs are recomputed against actual switch states before issuing commands, preventing stale commands from ever being sent to switches returning from `UNAVAILABLE` (D-95).

3. **Output Commanding & Command Backoff (`outputs.py`, D-108):**
   - Commands (`switch.turn_on` / `switch.turn_off`) are sent immediately on desired state change, followed by exponential backoff (1m, 2m, 4m, 8m, then every 15m) if switches do not follow.
   - `UNAVAILABLE` switches are never commanded or queued; when a switch reconnects, commanding resumes immediately.
   - Commands execute with `asyncio.timeout(30)` (`COMMAND_TIMEOUT`) and cancel cleanly during shutdown.

4. **Shadow Mode & Final Safe OFF (`controller.py`, D-66, D-109, D-110):**
   - In shadow mode (`control_active = False`), commands are suppressed, and desired outputs are fed back to `step()` via a fixed-point convergence loop (`_MAX_SHADOW_RUNS = 3`).
   - When switching `control_active` from ON to OFF, all switches not reporting OFF are commanded OFF with backoff until confirmed. The list of switches awaiting final OFF (`pending_off`) is persisted across HA restarts.

5. **Storage & UI Settings Ownership (`storage.py`, D-106):**
   - The adapter owns all user-facing settings (`Settings`), persisting them alongside `CoreState` in `.storage/floorheat`.
   - Disk writes are debounced and coalesced (`SAVE_DELAY = 30s`), with an immediate flush on `EVENT_HOMEASSISTANT_STOP`.
   - Corrupted or unparseable settings fall back to safe defaults with logged warnings.

6. **Home Assistant Entities (`climate`, `sensor`, `binary_sensor`, `switch`, `number`, `time`, D-114..D-116):**
   - Predictable, fixed entity IDs (`<platform>.floorheat_<zone_id>_<key>`).
   - `climate` entity supports `heat` mode only (per-zone turning OFF is disallowed by design). `hvac_action` correctly reflects active flow (`heating` when HP is ON and valve is open/unvalved).
   - `number` entities expose all §4 global and per-zone parameters, enforcing min/max boundaries and bidirectional temperature delta conversions (°C, °F, K).
   - `time` entity exposes `sensor_fault_reminder` in local wall-clock time.
   - `binary_sensor.floorheat_heat_request` tracks desired heat pump state and running duration (`on_duration` unrecorded to reduce database bloat).
   - `sensor.floorheat_alerts` reflects active alerts in both state count and attributes.

7. **Notification Dispatch (`notifications.py`, D-117):**
   - Dispatches core events to configured `notify.<name>` targets. Supports both legacy notify services and modern notify entities (`notify.send_message`).
   - Non-blocking execution with timeouts; missing targets generate a persistent notification at startup.

8. **Trial with Stand-In Switches (D-113):**
   - `docs/getting-started.md` details how to run shadow mode using Home Assistant Template Switch helpers as stand-ins before physical Shellys are wired. Fully validated in `test_shadow.py`.

---

### 3. Considerations for Upcoming Phase P7 (HA Heartbeat Client)

1. **Heartbeat Dispatch Loop:**
   - Must run every `HeartbeatInterval` (default 5 min), including in shadow mode (D-56).
   - Must pass `FloorheatController.settings.heating_season` in the `POST` body to the heat source Shelly (`{"v": 1, "season": bool}`).
2. **Script ID Discovery:**
   - Endpoint URL structure: `http://<device>/script/<script-id>/heartbeat`.
   - P7 should finalize how HA resolves `<script-id>` (via device registry, configuration, or `Script.List` RPC).
3. **Alerting on Script Failure & Parameter Divergence:**
   - 3 consecutive unreachable calls or non-200 responses -> raise persistent alert (D-61).
   - Script `params.heartbeat_timeout_s` != expected HA config -> raise parameter mismatch alert (D-73).

---

### 4. Readiness Checklist for Phase P7 (HA Heartbeat Client)
- [x] Control core v1 feature set complete (`core/`).
- [x] Shelly watchdog scripts & test harness complete (`shelly_scripts/`, `tests/shelly/`).
- [x] HA adapter: schema, reconcile loop, outputs, storage complete (P5).
- [x] HA entities, notifications, and user guides complete (P6).
- [ ] **Phase P7 Deliverables:**
  - [ ] Device address resolution (device registry or fallback).
  - [ ] Asynchronous `aiohttp` heartbeat client sending `POST` / `GET`.
  - [ ] Authentication via `secrets.yaml` (HTTP Digest auth).
  - [ ] Status parser & role validator ("valve" vs "heat_source").
  - [ ] Alerting: 3-failure unreachable alert and parameter mismatch alert (D-61, D-73).
  - [ ] Mocked HTTP tests for P7.

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

## Review Session: 2026-09-27 — Phase P3 Verification (Commit `c510a38`)

- **Scope:** Full codebase review up to Phase P3 (`custom_components/floorheat/core/alerts.py`, `engine.py`, `state.py`, `io.py`, `tests/core/test_alerts.py`, `tests/sim/test_simulation.py`, `tests/core/test_scenarios.py`).
- **Target Specification:** Spec rev. 1.2 (`docs/design.md`), Implementation Plan Phase P3 (`docs/implementation-plan.md`).
- **Automated Verification:**
...
