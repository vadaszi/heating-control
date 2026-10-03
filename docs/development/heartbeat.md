# Heartbeat and watchdogs

## Shelly heartbeat client

`heartbeat.py` (HTTP), `core/heartbeat.py` (evaluation). The protocol is specified in [Heartbeat protocol](../heartbeat-protocol.md); the scripts in [Shelly scripts](../shelly-scripts.md).

- The Shellys come from the YAML key `shellys_with_watchdog` (`host`, `script_id`, `switches`, optional `name`, `password`). There is no device registry lookup and no `Script.List`. The Shelly holding the heat source switch must report `role: "heat_source"` and hold no valve; every other one `"valve"`. Every mapped switch is on exactly one listed Shelly or in `relays_without_watchdog`.
- Every `heartbeat_interval` a `POST` goes to every listed Shelly, as tasks with a 10 s timeout; a Shelly whose previous call still runs is skipped. The heat source Shelly's body carries the heating season; a season change is sent at once (or right after a call that is still running).
- On the first call after the start and after a failed call, the status is read with `GET` first, because the `POST` answer already shows the state after the heartbeat. A `timed_out` or `failsafe` state and a restart (`uptime_s` lower than before) are only logged.
- A call **fails** on a connection error or timeout, a status other than 200, a body that is not a status, `v` ≠ 1 or the wrong `role`. After `heartbeat_fail_alert` failures in a row one alert names the cause (unreachable, 404 script not running, 401 authentication, unusable answer); the next good answer is the recovery.
- `params`: `heartbeat_timeout_s` is compared with `heartbeat_timeout`, `check_interval_s` only if `heartbeat_check_interval` is set. A difference is alerted once and clears silently.
- The alert state per Shelly is stored, so a restart neither repeats an alert nor loses a recovery.
- Digest authentication with the user `admin` when a password is set.
- Heartbeats go out also in shadow mode, but only while the reconcile loop is alive.

## The scripts

`shelly_scripts/valve_watchdog.js` and `shelly_scripts/heat_source_watchdog.js`; the role is fixed by the file.

- **Parameters** live only in the CONFIG block at the top of each script; the device's key-value store holds only runtime state (the heat source's season flag, written when it changes). HA never pushes parameters.
- **Timing:** a periodic check (`check_interval_s`, 60 s) compares the time since the last heartbeat with the timeouts, using the device's uptime, so 5 h and 24 h timeouts need no long timers and no valid clock. The script start (boot or restart) counts as the last heartbeat.
- **Timed out:** the valve script switches every managed channel ON, the heat source script OFF, and both re-assert that state at every check if something else switched it. A returning heartbeat ends it at once; the script switches nothing and the reconcile loop sets the outputs.
- **Heat source failsafe operation:** after `failsafe_trigger_s` with the season flag ON, heat in the daily window by the device's local time (`sys.time`); without a valid time, an uptime cycle starting with ON. The script's own switching in the failsafe holds a minimum ON and OFF time.
- **Language subset:** the Shelly engine runs a JavaScript subset (`let`/`var`, named top-level functions declared before use; no arrow functions, `const`, classes, template literals, destructuring or promises). `tests/shelly/subset.test.mjs` parses the scripts with acorn and rejects anything else.
- The scripts refer only to the user manual (`docs/shelly-scripts.md`, `docs/heartbeat-protocol.md`).

## External watchdog ping

`watchdog.py`: if `watchdog_ping_url` is set, a `GET` every `watchdog_ping_interval` (default 300 s) and one at the start, as a task with a 10 s timeout, skipped while the previous one runs; HTTP 2xx is success. Only while the reconcile loop is alive; also in shadow mode. A failure is only logged (warning at the first, info on recovery): the external service alerts when the pings stop. The URL is a secret and never logged.
