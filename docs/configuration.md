# Configuration reference

floorheat is set up in `configuration.yaml`. The YAML holds only the **wiring**: which sensor and which switches belong to which zone. Every value you change in daily use (set points, parameters, heating season, Control active) is changed from the HA UI and stored by HA ([design §5.6](design.md#56-configuration-d-52-d-55)).

> The entities that show and change those values are added in the next development phase (P6). Until then the integration runs with the defaults from [design §4](design.md#4-parameters), in shadow mode.

## Example

```yaml
floorheat:
  heat_source_switch: switch.heat_pump_request
  zones:
    - id: living_room
      name: Living room
      sensor: sensor.living_room_temperature
      valve: switch.valve_living_room
      sensor_offset: -0.2
    - id: bathroom
      name: Bathroom
      sensor: sensor.bathroom_temperature
      valve: none
```

The entity ids are examples: use your own.

## Keys

### Top level

| Key | Type | Default | Description |
|---|---|---|---|
| `heat_source_switch` | `switch` entity | required | The switch that requests heat from the heat source (e.g. a relay on the heat pump's thermostat terminals). |
| `zones` | list | required | At least one zone; see below. The order matters: on a tie, the zone listed first becomes the calling zone. |
| `plausible_min` | number | 0 °C | Readings below this are ignored as implausible. |
| `plausible_max` | number | 40 °C | Readings above this are ignored as implausible. |
| `reconcile_interval` | integer, seconds (10–300) | 60 | How often the outputs are checked and corrected. |
| `output_mismatch_alert` | integer ≥ 1 | 3 | Alert after this many reconcile intervals in which a switch does not follow its command or is unavailable. |

### Per zone

| Key | Type | Default | Description |
|---|---|---|---|
| `id` | slug | required | Stable key of the zone: lowercase letters, digits and `_`, starting with a letter (e.g. `living_room`). The stored state and the entity ids are based on it. **Never change it** once the zone is in use. |
| `name` | string | required | Display name. Names must be unique (case and surrounding spaces are ignored). |
| `sensor` | `sensor` entity | required | The zone's temperature sensor. It must have a temperature unit (°C, °F or K). |
| `valve` | `switch` entity or `none` | required | The zone's valve actuator switch, or `none` for a zone without a valve. |
| `power_sensor` | `sensor` entity | – | Power measurement of the valve channel. Accepted now; used by the actuator fault check in a later release. |
| `sensor_offset` | number | 0 | Calibration added to every reading (range ±5 °C). |

### Rules
- Temperatures in the YAML (`sensor_offset`, `plausible_min`, `plausible_max`) are in **your HA unit system** (°C or °F). A sensor reading is converted from the sensor's own unit.
- Each switch may be mapped only once: two zones cannot share a valve switch, and a valve switch cannot also be the heat source switch.
- **At least one flow path.** floorheat assumes water can flow whenever the heat source request is ON: through a zone without a valve, a bypass or a buffer/hydraulic separator. If every zone has a valve, a warning is logged at startup. Make sure your installation has such a path.

## Checks at startup

- A structural error (missing key, wrong entity domain, duplicate zone id or name, invalid id, value out of range, unknown key) stops the setup. HA shows "Invalid config" and the log names the problem. For an invalid id, a valid one is suggested.
- An entity that HA does not know (neither registered nor reporting a state) is logged as an error and shown as a persistent notification once HA has started. The integration still runs:
  - an unknown sensor gives no reading, and the zone goes into sensor fault after the timeout;
  - an unknown switch counts as unavailable, which is OFF.
- A sensor without a temperature unit is logged once as a warning; its readings are ignored.

## How it runs

- **Reconcile loop.** Every `reconcile_interval`, and at once whenever a mapped sensor or switch changes state, floorheat computes the desired outputs from the current states and switches every output that differs. It starts only when HA has finished starting, so entities that are still loading neither get commands nor count as failures.
- **Retries.** A switch that does not follow gets its command again after 1, 2, 4 and 8 minutes, then every 15 minutes. Nothing is sent to an unavailable switch; when it returns, it is corrected at once. After `output_mismatch_alert` intervals the alert "output not following command" is raised (notifications follow in P6; until then it is logged as a warning).
- **Restart.** The logic state (timers, calling zone, sensor faults, heat pump ON/OFF times) and the UI settings are stored in HA's `.storage/floorheat` file, at most every 30 seconds and when HA stops. After a restart the switch states are read back and control continues where it stopped.
- **Unavailable heat source switch.** It counts as OFF while it is unavailable. If it comes back ON, the heat pump never stopped (a device that lost power restarts OFF), so it is not switched OFF because of a Wi-Fi glitch.

## Shadow mode

After the first installation **Control active is OFF** (shadow mode). floorheat then reads everything and makes its decisions, but it sends **no** switch commands. It treats its own decisions as if the switches had followed, so the decisions stay consistent. Use it to compare floorheat with your existing controller before going live.

- **Switching Control active OFF** (live → shadow) sends one final safe set: heat source OFF, all valves OFF. A switch that is unavailable at that moment, or does not follow, gets the OFF again (with the retries above) until it has reported OFF once, also across a restart. After that, nothing more is sent.
- **Switching Control active ON** (shadow → live) sets every output to the desired state at the next run.
- **Going live after shadow mode:** from then on the real switch states count. If shadow mode believed the heat source was running, the real switch reads OFF. That counts as a stop, so the minimum OFF time (default 60 min) runs before the heat source is first requested. The zones that need heat already open their valves.
