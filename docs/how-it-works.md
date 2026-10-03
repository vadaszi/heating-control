# How it works

This page explains every decision the integration makes, in plain words. The [example dashboard](dashboard.md) shows these decisions live: each zone's state and reason, and what the heat source does and why.

The integration is built for underfloor heating fed by a heat pump: a slow system that heats a lot of concrete. Its two goals are **comfort in every zone** and **long heat source runs**: a heat pump that switches on and off every few minutes wears out and works inefficiently.

## Terms

| Term | Meaning |
|---|---|
| Zone | One or more floor circuits with one temperature sensor and (usually) one valve actuator. |
| Room temperature | The sensor reading plus the zone's `sensor_offset`. |
| Base target | The zone's normal target temperature: the climate entity's target. |
| Target | The target in force right now: the base target, or what holiday or an auto schedule sets (shown as "Effective target temperature"). |
| Hysteresis | How far the room temperature may move around the target, per zone (default 0.2 °C). |
| Start temperature | Target − hysteresis: at or below it the zone wants heat. |
| Stop temperature | Target + hysteresis: at or above it the zone stops heating. |
| Wait time | Per zone (default 30 min): an open-window filter before a cold zone may start the heat source. |
| Heat source request | The switch that asks the heat pump or boiler for heat. "The heat source runs" on this page means this switch reports ON. |
| Run | The time from heat source request ON to OFF. |
| Calling zone | The zone whose demand started the current run (at most one per run). |

Example: target 22.0 °C, hysteresis 0.2 °C → start temperature 21.8 °C, stop temperature 22.2 °C.

## Zone states

| State | Meaning | Valve |
|---|---|---|
| Idle | No demand. | closed |
| Waiting | Too cold while the heat source is off; the wait time runs. | closed |
| Heating | The zone wants heat. | open |
| Forced | A manual schedule runs. | open (closed while too warm, see [manual schedules](#targets-schedules-and-holiday)) |
| Sensor fault | No valid reading for too long. | open whenever the heat source runs; no demand of its own |

A zone without a valve goes through the same states; it just has no valve to switch. Its climate entity shows *Heating* whenever the heat source runs, because warm water then flows through it.

## The zone rules

1. **Too cold while the heat source is off: wait.** At or below its start temperature the zone goes to *Waiting* and its wait time starts. This filters out an open window: a few minutes of fresh air must not start the heat pump.
2. **One check at the end of the wait.** When the wait time is over, only the temperature at that moment counts. Still at or below the start temperature: the zone goes to *Heating*, its valve opens and it asks for heat. It becomes the calling zone. Warmer again: back to *Idle*. Readings during the wait don't matter. If you lower the target during the wait so that the zone is no longer too cold, the wait ends at once.
3. **Join a running heat source at once.** While the heat source runs, a zone at or below its start temperature goes to *Heating* immediately, without the wait time (also a zone that is waiting). The wait time only prevents *starting* the heat source.
4. **A raised target starts at once.** When a schedule, the end of holiday or you raise a zone's target so that it is at or below its new start temperature, the zone goes to *Heating* without the wait time. The heat source's minimum off time still applies.
5. **Stop.** A heating zone goes back to *Idle* at or above its stop temperature, also after you lowered its target.
6. **Calling zone and sync rule** (below).

### Calling zone and sync rule

Many installations can't give full flow to every zone at once (in the house this was built for, the main pipe is too thin). So the zone that started the run gets the heat first, and the others are topped up at the end of the run:

- The zone that starts the run is the **calling zone**. If several zones would start it at the same moment (e.g. their wait times end together), the one furthest below its start temperature wins; on a tie, the one listed first in the YAML.
- When the calling zone reaches its target (not its stop temperature), every other zone that is below its stop temperature joins and heats until its own stop temperature. This **sync rule** fires once per run.
- Result: all zones end the run near their stop temperatures, so none of them asks for heat again shortly after.
- If the calling zone's sensor fails during the run, it counts as having reached its target: the sync rule fires.
- A run started by a manual schedule has no calling zone at first: the first zone that joins because it is cold becomes the calling zone. If none joins, there is no sync rule in that run.

## Heat source protection

- **Minimum on time and minimum off time** (default 60 min each, never below 30 min): the heat source request stays ON at least the minimum on time and OFF at least the minimum off time. Both count from the moments the switch really changed.
- **Everything warm before the minimum on time is over:** the request stays ON until it is, and every valve opens to spread the heat over the house (heat source status "Running for minimum on time", zone reason "Spreading heat"). Zones at or above the manual max temperature (default 25 °C) stay closed.
- **A zone needs heat during the minimum off time:** the zone goes to *Heating* and opens its valve at once, but the request waits until the minimum off time is over (reason "Waiting for heat source minimum off time"). If the zone gets warm enough meanwhile, it goes back to *Idle* and nothing starts.
- The wait time and the minimum off time run side by side: the request goes ON when both are over and the zone is still too cold.
- **First start:** with nothing stored yet, no minimum off time applies. If the heat source is already running at the first start, its minimum on time counts from then.

### Heat source switch unavailable

A Wi-Fi glitch or a router restart can make the heat source switch unavailable for a while. The integration must never switch a working heat pump OFF because of that:

- While unavailable, the switch counts as OFF: no zone joins, and the minimum off time counts from when it went away. The run itself (calling zone, sync rule) is kept, because the heat pump may still be running.
- **Back ON:** it never stopped, because a relay that lost power restarts OFF. The run and the minimum on time simply go on.
- **Back OFF:** it stopped when it went away; that was the off time.

This is why the heat source relay's power-on state must be **OFF** ([Getting started](getting-started.md#4-switches)).

## Targets: schedules and holiday

What sets the target, highest first:

1. **Failsafe** (every sensor dead, [below](#failsafe)).
2. **Holiday:** every zone's target is its own holiday temperature (default 18 °C); schedules are suspended. It ends at its end date and time, or when you switch it off; then the base targets apply again, and zones that are now too cold start at once.
3. **Manual schedule:** forces a zone to heat during a time window, whatever its temperature (state *Forced*). For safety it stops at the manual max temperature (default 25 °C) and resumes 1.0 °C below it. A forced zone is never the calling zone; other cold zones join its run as usual. A zone with a sensor fault is not forced, because the safety limit can't be checked.
4. **Auto schedule:** sets a zone's target during a time window, e.g. every day 13:00–17:00 at 23 °C. Two auto schedules for the same zone may not overlap.
5. **Base target** (the climate entity).

Details (windows across midnight, daylight saving time, one-shot and recurring schedules, holiday end): [Schedules and holiday](configuration.md#schedules-and-holiday).

## Heating season

A switch, default ON. When you switch it **OFF**:

- no zone asks for heat; the heat source request goes OFF and every valve closes at once, even during the minimum on time;
- sensor faults are still detected and notified (no daily reminder);
- schedules and holiday create no heating;
- the weekly [valve exercise](#valve-exercise) runs instead.

Switching it ON again starts normally: a cold zone waits its wait time first.

## Sensor faults

- A reading is **valid** if it is a number within the plausible range (default 0–40 °C, checked before the offset) and arrived within the sensor fault timeout (default 60 min). Implausible readings are ignored, as if nothing had arrived.
- Until the timeout the last valid reading counts, so a short dropout changes nothing.
- After the timeout the zone goes to **Sensor fault**: it creates no demand of its own, and its valve opens whenever the heat source runs for other zones ("follows the house"), so the room still gets some heat.
- You get a notification when the fault starts and when the sensor is back, and a daily reminder (default 08:00) while it lasts, in the heating season.
- A zone that has had no reading at all since the start waits for its first one (reason "Waiting for a sensor reading") and becomes a sensor fault after the timeout.

## Failsafe

What happens when the integration can't control normally.

**Every sensor dead, Home Assistant running.** When no sensor has sent a valid reading for the failsafe operation delay (default 24 h) and every zone is in sensor fault, the integration heats on a timetable: every day from failsafe operation start to stop (default 10:00–15:00) every valve opens and the heat source runs (the minimum on and off times still apply). Heating season only. The first valid reading from any sensor ends it at once. You get a notification when it starts and when it ends; the mode sensor shows `failsafe`.

**Home Assistant down.** Then the integration can't act at all. This is what the Shelly watchdog scripts are for ([Shelly scripts](shelly-scripts.md)):

- after 5 hours without a heartbeat, the valve Shellys open every valve and the heat source Shelly switches OFF;
- after 24 hours, if the last heartbeat said heating season ON, the heat source Shelly requests heat every day 10:00–15:00 by its own clock (or, without a valid clock, 5 hours ON and 19 hours OFF);
- when Home Assistant is back, the scripts stop acting and the integration takes over.

Relays without the scripts stay as they were. The [external watchdog](getting-started.md#9-external-watchdog) tells you that Home Assistant is down.

## Valve exercise

Valve actuators that don't move for months can stick. Outside the heating season, once a week (default Monday 08:00), each valve opens for 15 minutes, one after another in the YAML order, with the heat source off. Zones without a valve are skipped. A run missed because Home Assistant was down is skipped until the next week. Switching the heating season ON ends it. It is only logged, not notified.

## Alerts

- **Switch not following command:** a relay that differs from what the integration wants, or is unavailable, for 3 reconcile intervals (≈ 3 min) is notified once, and again when it follows. Normal switching never alerts. The integration retries the command after 1, 2, 4 and 8 minutes, then every 15 minutes.
- **Long run:** the heat source has been running longer than the long run alarm (default 12 h), e.g. because a zone can't reach its target. Notified once, and when the heat source switches OFF again.
- **Shelly watchdog not answering** and **script parameters differ:** see [Shelly scripts](shelly-scripts.md).

The alerts sensor lists every active alert; [Notifications](configuration.md#notifications) lists every message.

## Restart

After a Home Assistant restart the integration continues where it stopped: running wait times, the minimum on and off times, the calling zone, sensor faults, schedules, holiday and every setting are stored. The relays' states are read back. A wait time that had 10 minutes left before the restart has about 10 minutes left after it.

## Shadow mode

With **Shadow mode** ON the integration makes every decision but switches nothing. It treats its decisions as if the relays had followed, so they stay consistent. Heartbeats to the Shellys and the watchdog ping continue. This is the state after the first installation; see [Getting started](getting-started.md#7-shadow-run).

Switching Shadow mode ON while the integration was in control sends the heat source and every valve OFF once, then nothing more. For switching the relays by hand, see [Troubleshooting](troubleshooting.md#switching-by-hand).

## Hot water

If your heat pump also makes domestic hot water, it usually pauses floor heating during a hot water run. The integration doesn't know about that and doesn't need to: the heat request may stay ON, and the heat pump resumes floor heating afterwards. Zones then take a little longer to warm up, and the minimum on time also counts the hot water time.

## Limitations

- It is an on/off controller with hysteresis: no PID, no weather compensation, no learning of how long a zone takes to warm up. The flow temperature is the heat source's business (e.g. its weather curve).
- No automatic preheat before a schedule or the end of holiday: start them early enough for your floor.
- One heat source switch for all zones.
- "The heat source runs" means its request switch is ON. The integration can't see whether the compressor or burner really runs.
- Configuration is in YAML; daily settings are in the UI.
