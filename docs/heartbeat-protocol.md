# Heartbeat protocol (v1)

How the `floorheat` integration talks to the Shelly watchdog scripts (`shelly_scripts/`).
Spec: [`design.md`](design.md) §3.6 (failsafe case 2), §5.4, decisions D-60, D-61, D-72, D-73, D-100 to D-105.
Setup and bench tests: [`shelly-scripts.md`](shelly-scripts.md).

## Purpose
- Home Assistant (HA) sends a **heartbeat** to every Shelly that runs a watchdog script, every `HeartbeatInterval` (5 min), also in shadow mode (D-56).
- A script that gets no heartbeat for `heartbeat_timeout_s` (default 5 h, D-60) puts its outputs into the safe state:
  - valve script: all valve channels ON (open);
  - heat source script: output OFF.
- Every call is answered with the script's **status**. One call therefore checks that the device is reachable **and** that the script runs (D-61), and reports the script's parameters so HA can compare them with what it expects (D-73).

## Endpoint
```
http://<shelly-address>/script/<script-id>/heartbeat
```
- `<script-id>` is the script's slot number on the device (shown in the device web UI). HA takes the address and the script id from its YAML (`shellys`, [configuration](configuration.md#shelly-watchdogs), D-120); it does not look them up.
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
  - heat source: `season` missing or not a boolean (D-105).

  HA counts a `400` as a failed call, so a broken request leads to the D-61 alert instead of silently keeping the watchdog quiet.

## Response
`200`, `Content-Type: application/json`. Same shape for `POST` and `GET`:

```json
{
  "v": 1,
  "role": "heat_source",
  "script_version": "1.0.0",
  "running": true,
  "state": "normal",
  "heartbeat_seen": true,
  "heartbeat_age_s": 0,
  "uptime_s": 86400,
  "season": true,
  "switches": [{"id": 0, "output": true}],
  "params": {"heartbeat_timeout_s": 18000, "check_interval_s": 60, "switch_id": 0}
}
```

| Field | Type | Meaning |
|---|---|---|
| `v` | integer | Protocol version of the script (1). |
| `role` | string | `"valve"` or `"heat_source"`; fixed by the script file (D-100). |
| `script_version` | string | Version of the script file. |
| `running` | boolean | Always `true`: the script answered. |
| `state` | string | Watchdog state, see below. |
| `heartbeat_seen` | boolean | A heartbeat has arrived since the script started. |
| `heartbeat_age_s` | integer | Seconds since the last heartbeat, or since the script started if none has arrived (D-102). `0` in a `POST` response. |
| `uptime_s` | integer | Device uptime in seconds (a small value means the device rebooted). |
| `season` | boolean or null | Heat source only: the stored heating season flag; `null` = never set (treated as OFF, D-105). The `POST` response already carries the new value. |
| `switches` | array | The switches the script manages, `{"id": <switch id>, "output": true/false/null}`; `null` = no such switch on the device. |
| `params` | object | The script's configured parameters (D-73), see below. |

### Watchdog states
| State | Script | Meaning | Outputs |
|---|---|---|---|
| `normal` | both | Heartbeats arrive (or the timeout has not passed since start). | Not touched; HA controls them. |
| `timed_out` | both | No heartbeat for `heartbeat_timeout_s`. | Valve: all channels ON. Heat source: OFF. Re-asserted on every check if something else switches them (D-103). |
| `failsafe` | heat source, **from v1.2 (P12)** | No heartbeat for FailsafeTrigger (24 h), season `true`. | Heat during the daily window (device clock) or by the uptime cycle (§3.6, D-72). |

The state changes to `timed_out` at the first check (every `check_interval_s`) at or after the timeout. A valid heartbeat puts it back to `normal` at once; the script then switches nothing, and HA's reconcile loop sets the outputs (S4).

### `params`
| Key | Script | Default | Meaning |
|---|---|---|---|
| `heartbeat_timeout_s` | both | 18000 (5 h) | HeartbeatTimeout (§4, D-60). |
| `check_interval_s` | both | 60 | How often the script checks the timeout. |
| `switch_ids` | valve | `null` | `null` = every switch component of the device (D-104), or a list of ids. |
| `switch_id` | heat source | 0 | The switch that requests heat. |

The values come only from the script's CONFIG block (D-101); HA never sends parameters. v1.2 adds the heat source failsafe keys (FailsafeTrigger, window, uptime cycle).

## What HA does with it (D-120 to D-122)
- Every `HeartbeatInterval` (default 5 min) HA sends a `POST` to every listed Shelly, also in shadow mode. The heat source Shelly also gets one at once when the heating season switch changes.
- On the first call after HA starts, and after a failed call, HA first reads the status with `GET`. The `POST` answer can no longer show a timeout, because the script applies the heartbeat before it answers. A `timed_out` state and its `heartbeat_age_s` are only logged, as is a restart (`uptime_s` lower than at the previous answer).
- A call **fails** if there is no connection or no answer within 10 s, the HTTP status is not `200` (`404`: script not running or wrong id; `401`: authentication), the body is not a status object, `v` is not 1, or `role` is not the one HA expects from its YAML. After `heartbeat_fail_alert` (default 3) failures in a row HA notifies once, and again when the Shelly answers.
- `params`: HA compares `heartbeat_timeout_s` (and `check_interval_s` if an expected value is configured) and notifies once while they differ.
- HA sends heartbeats only while its reconcile loop works (a completed run within the last 3 reconcile intervals), so a broken integration lets the watchdogs act.

## Versioning
- Adding a field to the request, the response, `params` or a new `state` value is **not** a breaking change: `v` stays 1. Both sides ignore fields they do not know.
- HA compares only the `params` it has expected values for.
- A change that breaks existing clients (renaming or removing a field, changing a meaning) raises `v`. HA alerts when a script reports a `v` it does not support.

## Authentication and security
- If authentication is enabled on the Shelly, the device protects script endpoints like every other endpoint (HTTP digest auth; the user name is always `admin`).
- HA takes the password from its `secrets.yaml` (see [`examples/secrets.example.yaml`](../examples/secrets.example.yaml)). **Never put an address, user name or password into the repository** (§0.1).
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
