# Reconcile loop and outputs

`controller.py` (`FloorHeatingController`), `inputs.py`, `outputs.py`.

## When it runs

- Every `reconcile_interval` (default 60 s). Only these runs are **reconcile ticks** (`Inputs.reconcile_tick`), which the output mismatch counter counts.
- At once on a state change of any mapped sensor or switch, and after every settings change (parameters, season, Control active, schedules, holiday).
- It starts when Home Assistant has started (`async_at_started`), so entities that are still loading neither get commands nor count as mismatches.
- An `asyncio.Lock` serialises the runs.

## One run

1. Read the inputs (`inputs.py`): each sensor's state and `last_reported`, converted from the sensor's own unit to °C (a sensor without a temperature unit gives no reading and logs one warning); each switch's state (anything other than `on`/`off` is unavailable).
2. Call `step` with the **current** actual states. A command is never computed from an older state: a heat source switch that returns from `unavailable` must not get a stale OFF.
3. Apply the outputs: update the entities, store the new state (delayed save), turn the events into notifications, delete ended one-shot schedules, switch holiday off when it ended.
4. Send commands for the outputs that differ (`outputs.py`).

## Commands and backoff

- The first command for a desired state goes out at once. While the switch does not follow, it is repeated after 1, 2, 4 and 8 minutes, then every 15 minutes. The count resets when the switch follows or the desired state changes.
- Nothing is sent or queued while a switch is unavailable; the first command after it returns goes out at once.
- Commands run as tasks with a 30 s timeout, outside the lock. A failing command is logged and never stops the loop.
- Zones without a valve have no output.

## Shadow mode (Control active OFF)

- No commands. The core gets the **commanded** states as feedback, so its decisions stay consistent: when the desired state changes, `step` runs again at once with the same `now` (not a new tick), as if the switches had followed.
- After a restart the commanded heat source starts from the stored last known state, the valves from OFF.
- The mismatch counters are reset silently. Heartbeats and the watchdog ping continue.

**ON → OFF (going to shadow mode):** one final safe set — every switch that does not report OFF gets OFF, retried with the backoff above (persisted across restarts as `pending_off`) until it has reported OFF once. A switch unavailable at that moment gets it when it returns. After that nothing more is sent.

**OFF → ON (going live):** the next run sets every output. From then on the real switch states count; a heat source that shadow mode believed ON but that reads OFF counts as stopped, so the minimum off time applies before the first start.

## Settings

The controller owns the UI values (zone and global parameters, heating season, Control active, schedules and the schedule counter, holiday and its end date/time). Entities and services call its `async_set_*` methods and listen with `async_add_listener`; they never restore their own state. Invalid values raise `ConfigError`, which the entities turn into a service validation error. Every change is stored and triggers a run.

## Liveness

`loop_alive`: the last completed run is at most 3 reconcile intervals old. Heartbeats and the watchdog ping go out only while it holds, so a broken integration (e.g. every run failing after an HA update) lets the Shelly watchdogs act and the external watchdog alert, as if HA had stopped.

## Startup checks

Structural YAML errors fail the setup (`schema.py`, `build_config`, `CoreConfig`). Entities that HA does not know after it has started are reported (error log + persistent notification) and treated as unavailable; the integration keeps running, so a sensor integration that fails to load once does not stop the heating. After the platforms are set up, entities of this config entry that no platform creates any more are removed (only if every platform set up).
