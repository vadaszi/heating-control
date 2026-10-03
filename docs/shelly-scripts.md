# Shelly scripts: which script on which device, upload, configure, test

The Shellys that switch your valves and your heat source run a small **watchdog script**. It is the safety net for the case that Home Assistant (HA) or the Multizone Floor Heating Manager integration stops working ([How it works → Failsafe](how-it-works.md#failsafe)):

- HA sends a heartbeat to every script every 5 minutes.
- If the heartbeats stop for 5 hours (`heartbeat_timeout_s`), each script puts its device into a safe state on its own:
  - **valve Shellys:** all valve channels ON (open), so the floor can take heat whenever the heat source runs;
  - **heat source Shelly:** heat request OFF.
- The 5 hours give you time to fix HA before anything happens.
- If the heartbeats stay away for 24 hours (`failsafe_trigger_s`) and the last heartbeat said "heating season ON", the heat source Shelly starts the **failsafe operation**: it requests heat every day from 10:00 to 15:00 by its own clock, so the house does not cool down while HA is broken. Without a valid clock it uses an uptime cycle instead (5 h ON, 19 h OFF). See [Failsafe operation](#failsafe-operation-heat-source).
- When heartbeats come back, the scripts stop acting at once and switch nothing; HA's reconcile loop sets every output again.
- The scripts talk only to HA, never to each other. Protocol details: [`heartbeat-protocol.md`](heartbeat-protocol.md).

## Which script goes where
| Device | Script | Example |
|---|---|---|
| Every Shelly that switches zone valves | [`shelly_scripts/valve_watchdog.js`](../shelly_scripts/valve_watchdog.js) | Shelly Plus 2PM (Gen2) in switch profile |
| The Shelly that switches the heat source request | [`shelly_scripts/heat_source_watchdog.js`](../shelly_scripts/heat_source_watchdog.js) | Shelly 1 Gen3 / Gen4 |

Any Shelly Gen2 or newer with scripting can run them. A device switching both valves and the heat source is not supported: give the heat source its own Shelly.

## Required device settings
Set these in the device's web UI (names may differ slightly between firmware versions). Use the current firmware.

1. **Power-on default: OFF** for every output the scripts manage, **especially the heat source Shelly.**
   After a power cut the device must start with its output OFF. The integration relies on this: when the heat source switch comes back ON after being unavailable, HA concludes it never stopped; when it comes back OFF, HA concludes it stopped when it went away. "Restore last state" or "ON" would break that reasoning.
2. **Valve Shellys: switch profile** (not cover/roller), so each channel is a separate switch.
3. **Script "Run on startup" enabled**, so the watchdog runs again after every reboot.
4. **Heat source Shelly: time zone and time server (SNTP).** The failsafe operation runs by the device's local time. Set the device's time zone (location) correctly and keep the default time server, so the clock is set from the internet after every start. Without a valid time the script falls back to the uptime cycle.
5. **Authentication:** recommended if other people can reach your network. HA then needs the device password (in HA's `secrets.yaml`, never in this repository). The user name is always `admin`.

## Upload
In the device web UI:
1. Open **Scripts** → **Create script** (or **Add script**).
2. Name it, e.g. `floor_heating_valve_watchdog` or `floor_heating_heat_source_watchdog`.
3. Paste the **whole** content of the script file, unchanged except for the CONFIG block (see below). **Save**.
4. Enable **Run on startup** and press **Start**.
5. Note the script's **id** (the number in the script list or in the URL of the script page, e.g. `1`). The integration needs it with the device address in its YAML (`shellys_with_watchdog`, [configuration](configuration.md#shelly-watchdogs)). The heartbeat endpoint is:
   ```
   http://<shelly-address>/script/<script-id>/heartbeat
   ```
6. The script console should show `Floor heating ... watchdog: started, ...`. An error such as `CONFIG.heartbeat_timeout_s must be ...` means the CONFIG block has an invalid value; the script stops until you fix it.

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
| `kvs_season_key` | `"multizone_floor_heating_manager_season"` | Key in the device's key-value store that keeps the heating season flag. |
| `failsafe_trigger_s` | `86400` (24 h) | Seconds without a heartbeat before the failsafe operation starts. Longer than `heartbeat_timeout_s`. |
| `failsafe_start` | `"10:00"` | Failsafe operation start, the device's local time (`"HH:MM"`). |
| `failsafe_stop` | `"15:00"` | Failsafe operation stop. The window may cross midnight (`"22:00"` to `"03:00"`); start and stop must differ. |
| `uptime_on_s` | `18000` (5 h) | Without a valid clock: ON time of the uptime cycle. |
| `uptime_off_s` | `68400` (19 h) | Without a valid clock: OFF time of the uptime cycle. |
| `min_on_s` | `3600` (1 h) | Failsafe operation only: once the script switches the heat source ON, it stays ON at least this long. `0` = no minimum. |
| `min_off_s` | `3600` (1 h) | Failsafe operation only: once the script switches it OFF, it stays OFF at least this long. `0` = no minimum. |

HA never changes these values. It reads them from every heartbeat answer and alerts you if they differ from the values it expects: `heartbeat_timeout_s` against the integration's `heartbeat_timeout` (default 18000), and `check_interval_s` only if you set `heartbeat_check_interval`. If you change a timeout on the device, change HA's expected value too.

The failsafe values are **not** compared. They are separate from the integration's own "Failsafe operation delay / start / stop" settings: those apply only while HA runs and every temperature sensor is dead, the script's values only while HA is down. If you want both to heat at the same time of day, set the same values in both places.

### Behaviour worth knowing
- **After a reboot or a script restart** the script treats the start as the last heartbeat: outputs stay at the power-on default (OFF) and the timeout counts from the start.
- **While timed out** the script keeps its safe state: if something else (the Shelly app, or HA while its heartbeat is broken) switches an output back, the script corrects it at the next check. HA then shows an "output not following command" alert.
- **Season flag:** every heartbeat to the heat source carries HA's heating season switch. The script stores it (only when it changes) and reports it; `null` means it was never set, which counts as OFF. The failsafe operation runs only with the flag ON.

### Failsafe operation (heat source)
- **When:** no heartbeat for `failsafe_trigger_s` (24 h) **and** the last heartbeat said heating season ON. With the season OFF (or never set) the output simply stays OFF; the console says "no failsafe operation" once. The status shows `"state": "failsafe"` while it runs.
- **With a valid clock** (the status shows `"time": "HH:MM"`): the heat source is ON from `failsafe_start` to `failsafe_stop` every day, OFF the rest of the day. Reached inside the window, it heats for the rest of it.
- **Without a valid clock** (`"time": null`, e.g. after a power cut while the internet is down; Shellys have no backup clock): the uptime cycle, 5 h ON then 19 h OFF, starting with ON when the failsafe operation starts. As soon as the clock becomes valid, the window takes over.
- **Minimum ON and OFF time:** every switch the script makes holds for `min_on_s` / `min_off_s` (1 h each), so it never short-cycles the heat source. Example: the failsafe operation starts at 14:45 → ON until 15:45, not 15:00. These minimums apply only to the script's own switching in the failsafe operation; while HA sends heartbeats the script never touches the relay.
- **It keeps its output:** like a timed-out script, it switches the relay back at the next check if someone else switches it. **To switch the heat source by hand while HA is down, stop the script** in the device web UI (Scripts → Stop); start it again afterwards.
- **When HA comes back**, the first heartbeat ends the failsafe operation at once; the script switches nothing and HA takes over. A rare case: if HA comes back during the first hour of a failsafe run, no zone needs heat, and the last thing HA saw before it went away was the heat source ON, HA concludes the heat source never stopped and may switch it OFF after a short run. Accepted.
- **Power cut during a long HA outage:** after a reboot the start counts as the last heartbeat, so the output stays OFF for another `failsafe_trigger_s` (24 h) before the failsafe operation starts again. This keeps a Shelly that restarts together with HA (one power cut for the whole house) from switching the heat pump ON just before HA takes over again. Accepted.

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
Run these before installing, on the bench or with the loads disconnected. You send the heartbeats yourself. **Do not** run them on a system that is heating.

If the integration already sends heartbeats to the device, they keep the watchdog quiet and spoil the test. For the bench tests, remove the device from `shellys_with_watchdog` in the integration's YAML, list its switches in `relays_without_watchdog` instead and restart HA; undo it afterwards. With shortened timeouts the integration would also report "script parameters differ".

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

**Heat source Shelly: failsafe operation (S2 second half, S5, S3)**

Shorten the failsafe values too (CONFIG block of the heat source script), save and restart the script:
```
  heartbeat_timeout_s: 120,
  check_interval_s: 5,
  failsafe_trigger_s: 240,
  failsafe_start: "HH:MM",      // about 8 minutes from now (device time)
  failsafe_stop: "HH:MM",       // 5 minutes after the start
  uptime_on_s: 120,
  uptime_off_s: 180,
  min_on_s: 60,
  min_off_s: 60,
```
Check with `curl -s $URL` that `"time"` shows the device's local time and `params` show your values.

*S2: failsafe operation by the clock*
1. Run the heartbeat loop with `'{"v": 1, "season": true}'` for a minute; switch the output ON in the app.
2. Stop the loop. After about 2 minutes: output **OFF**, `"state": "timed_out"`.
3. After about 4 minutes: `"state": "failsafe"`; the output stays **OFF** until `failsafe_start`.
4. At `failsafe_start` the output switches **ON** (console: "failsafe operation: heat source ON (HH:MM)"). Switch it OFF in the app → ON again within about 5 seconds.
5. At `failsafe_stop` it switches **OFF**.
6. Start the loop again → `"state": "normal"`; nothing switches.

*S5: season OFF never heats*
1. Run the loop with `'{"v": 1, "season": false}'` for a minute, then stop it.
2. Wait past `failsafe_start`: `"state"` stays `"timed_out"`, the output stays **OFF**; the console says "no failsafe operation: the last heartbeat did not say heating season ON".
3. Send one heartbeat with `"season": true` again before the next test.

*S3: reboot, no valid time (uptime cycle)*
1. Make the device start without a valid time: in its settings, set the time server (SNTP) to an address that does not answer, or disconnect your router from the internet. Send one heartbeat with `"season": true`, then **cut the power** for a few seconds.
2. After the power returns: `curl -s $URL` shows `"time": null`, `"season": true`, `"state": "normal"`, output **OFF**. If `"time"` shows a time, the device got its clock anyway: note it and skip this test.
3. About 2 minutes after the boot: `"timed_out"`, output OFF. About 4 minutes after the boot: `"failsafe"`, output **ON** (console: "uptime cycle, no valid time").
4. 2 minutes later **OFF**, 3 minutes after that **ON** again.
5. Restore the time server (or the internet). When `"time"` shows a time again, the window takes over: outside the window the output goes OFF (after `min_on_s` at the latest).

**Reboot** (both devices)
1. Send a heartbeat (heat source: season `true`), switch the outputs ON, then **cut the power** for a few seconds (unplug it). A software reboot from the web UI is not enough: it keeps the outputs as they were, and only a real power loss applies the power-on default.
2. After the power returns all outputs are **OFF** (this checks the power-on default), `curl -s $URL` answers (script started on its own), `"heartbeat_seen": false`, small `uptime_s`; heat source: `"season": true` (kept in storage).
3. Valve Shelly: without heartbeats, all channels switch ON about 2 minutes after the boot.

**Endpoint check on your device type**
For the Shelly Plus 2PM (Gen2) and the Shelly 1 (Gen3 or Gen4), note the firmware version and check:
1. `GET` and `POST` work as in S6.
2. With authentication enabled: without credentials the answer is `401`; with `--digest -u admin:<password>` it is `200`.
3. The script keeps answering after a reboot (Run on startup).

If a step fails on your device type or firmware, please [open an issue](https://github.com/vadaszi/multizone-floor-heating-manager/issues) with the device type, the firmware version and what you saw (no addresses or passwords).

**Afterwards:** paste the unmodified script again (or set every value you changed back to the default in the tables above), save and restart it, and check with `curl -s $URL` that `params` show the defaults. Heat source: restore the time server if you changed it, and send a heartbeat with `"season": true` (or let HA send one) so the stored season flag is right.

## Troubleshooting
| Symptom | Cause / fix |
|---|---|
| `404` from the endpoint | The script is not running (stopped, error, or "Run on startup" off after a reboot), or the script id in the URL is wrong. |
| `401` | Authentication is on: use `--digest -u admin:<password>`. |
| `400` | The request body is not a JSON object, or a heat source heartbeat has no `season` true/false. It does not count as a heartbeat. |
| Script stops right after the start | Invalid CONFIG value: the script console shows which one. |
| Heat source failsafe operation never starts | The stored season flag is `false` or `null` (`"season"` in the status): it heats only if the last heartbeat said heating season ON. |
| Failsafe heats at the wrong time of day | The device's time zone is wrong: check `"time"` in the status against your clock. |
| Valve channels do not switch | The device is in cover/roller profile, or `switch_ids` lists channels that do not exist (`"output": null` in the status). |
