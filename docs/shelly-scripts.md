# Shelly scripts: which script on which device, upload, configure, test

The Shellys that switch your valves and your heat source run a small **watchdog script**. It is the safety net for the case that Home Assistant (HA) or the `floorheat` integration stops working (spec [`design.md`](design.md) §3.6, failsafe case 2):

- HA sends a heartbeat to every script every 5 minutes.
- If the heartbeats stop for 5 hours (`heartbeat_timeout_s`), each script puts its device into a safe state on its own:
  - **valve Shellys:** all valve channels ON (open), so the floor can take heat whenever the heat source runs;
  - **heat source Shelly:** heat request OFF.
- The 5 hours give you time to fix HA before anything happens (D-60).
- When heartbeats come back, the scripts stop acting at once and switch nothing; HA's reconcile loop sets every output again.
- The scripts talk only to HA, never to each other. Protocol details: [`heartbeat-protocol.md`](heartbeat-protocol.md).

> v1 scripts: the heat source script only switches OFF. The 24 h failsafe (heating in a daily window, or by an uptime cycle without a clock) comes with v1.2.

## Which script goes where
| Device | Script | Example |
|---|---|---|
| Every Shelly that switches zone valves | [`shelly_scripts/valve_watchdog.js`](../shelly_scripts/valve_watchdog.js) | Shelly Plus 2PM (Gen2) in switch profile |
| The Shelly that switches the heat source request | [`shelly_scripts/heat_source_watchdog.js`](../shelly_scripts/heat_source_watchdog.js) | Shelly 1 Gen3 / Gen4 |

Any Shelly Gen2 or newer with scripting can run them. A device switching both valves and the heat source is not supported: give the heat source its own Shelly.

## Required device settings
Set these in the device's web UI (names may differ slightly between firmware versions). Use the current firmware.

1. **Power-on default: OFF** for every output the scripts manage, **especially the heat source Shelly.**
   After a power cut the device must start with its output OFF. The integration relies on this: when the heat source switch comes back ON after being unavailable, HA concludes it never stopped; when it comes back OFF, HA concludes it stopped when it went away (D-95). "Restore last state" or "ON" would break that reasoning.
2. **Valve Shellys: switch profile** (not cover/roller), so each channel is a separate switch.
3. **Script "Run on startup" enabled**, so the watchdog runs again after every reboot.
4. **Authentication:** recommended if other people can reach your network. HA then needs the device password (in HA's `secrets.yaml`, never in this repository). The user name is always `admin`.

## Upload
In the device web UI:
1. Open **Scripts** → **Create script** (or **Add script**).
2. Name it, e.g. `floorheat_valve_watchdog` or `floorheat_heat_source_watchdog`.
3. Paste the **whole** content of the script file, unchanged except for the CONFIG block (see below). **Save**.
4. Enable **Run on startup** and press **Start**.
5. Note the script's **id** (the number in the script list or in the URL of the script page, e.g. `1`). The heartbeat endpoint is:
   ```
   http://<shelly-address>/script/<script-id>/heartbeat
   ```
6. The script console should show `floorheat ... watchdog: started, ...`. An error such as `CONFIG.heartbeat_timeout_s must be ...` means the CONFIG block has an invalid value; the script stops until you fix it.

To update a script later, replace its code with the new file, re-apply your CONFIG changes, save and restart it. The heat source's stored season flag survives this (it lives in the device's key-value store).

## Configure
Only edit the CONFIG block at the top of the script. The defaults fit most installations.

**Valve script**
| Key | Default | Meaning |
|---|---|---|
| `heartbeat_timeout_s` | `18000` (5 h) | Seconds without a heartbeat before all valve channels are switched ON. |
| `check_interval_s` | `60` | How often the script checks; it acts within one check after the timeout. |
| `switch_ids` | `null` | `null` = every switch of the device. Use a list such as `[0]` if a channel switches something other than a valve. |
| `endpoint` | `"heartbeat"` | Endpoint name in the URL. |

**Heat source script**
| Key | Default | Meaning |
|---|---|---|
| `heartbeat_timeout_s` | `18000` (5 h) | Seconds without a heartbeat before the output is switched OFF. |
| `check_interval_s` | `60` | As above. |
| `switch_id` | `0` | The switch that requests heat. |
| `endpoint` | `"heartbeat"` | Endpoint name in the URL. |
| `kvs_season_key` | `"floorheat_season"` | Key in the device's key-value store that keeps the heating season flag. |

HA never changes these values. It reads them from every heartbeat answer and, from v1 on, alerts you if they differ from the values it expects (D-73). If you change a timeout on the device, change HA's expected value too.

### Behaviour worth knowing
- **After a reboot or a script restart** the script treats the start as the last heartbeat: outputs stay at the power-on default (OFF) and the timeout counts from the start (D-72, D-102).
- **While timed out** the script keeps its safe state: if something else (the Shelly app, or HA while its heartbeat is broken) switches an output back, the script corrects it at the next check (D-103). HA then shows an "output not following command" alert.
- **Season flag:** every heartbeat to the heat source carries HA's heating season switch. The script stores it (only when it changes) and reports it; `null` means it was never set, which counts as OFF (D-105). It is used by the v1.2 failsafe.

## Check that it works
With `curl` from a computer on the same network (add `--digest -u admin:<password>` to every command if authentication is on):

```bash
# status only (does not count as a heartbeat)
curl -s http://<shelly-address>/script/<script-id>/heartbeat

# a heartbeat to a valve script
curl -s -X POST -H 'Content-Type: application/json' -d '{"v": 1}' \
  http://<shelly-address>/script/<script-id>/heartbeat

# a heartbeat to the heat source script (season ON)
curl -s -X POST -H 'Content-Type: application/json' -d '{"v": 1, "season": true}' \
  http://<shelly-address>/script/<script-id>/heartbeat
```
The answer is a JSON status with `"running": true`, the watchdog `state`, `heartbeat_age_s`, the switch outputs and `params`. The fields are described in [`heartbeat-protocol.md`](heartbeat-protocol.md).

## Bench tests (shortened timeouts)
Run these before installing, on the bench or with the loads disconnected. HA's heartbeat client comes in a later phase, so you send the heartbeats yourself. **Do not** run them on a system that is heating.

**Preparation:** in the CONFIG block set
```
  heartbeat_timeout_s: 120,
  check_interval_s: 5,
```
save and restart the script. A heartbeat loop for a second terminal (stop it with Ctrl+C):
```bash
URL=http://<shelly-address>/script/<script-id>/heartbeat
BODY='{"v": 1}'                       # heat source: '{"v": 1, "season": true}'
while true; do curl -s -o /dev/null -w '%{http_code}\n' -X POST \
  -H 'Content-Type: application/json' -d "$BODY" "$URL"; sleep 30; done
```

**S6 — status is answered**
1. `curl -s $URL` → `"running": true`, `"state": "normal"`, `"heartbeat_seen": false` (right after the start), `params` show `120` and `5`.
2. Send one heartbeat (`POST`) → `"heartbeat_seen": true`, `"heartbeat_age_s": 0`. Heat source: `"season": true`.
3. `curl -s -X POST -d 'x' $URL` → `400`; heat source also `-d '{"v": 1}'` → `400` (no season).

**S1 — valve Shelly, heartbeat stops**
1. Run the heartbeat loop for a minute; switch all channels OFF in the app.
2. Stop the loop. Within about 2 minutes **all channels switch ON**; `curl -s $URL` shows `"state": "timed_out"`.
3. Switch one channel OFF in the app → it is back ON within about 5 seconds.

**S4 — valve Shelly, heartbeat returns**
1. From the end of S1, start the heartbeat loop again. The next answer shows `"state": "normal"`; **no channel changes**.
2. Switch a channel OFF in the app → it **stays OFF** (the script no longer acts) while the loop runs.

**Heat source Shelly: timeout and return** (S2 first half, S4)
1. Run the heartbeat loop; switch the output ON in the app.
2. Stop the loop. Within about 2 minutes the output **switches OFF**; `"state": "timed_out"`. Switching it ON in the app → OFF again within about 5 seconds.
3. Start the loop again → `"state": "normal"`; the output stays OFF. Switch it ON in the app → it **stays ON**.

**Reboot** (both devices)
1. Send a heartbeat (heat source: season `true`), switch the outputs ON, then reboot the device (web UI, or power off and on).
2. After the reboot all outputs are **OFF** (this checks the power-on default), `curl -s $URL` answers (script started on its own), `"heartbeat_seen": false`, small `uptime_s`; heat source: `"season": true` (kept in storage).
3. Valve Shelly: without heartbeats, all channels switch ON about 2 minutes after the boot.

**V2 — heartbeat endpoint on each device type**
For the Shelly Plus 2PM (Gen2) and the Shelly 1 (Gen3 or Gen4), note the firmware version and check:
1. `GET` and `POST` work as in S6.
2. With authentication enabled: without credentials the answer is `401`; with `--digest -u admin:<password>` it is `200`.
3. The script keeps answering after a reboot (Run on startup).

Report the results (device type, firmware version, pass/fail; no addresses or passwords) so they can be recorded in the spec (§8, V2).

**Afterwards:** paste the unmodified script again (or set `heartbeat_timeout_s: 18000` and `check_interval_s: 60`), save and restart it, and check with `curl -s $URL` that `params` show the defaults.

## Troubleshooting
| Symptom | Cause / fix |
|---|---|
| `404` from the endpoint | The script is not running (stopped, error, or "Run on startup" off after a reboot), or the script id in the URL is wrong. |
| `401` | Authentication is on: use `--digest -u admin:<password>`. |
| `400` | The request body is not a JSON object, or a heat source heartbeat has no `season` true/false. It does not count as a heartbeat. |
| Script stops right after the start | Invalid CONFIG value: the script console shows which one. |
| Valve channels do not switch | The device is in cover/roller profile, or `switch_ids` lists channels that do not exist (`"output": null` in the status). |
