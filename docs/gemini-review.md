# Gemini Code Review & Architecture Log

> **Target Audience:** Claude Code / System Maintainers
> **Source of Truth:** `docs/design.md` (Spec rev. 1.2), `docs/implementation-plan.md`
> **Instructions for Claude Code:**
> - The **most recent review session** is always kept at the top of the log right below the Table of Contents.
> - Older sessions are archived below in reverse-chronological order.
> - Check the **"Action Items / Recommendations"** of the latest session before beginning the next implementation phase.

---

## Quick Navigation

- [⭐ **Latest Review: 2026-09-27 (Phase P2 / Commit `b0376c2`)**](#review-session-2026-09-27--phase-p2-verification-commit-b0376c2)
- [Review History & Session Index](#review-history--session-index)

---

## Review History & Session Index

| Date | Phase / Milestone | Commit | Status / Verdict | Link |
|---|---|---|---|---|
| **2026-09-27** | Phase P2 (Core zone logic, sensor validity, HP protection) | `b0376c2` | ✅ P2 Complete & Verified (100% core coverage, 6 observations noted) | [Jump to session](#review-session-2026-09-27--phase-p2-verification-commit-b0376c2) |

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
- **Actionable Note:** Spec Rule 6 defines `HEATING -> IDLE` on SetPoint decrease. Consider whether a SetPoint decrease should also cancel `WAITING` immediately if `not zone.needs_heat()`.

#### B. Unvalved Zone Reason Text during Heat Spreading (D-20 / D-71)
- **Location:** `custom_components/floorheat/core/engine.py` (`_valve` and `_reason`, lines 314–347)
- **Detail:** During heat spreading (`request.spreading == True`), if an unvalved zone's temperature reaches `ManualMaxTemp` (25.0 °C), `_valve` returns `False`, and `_reason` returns `"Idle, at or above ManualMaxTemp"`. However, an unvalved zone has no actuator—hot water continues to circulate through it as long as the heat pump runs.
- **Actionable Note:** Minor dashboard clarity consideration: for unvalved zones, should reason reflect that circulation is continuous, or remain as-is per D-02 ("Unvalved zone: dummy output, full participation in logic")?

#### C. Timezone Contract for `step()` (Impacting P3 & P9)
- **Location:** `custom_components/floorheat/core/engine.py` (`_check`) & `custom_components/floorheat/core/io.py` (`Inputs`)
- **Detail:**
  - `_check()` verifies `now.utcoffset() is not None`.
  - In P3, `GlobalParams.sensor_fault_reminder` is `time(8, 0)` (local wall-clock time 08:00), and in P9, schedules (e.g. `22:00–02:00`) are local wall-clock times.
  - Currently, `Inputs` contains **no time zone information**, and test harness scenarios pass `now` as UTC.
  - If the HA adapter passes UTC `now` (`dt_util.utcnow()`) to `step()`, calling `now.time()` in `engine.py` evaluates UTC time, firing the 08:00 reminder at 09:00 or 10:00 local time (CET/CEST).
- **Actionable Note:** Establish the timezone contract before implementing P3: either mandate that `now` passed to `step()` must be localized to HA's configured time zone (i.e. `dt_util.now()`), or pass `time_zone: ZoneInfo` in `Inputs` so `engine` can safely convert `now.astimezone(inputs.time_zone)`.

#### D. Defensive Validation: Timezone Awareness on `ZoneInput.last_reported`
- **Location:** `custom_components/floorheat/core/engine.py` (`_read_sensor`, line 173)
- **Detail:** `reported = min(reported, now)` will raise an unhandled `TypeError: can't compare offset-naive and offset-aware datetimes` if an entity or test fixture supplies a naive datetime for `last_reported`.
- **Actionable Note:** Add a guard in `_check()` or `ZoneInput` asserting `last_reported.utcoffset() is not None`.

#### E. Transient Switch Glitches & HP Protection Timers (P5 Reconcile Loop)
- **Location:** `custom_components/floorheat/core/engine.py` (`_heat_source_times`, lines 147–160)
- **Detail:** Per D-66, an `UNAVAILABLE` switch counts as `OFF`, starting `HpMinOffTime`. If the heat pump is running and the Shelly drops off Wi-Fi for 1 reconcile interval (60s), `running` becomes `False`. Upon reconnect 60s later, `running` becomes `True`, and `_heat_source_times` detects an `OFF -> ON` transition, restarting `HpMinOnTime` (60 min) from scratch.
- **Actionable Note for P5:** In Phase P5, decide whether the HA adapter should debounce momentary `UNAVAILABLE` switch states before passing them to the core.

#### F. Manifest Classification
- **Location:** `custom_components/floorheat/manifest.json`
- **Detail:** `iot_class` is `"local_polling"`.
- **Actionable Note:** Because `floorheat` coordinates existing entities rather than polling a device directly, `"calculated"` or `"local_push"` is more idiomatic for Home Assistant.

### 3. Readiness for Phase P3 Checklist
When beginning Phase P3, the following requirements from `docs/implementation-plan.md` apply:
- [ ] Heating season OFF: immediate shutdown overriding min ON, no heating demand (D-68).
- [ ] Notification events:
  - `EventKind.SENSOR_FAULT_STARTED` on entering fault.
  - `EventKind.SENSOR_FAULT_RECOVERED` on exiting fault.
  - `EventKind.SENSOR_FAULT_REMINDER` daily at 08:00 wall-clock time while any sensor is faulty (suppressed outside heating season, D-75; tracked via `CoreState.last_fault_reminder_on`).
- [ ] Output mismatch counter:
  - Inactive in shadow mode (`Inputs.control_active is False`).
  - Track consecutive reconcile intervals where actual output != desired or is `UNAVAILABLE`.
  - Emit `OUTPUT_MISMATCH` after `output_mismatch_alert` consecutive intervals, and `OUTPUT_MISMATCH_RECOVERED` on resolution (D-67).
- [ ] Implement and verify scenarios A17, A20 (v1 part), A29 (alert part), and simulation with season transitions.
