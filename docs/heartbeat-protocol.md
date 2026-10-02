# Heartbeat protocol (v1)

How the Multizone Floor Heating Manager integration talks to the Shelly watchdog scripts (`shelly_scripts/`).
What the scripts do for you: [How it works → Failsafe](how-it-works.md#failsafe).
Setup and bench tests: [`shelly-scripts.md`](shelly-scripts.md).

## Purpose
- Home Assistant (HA) sends a **heartbeat** to every Shelly that runs a watchdog script, every `HeartbeatInterval` (5 min), also in shadow mode.
- A script that gets no heartbeat for `heartbeat_timeout_s` (default 5 h) puts its outputs into the safe state:
  - valve script: all valve channels ON (open);
  - heat source script: output OFF.
- After `failsafe_trigger_s` (default 24 h) without a heartbeat, the heat source script runs the failsafe operation if the last heartbeat said heating season ON.
- Every call is answered with the script's **status**. One call therefore checks that the device is reachable **and** that the script runs, and reports the script's parameters so HA can compare them with what it expects.

## Endpoint
```
http://<shelly-address>/script/<script-id>/heartbeat
```
- `<script-id>` is the script's slot number on the device (shown in the device web UI). HA takes the address and the script id from its YAML (`shellys`, [configuration](configuration.md#shelly-watchdogs)); it does not look them up.
- `heartbeat` is the default endpoint name (`CONFIG.endpoint`).
- One endpoint, two methods:

| Method | Meaning | Resets the watchdog |
|---|---|---|
| `POST` | Heartbeat. Answered with the status after the heartbeat was applied. | yes |
| `GET` | Status only (bench tests, troubleshooting). | no |

Other methods get `405` with `Allow: GET, POST`.

## Request (POST)
`Content-Type: application/json`. The device rejects requests larger than 3072 bytes (request line, headers and body together).

| Script | Body | Notes |
|---|---|---|
| valve | `{"v": 1}` | The body may also be empty. |
| heat source | `{"v": 1, "season": true}` | `season` is required and must be `true` or `false`: the heating season switch in HA. |

- `v` is the protocol version HA speaks. The scripts ignore it and any other unknown field, so HA can add fields without breaking older scripts.
- No sequence number and no HA time: the scripts measure time only with their own uptime, and nothing would use them. They can be added later as optional fields (no version change).
- **Invalid request → `400`** with `{"error": "..."}`, and it does **not** count as a heartbeat:
  - body that is not a JSON object;
  - heat source: `season` missing or not a boolean.

  HA counts a `400` as a failed call, so a broken request leads to the "Shelly watchdog not answering" alert instead of silently keeping the watchdog quiet.

## Response
`200`, `Content-Type: application/json`. Same shape for `POST` and `GET`:

```json
{
  "v": 1,
  "role": "heat_source",
  "script_version": "1.1.0",
  "running": true,
  "state": "normal",
  "heartbeat_seen": true,
  "heartbeat_age_s": 0,
  "uptime_s": 86400,
  "season": true,
  "time": "09:41",
  "switches": [{"id": 0, "output": true}],
  "params": {
    "heartbeat_timeout_s": 18000, "check_interval_s": 60, "switch_id": 0,
    "failsafe_trigger_s": 86400, "failsafe_start": "10:00", "failsafe_stop": "15:00",
    "uptime_on_s": 18000, "uptime_off_s": 68400, "min_on_s": 3600, "min_off_s": 3600
  }
}
```

| Field | Type | Meaning |
|---|---|---|
| `v` | integer | Protocol version of the script (1). |
| `role` | string | `"valve"` or `"heat_source"`; fixed by the script file. |
| `script_version` | string | Version of the script file. |
| `running` | boolean | Always `true`: the script answered. |
| `state` | string | Watchdog state, see below. |
| `heartbeat_seen` | boolean | A heartbeat has arrived since the script started. |
| `heartbeat_age_s` | integer | Seconds since the last heartbeat, or since the script started if none has arrived. `0` in a `POST` response. |
| `uptime_s` | integer | Device uptime in seconds (a small value means the device rebooted). |
| `season` | boolean or null | Heat source only: the stored heating season flag; `null` = never set (treated as OFF). The `POST` response already carries the new value. |
| `time` | string or null | Heat source only (script 1.1.0 and later): the device's local time `"HH:MM"` used for the failsafe operation; `null` = no valid time (uptime cycle). |
| `switches` | array | The switches the script manages, `{"id": <switch id>, "output": true/false/null}`; `null` = no such switch on the device. |
| `params` | object | The script's configured parameters, see below. |

### Watchdog states
| State | Script | Meaning | Outputs |
|---|---|---|---|
| `normal` | both | Heartbeats arrive (or the timeout has not passed since start). | Not touched; HA controls them. |
| `timed_out` | both | No heartbeat for `heartbeat_timeout_s`. | Valve: all channels ON. Heat source: OFF. Re-asserted on every check if something else switches them. |
| `failsafe` | heat source | No heartbeat for `failsafe_trigger_s` (24 h) and season `true`. With the season `false` or `null` the state stays `timed_out`. | The failsafe operation: ON from `failsafe_start` to `failsafe_stop` by the device clock, or without a valid time by the uptime cycle (`uptime_on_s` ON, `uptime_off_s` OFF, starting with ON); every switch holds for `min_on_s` / `min_off_s`; re-asserted at every check. |

The state changes to `timed_out` (and later `failsafe`) at the first check (every `check_interval_s`) at or after the timeout. A valid heartbeat puts it back to `normal` at once; the script then switches nothing, and HA's reconcile loop sets the outputs (S4).

### `params`
| Key | Script | Default | Meaning |
|---|---|---|---|
| `heartbeat_timeout_s` | both | 18000 (5 h) | HeartbeatTimeout. |
| `check_interval_s` | both | 60 | How often the script checks the timeout. |
| `switch_ids` | valve | `null` | `null` = every switch component of the device, or a list of ids. |
| `switch_id` | heat source | 0 | The switch that requests heat. |
| `failsafe_trigger_s` | heat source | 86400 (24 h) | FailsafeTrigger of the script. |
| `failsafe_start`, `failsafe_stop` | heat source | `"10:00"`, `"15:00"` | The failsafe operation window, device local time; may cross midnight. |
| `uptime_on_s`, `uptime_off_s` | heat source | 18000, 68400 | The uptime cycle without a valid time (5 h ON, 19 h OFF). |
| `min_on_s`, `min_off_s` | heat source | 3600, 3600 | Shortest ON / OFF the script switches in the failsafe operation. |

The values come only from the script's CONFIG block; HA never sends parameters. The heat source failsafe keys (script 1.1.0) are reported but not compared by HA.

## What HA does with it
- Every `HeartbeatInterval` (default 5 min) HA sends a `POST` to every listed Shelly, also in shadow mode. The heat source Shelly also gets one at once when the heating season switch changes.
- On the first call after HA starts, and after a failed call, HA first reads the status with `GET`. The `POST` answer can no longer show a timeout, because the script applies the heartbeat before it answers. A `timed_out` or `failsafe` state and its `heartbeat_age_s` are only logged, as is a restart (`uptime_s` lower than at the previous answer).
- A call **fails** if there is no connection or no answer within 10 s, the HTTP status is not `200` (`404`: script not running or wrong id; `401`: authentication), the body is not a status object, `v` is not 1, or `role` is not the one HA expects from its YAML. After `heartbeat_fail_alert` (default 3) failures in a row HA notifies once, and again when the Shelly answers.
- `params`: HA compares `heartbeat_timeout_s` (and `check_interval_s` if an expected value is configured) and notifies once while they differ.
- HA sends heartbeats only while its reconcile loop works (a completed run within the last 3 reconcile intervals), so a broken integration lets the watchdogs act.

## Versioning
- Adding a field to the request, the response, `params` or a new `state` value is **not** a breaking change: `v` stays 1. Both sides ignore fields they do not know.
- HA compares only the `params` it has expected values for.
- A change that breaks existing clients (renaming or removing a field, changing a meaning) raises `v`. HA alerts when a script reports a `v` it does not support.

## Authentication and security
- If authentication is enabled on the Shelly, the device protects script endpoints like every other endpoint (HTTP digest auth; the user name is always `admin`).
- HA takes the password from its `secrets.yaml` (see [`examples/secrets.example.yaml`](../examples/secrets.example.yaml)). **Never put an address or password into files you share** (forum posts, a configuration backup on GitHub).
- The endpoint is plain HTTP on the local network. Do not expose the Shellys to the internet.
- A `POST` can keep the watchdog quiet and change the stored season flag. Anyone who can reach the device can do this (and can also switch the outputs directly), so enable device authentication if the network is not trusted.

## Example (bench)
```bash
# status only
curl -s http://<shelly-address>/script/<script-id>/heartbeat
# heartbeat to a heat source script (add --digest -u admin:<password> if auth is on)
curl -s -X POST -H 'Content-Type: application/json' \
  -d '{"v": 1, "season": true}' http://<shelly-address>/script/<script-id>/heartbeat
```
