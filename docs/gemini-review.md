# Gemini Code Review & Architecture Log

> **Target Audience:** Claude Code / System Maintainers
> **Source of Truth:** `docs/design.md` (Spec rev. 1.2), `docs/implementation-plan.md`
> **Instructions for Claude Code:**
> - The **most recent review session** is always kept at the top of the log right below the Table of Contents.
> - Older sessions are archived below in reverse-chronological order.
> - Check the **"Action Items / Recommendations"** of the latest session before beginning the next implementation phase.

---

## Quick Navigation

- [⭐ **Latest Review: 2026-10-02 (Phases P9–P12 / Commit `75ac01e`)**](#review-session-2026-10-02--phases-p9p12-verification-commit-75ac01e)
- [Review Session: 2026-09-29 (Phases P7 & P7b / Commit `e82cdb6`)](#review-session-2026-09-29--phases-p7--p7b-verification-commit-e82cdb6)
- [Review Session: 2026-09-27 (Phases P5 & P6 / Commit `7f58b1a`)](#review-session-2026-09-27--phases-p5--p6-verification-commit-7f58b1a)
- [Review Session: 2026-09-27 (Phase P4 / Commit `44098ab`)](#review-session-2026-09-27--phase-p4-verification-commit-44098ab)
- [Review Session: 2026-09-27 (Phase P3 / Commit `c510a38`)](#review-session-2026-09-27--phase-p3-verification-commit-c510a38)
- [Review History & Session Index](#review-history--session-index)

---

## Review History & Session Index

| Date | Phase / Milestone | Commit | Status / Verdict | Link |
|---|---|---|---|---|
| **2026-10-02** | Phases P9–P12 (Schedules, holiday, dashboard, failsafe case 1, valve exercise, long run, watchdog ping, script 1.1.0) | `75ac01e` | ⚠️ Verified with 1 Critical Syntax Bug & 2 Actionable Edge Cases (700 Python tests, 112 JS tests, 100% core coverage) | [Jump to session](#review-session-2026-10-02--phases-p9p12-verification-commit-75ac01e) |
| **2026-09-29** | Phases P7 & P7b (Shelly heartbeat client, config entry, integration rename, reason keys) | `e82cdb6` | ✅ P7-P7b Complete & Verified (483 Python tests, 94 JS tests, 100% core coverage, 99.2% overall) | [Jump to session](#review-session-2026-09-29--phases-p7--p7b-verification-commit-e82cdb6) |
| **2026-09-27** | Phases P5 & P6 (HA adapter, entities, notifications, shadow mode, storage) | `7f58b1a` | ✅ P5-P6 Complete & Verified (411 Python tests, 94 JS tests, 100% core coverage) | [Jump to session](#review-session-2026-09-27--phases-p5--p6-verification-commit-7f58b1a) |
| **2026-09-27** | Phase P4 (Shelly watchdog scripts v1, heartbeat protocol) | `44098ab` | ✅ P4 Complete & Verified (305 Python tests, 94 JS tests, AST subset enforced) | [Jump to session](#review-session-2026-09-27--phase-p4-verification-commit-44098ab) |
| **2026-09-27** | Phase P3 (Season OFF, alerts, mismatch counter, v1 core complete) | `c510a38` | ✅ P3 Complete & Verified (305 tests, 100% core coverage, v1 core finished) | [Jump to session](#review-session-2026-09-27--phase-p3-verification-commit-c510a38) |
| **2026-09-27** | Phase P2 (Core zone logic, sensor validity, HP protection) | `b0376c2` | ✅ P2 Complete & Verified (249 tests, 100% core coverage, 6 observations noted) | [Jump to session](#review-session-2026-09-27--phase-p2-verification-commit-b0376c2) |

---

## Review Session: 2026-10-02 — Phases P9–P12 Verification (Commit `75ac01e`)

- **Scope:** Core schedules & holiday mode (`core/schedule.py`), schedule form & services (`form.py`, `services.py`, `select.py`, `date.py`, `time.py`, `number.py`, `button.py`), example dashboard (`examples/dashboard.example.yaml`, `tests/adapter/test_dashboard.py`), Failsafe Case 1 (`core/failsafe.py`), Off-season valve exercise (`core/exercise.py`), Long run alarm (`core/alerts.py`), External watchdog ping (`watchdog.py`, D-155), Shelly heat source script 1.1.0 (`shelly_scripts/heat_source_watchdog.js`, D-153, D-154), and persistent storage updates (`storage.py`).
- **Target Specification:** Spec rev. 1.2 (`docs/design.md`), Implementation Plan Phases P9–P12 (`docs/implementation-plan.md`), decisions D-129 through D-155.
- **Automated Verification:**
  - `pytest --cov`: **700 passed** in 53.26s (+217 tests since last review).
  - Core branch coverage: **100.00%** (845+ stmts, 0 missed across all core modules).
  - Total project test coverage: **99.53%** (3051 stmts, 754 branches across all 35 modules).
  - `npm test`: **112 passed** in 217ms across 19 test suites (`tests/shelly/**/*.test.mjs`).
  - `mypy`: **0 errors** across 71 source files.
  - `ruff`: **Clean** across all 73 files.

### 1. Actionable Findings & Technical Issues

1. **CRITICAL: Syntax Error on Python <3.14 in `storage.py:150`:**
   - **The Bug:** Line 150 of `storage.py` reads:
     ```python
     except TypeError, ValueError:
     ```
     Unparenthesized exception tuples are only allowed in Python 3.14+. On Python 3.12 and 3.13 (which Home Assistant runs on in production across HA OS 13/14, Docker, and Core), this causes an immediate `SyntaxError: multiple exception types must be parenthesized` during import, crashing the integration on startup.
   - **Action Required:** Change line 150 to:
     ```python
     except (TypeError, ValueError):
     ```

2. **Unhandled `KeyError` in Schedule Form on Zone Rename (`form.py:46`):**
   - **The Bug:** In `async_add_from_form()`:
     ```python
     zone_ids = {zone.name: zone.id for zone in controller.config.core.zones}
     ...
     zone_ids=None if form.zone == ALL_ZONES else (zone_ids[form.zone],),
     ```
     If an owner renames a zone in `configuration.yaml` and reloads HA, `controller.form` retains the previous zone name in memory (as `ScheduleForm` is transient and not re-synchronized on reload). Clicking "Add schedule" crashes with an uncaught `KeyError`.
   - **Action Required:** Guard lookup with `zone_ids.get(form.zone)` and fall back to `ALL_ZONES` or display a notification if `form.zone` is not in `zone_ids`.

3. **Liveness Guard Sensitivity Floor Needed (`controller.py:146`):**
   - **The Issue:** `loop_alive()` uses:
     ```python
     limit = HEARTBEAT_LIVENESS_TICKS * self.config.reconcile_interval
     ```
     For a configuration with `reconcile_interval: 10`, `limit` is only 30 seconds. Normal HA event loop delays (e.g. SQLite database migration, automated backup, or heavy startup) will trip `loop_alive() = False`, logging false warnings and halting heartbeats and watchdog pings.
   - **Action Required:** Floor the limit:
     ```python
     limit = max(timedelta(minutes=3), HEARTBEAT_LIVENESS_TICKS * self.config.reconcile_interval)
     ```

4. **Failsafe Script 1.1.0 Bench Verification Pending (Tests S2, S3, S5):**
   - **Status:** Heat source script 1.1.0 is deployed live on the owner's Shelly 1 Gen4, and heartbeats / status reporting are confirmed functional (`state: "normal"`).
   - **Action Required:** Complete hardware bench tests S2 (failsafe window by clock), S3 (uptime cycle without NTP after power loss), and S5 (season flag enforcement) with shortened bench timeouts before winter go-live.

---

## Review Session: 2026-09-29 — Phases P7 & P7b Verification (Commit `e82cdb6`)

- **Scope:** Shelly watchdog heartbeat client (`heartbeat.py`, `core/heartbeat.py`), schema validation for `shellys` & `no_watchdog` (`schema.py`), config entry / flow (`config_flow.py`, `runtime.py`), integration rename to `multizone_floor_heating_manager`, HA device and entity naming (`entity.py`, `climate.py`, `sensor.py`, `binary_sensor.py`, `number.py`, `switch.py`, `time.py`), translations (`translations/en.json`, `icons.json`), reason enum keys & `until` timer attributes (`core/io.py`, `core/engine.py`), checklists & trial documentation (`docs/bench-checklist.md`, `docs/trial-checklist.md`), and comprehensive test suites (`tests/adapter/test_config_entry.py`, `tests/adapter/test_heartbeat.py`, `tests/core/test_heartbeat.py`).
- **Target Specification:** Spec rev. 1.2 (`docs/design.md`), Implementation Plan Phases P7 & P7b (`docs/implementation-plan.md`), decisions D-118 through D-128.
- **Automated Verification:**
  - `pytest --cov`: **483 passed** in 24.76s (+72 tests since last review).
  - Core branch coverage: **100.00%** (845 stmts, 290 branches, 0 missed; minimum required: 95%).
  - Total project test coverage: **99.23%** (2081 stmts, 506 branches across all 26 adapter & core modules).
  - `npm test`: **94 passed** in 172ms across 16 test suites (`tests/shelly/**/*.test.mjs`).
  - `mypy`: **0 errors** across 52 source files (strict typing strictly maintained on `core/*`).
  - `ruff`: **Clean** across all 54 files (zero formatting or linter warnings).
  - Architecture purity: Pure core isolation maintained; 0 Home Assistant imports, 0 system clock calls in `core/` (enforced by AST tests in `tests/core/test_core_purity.py`).

### 1. Executive Summary
Phases P7 and P7b represent a major milestone, bringing the integration to complete feature-readiness for the v1 milestone. The system now features:
1. An asynchronous, fault-tolerant Shelly watchdog heartbeat client that continuously exercises hardware failsafes, checks script parameters, detects device reboots and timed-out watchdogs, and implements strict integration liveness protection.
2. An elegant architectural refinement of the Home Assistant adapter layer: clean domain rename to `multizone_floor_heating_manager`, Single Config Entry with YAML as the declarative source of truth, device registry integration (per-zone service devices + global service device), and compliance with modern HA entity and translation guidelines.
3. Elimination of high-churn countdown state strings in favor of static `Reason` enum keys coupled with ISO timestamp `until` attributes, preventing database bloat while improving front-end fidelity.
4. Validation against physical Shelly Plus 2PM hardware on the bench (confirming power loss behavior, S1/S4/S6 script execution, and HA control) and real-world shadow-mode trial validation (V6 sensor staleness check accepted by owner).

### 2. Verification of Phase P7 Deliverables (Shelly Heartbeat Client)

1. **Pure Core Heartbeat Logic (`core/heartbeat.py`, D-61, D-73, D-121):**
   - Implements `parse_status()` validating protocol v1, role (`valve` vs `heat_source`), state, heartbeat age, uptime, and script parameters. Defensive parsing guarantees malformed payloads, non-integer numbers, or unsupported protocol versions raise descriptive `StatusError` exceptions. Unknown extra fields are ignored for forwards compatibility (D-100).
   - Parameter discrepancy checking via `param_differences()`: compares `heartbeat_timeout_s` against expected config (default 18000s / 5h) and `check_interval_s` if configured.
   - Immutable state tracking via `HeartbeatTracking`: tracks consecutive failures (`fail_count`), alerted flags (`alerted`, `params_alerted`), and `FailureKind` (`unreachable`, `script_not_running`, `auth_failed`, `bad_answer`).
   - Pure state transition functions `record_failure()` and `record_success()` generate appropriate domain events (`WATCHDOG_FAILED`, `WATCHDOG_RECOVERED`, `WATCHDOG_PARAMS_MISMATCH`). Parameter mismatches are alerted once and clear silently on alignment.

2. **Asynchronous Adapter Client (`heartbeat.py`, D-56, D-120..D-122):**
   - Periodic dispatch loop: Executes every `heartbeat_interval` (default 5 min), running consistently in live and shadow modes (D-56).
   - Season change immediate push: When `heating_season` changes via UI, a heartbeat `POST` (`{"v": 1, "season": bool}`) is dispatched immediately to the heat source Shelly, ensuring device-level season awareness without waiting for the periodic timer.
   - HTTP Digest Authentication: Leverages `aiohttp.DigestAuthMiddleware` configured per Shelly with password, reusing device authentication nonces and avoiding repeated 401 round trips.
   - Diagnostic `GET` Status Probing: On HA startup and immediately following any failed call, reads device status via `GET` first (without resetting the watchdog heartbeat age). This allows the client to detect and log if a watchdog had timed out (with exact timeout duration) or if the device rebooted (uptime reset), without polluting user notifications.
   - Integration Liveness Guard (`_alive()`, D-122): Dispatches heartbeats only if the reconcile loop has completed a run within `HEARTBEAT_LIVENESS_TICKS * reconcile_interval` (3 ticks = 3 min). If the integration reconcile loop crashes or freezes, heartbeats stop automatically, deliberately triggering the hardware Shelly watchdog failsafe (opening valves, cutting heat request).
   - Serialization & Task Lifecycle: Uses a `_busy` set to prevent concurrent HTTP requests to the same physical device, tracks all spawned tasks in `_tasks`, and cancels them cleanly on `async_stop()`.

3. **YAML Schema & Watchdog Mapping (`schema.py`, D-118, D-120):**
   - Strict mapping validation: Every mapped switch must either belong to exactly one listed Shelly under `shellys` or be explicitly enumerated under `no_watchdog`.
   - Hardware separation enforcement: Verifies that the heat source switch is housed on a dedicated Shelly containing zero valve switches.
   - Rejects duplicate host/script_id combinations, duplicate Shelly names, and configurations where `heartbeat_interval >= heartbeat_timeout`.
   - Explicit warnings in documentation that switches in `no_watchdog` lack device-level hardware failsafes if Home Assistant halts.

4. **Alerts & Storage Integration (`controller.py`, `storage.py`):**
   - Active watchdog alerts (`WATCHDOG_FAILED`, `WATCHDOG_PARAMS_MISMATCH`) are aggregated dynamically into `controller.alerts` and reflected in `sensor.floor_heating_alerts`.
   - `HeartbeatTracking` state per Shelly is persisted across HA restarts in `.storage/multizone_floor_heating_manager`, preventing duplicate alert spam or missed recovery notifications across reboots.

### 3. Verification of Phase P7b Deliverables (Refactoring & HA Alignment)

1. **Domain & Brand Renaming (D-127):**
   - Domain transitioned cleanly from `floorheat` to `multizone_floor_heating_manager`.
   - Custom component path: `custom_components/multizone_floor_heating_manager/`.
   - Shelly script KVS key updated to `multizone_floor_heating_manager_season`.
   - Storage file renamed to `.storage/multizone_floor_heating_manager`.
   - Comprehensive upgrade guide added to `docs/configuration.md`.
   - Version set to `0.7.5` in `manifest.json`; strict policy of no Git tags or releases prior to `1.0.0` (D-128).

2. **Config Flow & Single Config Entry Architecture (D-124):**
   - `FloorheatConfigFlow` in `config_flow.py` supports `SOURCE_IMPORT` from YAML and cleanly aborts UI additions with `yaml_only` and `single_instance_allowed`.
   - The config entry holds no secrets or configuration data (data `{}`), keeping YAML as the authoritative source of truth.
   - Lifecycle management in `__init__.py`:
     - `async_setup`: Validates YAML and dispatches import flow.
     - `async_setup_entry`: Loads persistent storage, initializes controller, notifier, and heartbeat client, storing them in `entry.runtime_data` (`FloorheatRuntime`).
     - Automatic registry cleanup (`_async_remove_stale_devices`): When zones are removed from YAML, their devices and entities are automatically pruned from the Home Assistant device and entity registries.
     - `async_unload_entry`: Shuts down heartbeat tasks and controller cleanly while preserving user settings in `.storage`.

3. **Home Assistant Device & Entity Naming Conventions (D-125):**
   - Devices created as `DeviceEntryType.SERVICE`:
     - Per-zone service devices: `"<zone name> floor heating"` (`identifiers={(DOMAIN, zone.id)}`).
     - Global service device: `"Floor heating"` (`identifiers={(DOMAIN, GLOBAL_DEVICE)}`).
   - Adheres strictly to `has_entity_name = True`: entity display names contain only the property name ("Reason", "State", "Hysteresis", etc.), allowing HA to synthesize clean entity names and IDs.
   - Climate entity uses `_attr_name = None` to inherit the zone device name directly.
   - All configuration parameters assigned to `EntityCategory.CONFIG`, segregating internal tuning knobs from default user dashboards.

4. **Reason Keys & Timer Attributes (D-123, D-126):**
   - Core `step()` now emits a typed `Reason` enum in `core/io.py` (`idle`, `waiting`, `calling_zone`, `heating`, `held_by_minimum_off_time`, `spreading_heat`, `too_warm_for_spreading`, `heat_source_unavailable`, `no_reading_yet`, `sensor_fault`, `season_off`, `sensor_fault_season_off`).
   - The reason sensor (`ZoneReasonSensor`) native state is a static enum key, eliminating 60-second state write churn and database recorder bloat.
   - When active timers run (`waiting`, `held_by_minimum_off_time`, `spreading_heat`), the exact timer expiration is exposed via an aware ISO timestamp in the `until` extra state attribute.
   - `HeatRequestSensor` similarly omits `on_since` and `on_duration` attributes when the heat source is not actively running (D-123).
   - Localized strings and descriptions provided in `translations/en.json` conforming to HA hassfest standards.

### 4. Real-World Bench & Trial Results

1. **Shelly Plus 2PM Bench Verification (`docs/bench-checklist.md`):**
   - Hardware: Two Shelly Plus 2PM (Gen2, FW 1.7.5).
   - Tests S1 (watchdog timeout -> channels ON), S4 (heartbeat returns -> channels stay unchanged), S6 (GET/POST protocol), and V2 passed.
   - Power loss vs software reboot: Owner verified that physical power loss restarts channels to power-on default OFF, whereas a software reboot retains relay state. This confirms the validity of the D-95 power-loss recovery model.
   - Reconcile loop control: Floorheat driving real relays verified across 12 scenarios. Unplugging a 2PM generated expected output mismatch alerts and clean recovery.

2. **Explanation for Owner's Open Question on Test A4 ("Joining zone became calling zone"):**
   - In `docs/bench-checklist.md` (Test A4), the owner noted: *"Joined at once. Open question: the joining zone became the calling zone; to be explained."*
   - **Root Cause Analysis:** In that scenario, the heat source was kept ON either by heat pump minimum on time (`spreading_heat`) after the initial zone was satisfied, or by manual stand-in override. Under those conditions, no zone was actively demanding heat, so `state.calling_zone` was `None`. When the second zone (office) was lowered below `StartTemp`, rule 3 allowed it to heat immediately without waiting. The engine's cycle logic executed:
     ```python
     elif source.available and calling is None:
         calling = _choose_calling_zone(zones, zone_states)
     ```
     Because `calling` was `None`, the engine legitimately elected the newly joined zone as the `calling_zone` for the remaining cycle. This is intended behavior under spec §3.3 / D-92.

3. **Sensor Staleness Validation (`docs/trial-checklist.md`, V6):**
   - Live trial with BTHome/pvvx Bluetooth thermometers over extended periods showed continuous `last_reported` updates without false `sensor_fault` triggers.
   - Spec decision V6 accepted by the owner as verified.

### 5. Architectural Findings, Risks & Problems

1. **Concurrency Race Condition: Season Change Skipped During In-Flight Heartbeat (`heartbeat.py`):**
   - **The Bug:** In `_send()`, if a heartbeat call to the heat source Shelly is currently in-flight (network roundtrip ~0.5–2s), `shelly.key in self._busy` is `True`. If the user flips the `heating_season` switch during this exact window:
     - `_on_update()` fires and calls `_send([heat_source_shelly])`.
     - In `_send()`, the loop encounters `if shelly.key in self._busy: continue`.
     - The call is skipped, and `self._season_requested` is **never updated**.
     - No subsequent task is scheduled when the in-flight call finishes.
     - The heat source Shelly remains on the old season until the next periodic tick (up to 5 minutes later) or the next sensor state change.
   - **Recommendation:** Introduce a `_season_dirty` flag. If a season update occurs while `shelly.key in self._busy`, mark the flag dirty and immediately dispatch an updated heartbeat in the task's `finally:` block once the busy slot clears.

2. **Hardware Risk: Shelly Digest Authentication Unverified on Real Hardware (Test V2):**
   - **The Problem:** The owner's checklist explicitly states: *"The 401/200 part was not tested because authentication is off."*
   - In tests (`tests/adapter/test_heartbeat.py`), `DigestAuthMiddleware` is verified via a mock client. However, Shelly Gen2/Gen3 devices enforce specific digest auth constraints (`qop="auth"`, nonce recycling, MD5/SHA-256).
   - **Risk:** If an owner enables device passwords and configures `password: !secret ...` in YAML, real Shelly firmware may reject requests if subtle header incompatibilities exist.
   - **Recommendation:** Before relying on passwords in production, physically run Test V2 on hardware with authentication enabled.

3. **Liveness Guard Sensitivity on Short Reconcile Intervals (`heartbeat.py`):**
   - **The Problem:** In `_alive()`, the timeout threshold is `HEARTBEAT_LIVENESS_TICKS * self._config.reconcile_interval` (3 ticks).
   - If a user configures `reconcile_interval: 10` (the minimum permitted in schema), the threshold is only 30 seconds.
   - Any transient event loop contention in Home Assistant (e.g. SQLite database migration, automated backup, or heavy startup) exceeding 30s will trip `_alive() = False`, logging false warnings and halting heartbeats.
   - **Recommendation:** Impose a sensible minimum floor on the liveness limit: `max(timedelta(minutes=3), HEARTBEAT_LIVENESS_TICKS * self._config.reconcile_interval)`.

4. **Storage Separation on Entry Removal (`__init__.py`, `storage.py`):**
   - Removing the config entry via the HA UI cleans up devices and entities from the HA entity registry while leaving `.storage/multizone_floor_heating_manager` intact.
   - This matches decision D-124 and allows users or installers to rename zones in YAML or recreate entries without losing calibrated setpoints, hysteresis values, or historical operating states.

### 6. Readiness Checklist & Action Items for Phase P8 (v1 Release & Go-Live)
- [x] Control core v1 feature set complete (`core/`).
- [x] Shelly watchdog scripts & test harness complete (`shelly_scripts/`, `tests/shelly/`).
- [x] HA adapter: schema, reconcile loop, outputs, storage complete (P5).
- [x] HA entities, notifications, and user guides complete (P6).
- [x] HA heartbeat client, liveness guard, and watchdog alert tracking complete (P7).
- [x] Config entry, HA naming conventions, and reason keys complete (P7b).
- [x] Bench tests with Shelly Plus 2PM hardware passed (unauthenticated).
- [ ] **Action Items before Live Relays:**
  - [ ] **Fix season update race condition in `heartbeat.py`** via `_season_dirty` retry on task completion.
  - [ ] **Floor liveness guard limit** to `max(timedelta(minutes=3), ...)` to protect against short `reconcile_interval` settings.
  - [ ] **Physical verification of V2 (Digest Auth)** on real Shelly hardware if password protection is to be used.
- [ ] **Phase P8 Deliverables (v1 Release, Go-Live Checklist, Shadow Run):**
  - [ ] Update user documentation for final v1 go-live instructions.
  - [ ] Go-live checklist for owner transition from Computherm to live Shelly control.
  - [ ] 1-2 week shadow run evaluation against existing controller.
  - [ ] Verification of V1 (actuator holding power), V4 (secondary pump interlock), and V5 (heat pump contact response).
  - [ ] Tagging `v1.0.0` when approved by owner (D-128).

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
