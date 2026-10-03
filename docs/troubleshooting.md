# Troubleshooting

## Where to look

1. **The dashboard** ([example](dashboard.md)): each zone's state and reason, the heat source status and the alerts. Most "why does it (not) heat?" questions are answered by the reason. [How it works](how-it-works.md) explains each one.
2. **The alerts sensor** (`sensor.floor_heating_alerts`): every active problem, with a message.
3. **The history** of a zone's state, reason and valve relay, and of the heat request: what was decided when.
4. **The log** (*Settings → System → Logs*, search "multizone_floor_heating_manager"). Errors and warnings are shown by default. The integration also writes info lines (each heartbeat answer, commands that are repeated, schedules added or ended, the valve exercise), which Home Assistant hides unless you ask for them in `configuration.yaml`:
   ```yaml
   logger:
     default: warning
     logs:
       custom_components.multizone_floor_heating_manager: info
   ```
   Restart HA after adding it. At `debug` level the log also shows the cause of every single failed heartbeat (normally only a series of failures is reported).

## The configuration is refused at startup

Home Assistant shows "Invalid config" and the log names the problem: a missing or unknown key, a wrong entity domain, a duplicate zone id or name, an invalid zone id (a valid one is suggested), a value out of range, or a switch that is neither on a listed Shelly nor in `no_watchdog`. See [Checks at startup](configuration.md#checks-at-startup).

## "Floor heating: unknown entities"

A sensor or switch in the YAML is unknown to Home Assistant once it has started: a typo, or the integration that provides it did not load. The integration keeps running and treats it as unavailable: an unknown sensor gives no reading (the zone goes into sensor fault after the timeout), an unknown switch counts as OFF and gets no commands. Fix the YAML or the other integration and restart.

## A zone doesn't heat

Look at the zone's **reason**:

| Reason | What to do |
|---|---|
| Idle | The room temperature is above the start temperature (target − hysteresis). Check the effective target: holiday or an auto schedule may have changed it. |
| Waiting period | The wait time runs (attribute `until`); the zone starts if it's still too cold then. |
| Waiting for heat source minimum off time | The heat source stopped less than its minimum off time ago. The valve is already open; the request follows at `until`. |
| Heating season off | Switch the heating season on. |
| Sensor fault, valve follows the heat source | See [sensor fault](#sensor-fault) below. |
| Heating, heat source switch unavailable | The zone wants heat, but the heat source relay doesn't report. Check the relay and its Wi-Fi. |

If the reason says *Heating* or *Calling zone* but nothing gets warm: check **Shadow mode** (ON = nothing is switched), the valve relay's state, and whether the heat source reacts to its relay.

## The heat source runs although every zone is warm

The heat source status says **Running for minimum on time (spreading heat)**: it must run until its minimum on time is over (attribute `until`), and every valve is open so the heat goes somewhere. This also happens once after the first start, or when you go live, if the heat source was already running. See [Heat source protection](how-it-works.md#heat-source-protection).

## A zone without a valve shows "Heating"

Its climate entity shows *Heating* whenever the heat source runs for any zone: warm water flows through it. Its state and reason say what the zone itself wants. This is normal.

## Sensor fault

The zone has had no valid reading for longer than the sensor fault timeout (default 60 min).

- Check the sensor in *Developer tools → States*: is it available, and does its **last reported** time move?
- A reading outside the plausible range (default 0–40 °C) counts as no reading. Check `plausible_min` / `plausible_max`.
- A sensor without a temperature unit is ignored (a warning in the log says so).
- Battery, radio range, or the integration that provides the sensor.

While the fault lasts, the zone's valve opens whenever the heat source runs for other zones, so the room still gets some heat. The first valid reading ends the fault.

## "Switch not following command"

A relay has differed from what the integration wants, or has been unavailable, for 3 reconcile intervals.

- **Unavailable:** the relay is offline (power, Wi-Fi) or its integration has a problem.
- **Available but not following:** something else switches it: an automation, a schedule or timer on the device itself, the Shelly app, or a watchdog script that has acted (it stops when the heartbeat returns). The integration repeats its command after 1, 2, 4 and 8 minutes, then every 15 minutes; the log says "… does not follow; sending … again".

## "Shelly watchdog not answering"

The integration's heartbeat to a Shelly failed several times in a row (default 3, ≈ 15 min). The message names the cause:

| Cause | What to check |
|---|---|
| unreachable | The Shelly is offline, or `host` in the YAML is wrong. Try its web UI. |
| script not running (404) | The script was stopped, or "Run on startup" is off and the device restarted, or `script_id` is wrong. |
| authentication failed (401) | Authentication is on: set `password` in the `shellys` entry (`!secret`), or the password is wrong. |
| unusable answer | Another script answers at that id, the wrong script is on the device (valve script on the heat source Shelly or the other way round), or the script is older than the integration expects. |

When it answers again, you get "Shelly watchdog answering again". More: [Shelly scripts → Troubleshooting](shelly-scripts.md#troubleshooting).

## "Shelly script parameters differ"

A script's `heartbeat_timeout_s` (or `check_interval_s`, if you set `heartbeat_check_interval`) is not what the YAML expects (`heartbeat_timeout`, default 18000 s = 5 h). Usually a value changed for a bench test and not set back. Fix the script's CONFIG block or the YAML; the alert clears when they match.

## Failsafe

The mode is `failsafe`: no sensor has sent a valid reading for the failsafe operation delay (default 24 h). Every zone is in sensor fault, so check what all sensors have in common: the Bluetooth adapter or proxy, the Zigbee coordinator, the sensors' integration. The first valid reading ends the failsafe. Until then the integration heats daily between failsafe operation start and stop. See [Failsafe](how-it-works.md#failsafe).

## No heartbeats or watchdog pings: "the reconcile loop has not completed a run"

The integration sends heartbeats and watchdog pings only while its control loop works. If it is broken, e.g. after a Home Assistant update, the log says so, the Shelly watchdogs act after their timeout as if HA had stopped, and the external watchdog alerts you. Check the log for the error and [report it](#reporting-a-problem).

## Switching by hand

There is no separate manual mode. To control the relays yourself (e.g. to heat one room outside the logic, or for heat pump maintenance):

1. Switch **Shadow mode** ON. The integration switches the heat source and every valve OFF once, then sends no more commands.
2. Switch the relays as you like, in Home Assistant or the device app. No minimum on/off time, no temperature limit and no other protection applies now: you are in charge.
3. Leave the watchdog scripts running. The integration keeps sending heartbeats, so the scripts act only if Home Assistant itself stops.
4. Switch **Shadow mode** OFF to return to automatic control. It continues from the relays' real states; the heat source's minimum off time may delay the next start.

## Entity ids look odd after an update

A new entity added by an update to a zone device that is placed in an area gets the area name in its id (e.g. `number.bedroom_bedroom_floor_heating_holiday_temperature`). That is how Home Assistant names new entities; rename the id in the entity settings if you like. See [Updating](configuration.md#updating).

## Reporting a problem

Open an issue on GitHub with: the Home Assistant version, the integration version, your YAML (remove passwords, the ping URL and addresses), the relevant log lines (with the info level above), and what happened compared with what you expected. The zone's state, reason and the heat request from the history help a lot.
