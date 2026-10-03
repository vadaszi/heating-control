# Control core

`core/engine.py`:

```python
step(config: CoreConfig, state: CoreState, inputs: Inputs, now: datetime) -> tuple[Outputs, CoreState, list[Event]]
```

## Contract

- **Pure and deterministic.** The same arguments always give the same result. No clock, no I/O, no HA imports.
- **A fixed point per call.** Calling `step` again with its own new state, the same inputs and the same `now` returns the same outputs and state and no events. The adapter may therefore run it as often as it likes, and no notification is repeated.
- **Time.** `now` is an aware datetime in any time zone; `inputs.time_zone` is HA's time zone. The core converts `now` for everything that uses local wall-clock time (schedules, the reminder time, the failsafe window, the valve exercise). A local time in the spring DST gap is shifted by the gap length (02:30 → 03:30); a time in the repeated autumn hour means its first occurrence.
- **Units:** °C throughout.
- **Validation:** `CoreConfig`, `ZoneParams`, `GlobalParams` and `Schedule` validate themselves on construction (`ConfigError` lists every problem), so invalid values never reach `step`.

## Inputs (`core/io.py`)

| Field | Meaning |
|---|---|
| `zones[zone_id]` | `reading` (raw °C before the offset, `None` if not numeric), `last_reported` (the sensor's aware `last_reported`), `valve` (`OutputState` ON / OFF / UNAVAILABLE; `None` for a zone without a valve). Every configured zone must be present. |
| `heat_source` | The heat source switch's `OutputState`. In shadow mode the adapter passes the commanded state. |
| `zone_params`, `global_params` | The UI values. |
| `heating_season`, `shadow_mode` | The two switches. `shadow_mode` True: no commands (the mismatch alert is inactive). |
| `time_zone` | HA's time zone (`tzinfo`). |
| `reconcile_tick` | True only for the run started by the reconcile timer; the output mismatch counter counts these ticks. |
| `schedules`, `holiday_on`, `holiday_until` | Owned and stored by the adapter. Holiday is active while it is on and `now` is before its end (no end: until switched off). |

## Outputs

| Field | Meaning |
|---|---|
| `heat_source_on`, `valves[zone_id]` | The desired outputs (valved zones only). |
| `zones[zone_id]` | `ZoneReport`: `reason` (a fixed `Reason` key), `room_temp`, `setpoint` (effective target), `until` (end of the timer the reason names). The zone state (`ZoneState.mode`) and the calling zone are read from the new state. |
| `heat_source_status`, `heat_source_until` | What the heat source does and why (`HeatSourceStatus`); the first that applies wins: unavailable, season off, held by min OFF, spreading, failsafe heating, failsafe waiting, heating, idle. |
| `mode` | normal / holiday / failsafe. |
| `holiday_active`, `ended_schedules` | The adapter switches holiday off when it is no longer active and deletes the ended one-shot schedules. |
| `valve_exercise` | The zone whose valve is being exercised, if any. |

Reasons and statuses are **fixed keys**, never countdowns: an entity state must not change every minute only because time passes. The end of a running timer goes into `until`.

## Order within a step

1. Heat source transitions from the actual switch state (ON/OFF times; unavailable counts as OFF; back ON after ON means it never stopped).
2. Readings and sensor faults; each zone's effective target and manual window from holiday and schedules (`schedule.zone_target`).
3. Zone transitions: the zone rules, FORCED with its temperature cap; outside the heating season every zone without a fault is IDLE.
4. Failsafe (`failsafe.py`): inside its window every zone has demand.
5. Sync rule, the heat request with min ON/OFF, the calling zone; outside the season the request is OFF at once, overriding min ON.
6. Valves and reasons; in the failsafe every valve follows the heat source; outside the season the valve exercise opens one valve at a time (`exercise.py`).
7. Notification events and output mismatch tracking (`alerts.py`).

The rules themselves are described for users in [How it works](../how-it-works.md); every rule there has unit tests in `tests/core/`.

## State (`core/state.py`)

`CoreState` holds what must survive a restart:

- per zone (`ZoneState`): mode, wait start, last valid reading and its time, fault start, manual cap, valve output tracking, the effective target of the last step (to detect a raise), waiting-for-first-reading start;
- global: heat source actual state, last ON / OFF / unavailable times, calling zone, sync fired, heat source output tracking, the last reminder day, the last counted reconcile tick, failsafe active, long run alerted.

Alerts are derived from these fields (`alerts.active_alerts`), not stored separately. Serialisation: [Stored data](storage.md).

## Events

`Event(kind, message, zone_id, data)`: the adapter turns them into notifications (titles in `notifications.py`) or log lines. Kinds: sensor fault started / reminder / recovered, output mismatch / recovered, watchdog failed / recovered / parameters differ (from `core/heartbeat.py`), failsafe started / ended, long run / ended. Events are emitted on the change, so a restart neither repeats nor loses one.

## Output mismatch counting

Per output, on each reconcile tick: unavailable counts; a differing state counts only if the desired state is the same as at the previous tick (the command had a full interval); a just-changed desired state resets the count (normal switching never alerts); available and equal resets it and sends the recovery if the alert was sent. The alert is sent once when the count reaches `output_mismatch_alert`. In shadow mode the counters are reset silently.
