# Getting started

This guide takes you from nothing to a running installation: install the integration, connect your sensors and relays, let it run in **shadow mode** (it decides but switches nothing) and then hand over control.

> The integration controls a heating system. Keep your existing controller in charge until you have checked the integration's decisions in shadow mode.

## 1. Before you start

You need:

- **Home Assistant 2026.9.0 or newer.**
- **One temperature sensor per zone,** already in Home Assistant (any `sensor` with a temperature unit: Bluetooth, Zigbee, Z-Wave, Wi-Fi, …).
- **One switch per valve,** for the zones that have a valve actuator, and **one switch for the heat source request** (the contact that asks the heat pump or boiler for heat, e.g. on its room thermostat terminals). Any relay that Home Assistant can switch works: Shelly, Sonoff, Zigbee or Z-Wave relays, a relay board, and so on.
- **A flow path whenever the heat source runs:** a zone without a valve, a bypass, or a buffer tank / hydraulic separator. The integration does not wait for valves to open before it requests heat (actuators take minutes), and in some situations every valve may be closed while the heat source runs, e.g. when every zone is too warm during the heat source's minimum on time. If every zone has a valve, it logs a warning at startup.
- A fresh **backup** of Home Assistant (*Settings → System → Backups*).

[How it works](how-it-works.md) explains the logic in plain words. It's worth reading before you go live.

## 2. Install

**With HACS:**
1. HACS → ⋮ → *Custom repositories* → add `https://github.com/vadaszi/multizone-floor-heating-manager`, type *Integration*.
2. Search for "Multizone Floor Heating Manager" and download it.

**Manually:** copy the folder `custom_components/multizone_floor_heating_manager` from this repository into the `custom_components` folder of your HA configuration directory (next to `configuration.yaml`).

Don't restart yet; the configuration comes first.

## 3. Temperature sensors

Check each zone's sensor in *Developer tools → States*:

- It has a temperature unit (°C, °F or K).
- It reports regularly, and its **last reported** time moves even when the value stays the same. The integration uses that time to tell a quiet sensor from a dead one: after the sensor fault timeout (default 60 min) without a report, the zone goes into sensor fault.

A sensor that reads a little high or low can be corrected with `sensor_offset` in the YAML.

## 4. Switches

Note the entity id of each valve switch and of the heat source switch. Then check each relay:

- **Power-on state: OFF.** After a power cut the relay must start switched off. This matters most for the heat source relay: when its switch comes back ON after being unavailable, the integration concludes that it never lost power and keeps the heat source running, and when it comes back OFF it counts the time since it went away as off time.
- No timers, schedules or automations of its own on the relay. The integration switches it.
- For valve actuators: normally closed actuators are expected (switch ON = valve open).

### Shelly relays: the safety net

With Shelly relays (Gen2 or newer) you can install the integration's **watchdog scripts** on them. A script on each relay waits for a heartbeat that the integration sends every 5 minutes. If Home Assistant stops, the scripts act on their own:

- after 5 hours without a heartbeat the valve relays open every valve and the heat source relay switches OFF;
- after 24 hours, if it was heating season, the heat source relay starts a **failsafe operation**: it requests heat every day from 10:00 to 15:00, so the house does not cool down while HA is broken;
- when the heartbeats return, they stop at once and the integration takes over again;
- the integration alerts you when a Shelly or its script stops answering.

Without the scripts, or with other relays, a relay simply **stays as it was** when Home Assistant stops, e.g. with the heat source running or switched off, until someone switches it. Only the [external watchdog](#9-external-watchdog) then tells you that HA is down.

Setting up the scripts takes about 15 minutes per device: [Shelly scripts](shelly-scripts.md). You can do it now or later; the integration works without them.

## 5. Configuration

Add to `configuration.yaml`, with your own entity ids:

```yaml
multizone_floor_heating_manager:
  heat_source_switch: switch.heat_pump_request
  notify:
    - notify.mobile_app_your_phone
  relays_without_watchdog:     # relays without the watchdog script
    - switch.heat_pump_request
    - switch.valve_living_room
  zones:
    - id: living_room
      name: Living room
      sensor: sensor.living_room_temperature
      valve: switch.valve_living_room
    - id: bathroom
      name: Bathroom
      sensor: sensor.bathroom_temperature
      valve: none              # a zone without a valve
```

- **Zone `id`:** lowercase letters, digits and `_`. The stored state, schedules and entities belong to it, so choose it carefully: it must not change later. The `name` can change any time.
- **Order of the zones:** if two zones are equally cold at the same moment, the first one listed becomes the calling zone ([How it works](how-it-works.md#calling-zone-and-sync-rule)).
- **`relays_without_watchdog`** lists every switch without the watchdog script, one by one (a multi-channel relay is one entry per channel). Every other switch must be on a Shelly listed under `shellys_with_watchdog` (see [Shelly watchdogs](configuration.md#shelly-watchdogs)); a switch in neither list is a configuration error, so nothing is left unprotected by accident. With Shelly scripts installed, the example becomes:
  ```yaml
    shellys_with_watchdog:
      - name: Valves
        host: 192.0.2.11       # the Shelly's address
        script_id: 1
        switches: [switch.valve_living_room]
      - name: Heat pump
        host: 192.0.2.12
        script_id: 1
        switches: [switch.heat_pump_request]
  ```
- **`notify`** (optional): where notifications go, e.g. the companion app. See [Notifications](configuration.md#notifications).
- Every key: [Configuration reference](configuration.md). A complete example: [`examples/configuration.example.yaml`](../examples/configuration.example.yaml).

Check the configuration (*Developer tools → YAML → Check configuration*), then restart Home Assistant.

## 6. After the restart

- *Settings → Devices & services* shows "Multizone Floor Heating Manager" with one device per zone ("Living room floor heating", …) and a "Floor heating" device for the house. Assign each zone device to its area in the device settings if you like.
- *Settings → System → Logs* (search "multizone_floor_heating_manager") should show no errors. A persistent notification lists any entity that Home Assistant doesn't know (a typo in the YAML).
- The entities are listed in [Entities](configuration.md#entities): per zone e.g. `climate.living_room_floor_heating` and `sensor.living_room_floor_heating_reason`, for the house e.g. `binary_sensor.floor_heating_heat_request`.
- **Shadow mode** (`switch.floor_heating_shadow_mode`) is **ON**: the integration decides but switches nothing.
- Set each zone's target temperature on its climate entity. Check the parameters (the *Configuration* entities of the "Floor heating" device and of each zone): minimum on and off time of the heat source, hysteresis and wait time per zone. The defaults suit a heat pump with underfloor heating.
- Install the [example dashboard](dashboard.md). It shows everything below at a glance, with a 24-hour graph per zone and a card that explains every state and reason.

## 7. Shadow run

Let the integration run in shadow mode for a few days next to your existing controller. It decides as if it were in charge and treats its own decisions as done, but it sends no commands. Watch:

- **Heat request** (`binary_sensor.floor_heating_heat_request`) and **Heat source** (`sensor.floor_heating_heat_source`): when it would ask for heat, and why not when it doesn't;
- each zone's **State** and **Reason**: which zone would wait, call or heat, and why;
- the `valve` attribute of each zone's climate entity: which valves it would open;
- **Alerts** (`sensor.floor_heating_alerts`) and the notifications: sensor faults are reported in shadow mode too.

Things to look for: does a zone start heating when you expect it to? Do the heat source runs last long enough (at least the minimum on time)? Does a zone with a short window opening wait instead of starting the heat source? [How it works](how-it-works.md) explains each decision.

## 8. Going live

When the decisions look right:

1. Switch your old controller off, or disconnect it from the relays, so only one controller switches them.
2. Make sure the relays' own settings are right (power-on state OFF, no timers).
3. Switch **Shadow mode** OFF. From the next run (within a minute) the integration switches the relays.
4. Watch the first hours:
   - The real switch states count from now on. If shadow mode believed the heat source was running but the real switch is OFF, that counts as a stop: the **minimum off time** (default 60 min) runs before the first real heat request. The zones that need heat open their valves at once.
   - If the heat source was already running when you went live and no zone needs heat, it keeps running until its minimum on time has passed, with every valve open, so the heat goes somewhere. This happens once.
   - Each valve relay should follow its zone's `valve` attribute. A relay that doesn't follow its command for 3 minutes raises the alert "switch not following command".
5. Keep the old controller at hand for the first days.

To go back, switch Shadow mode ON: the integration switches the heat source and every valve OFF once and then sends nothing more ([Shadow mode](configuration.md#shadow-mode); for switching relays by hand see [Troubleshooting](troubleshooting.md#switching-by-hand)).

## 9. External watchdog

The integration can tell you about sensor faults and relays, but not that Home Assistant itself has stopped. An external service can: the integration pings it every 5 minutes, and it emails you when the pings stop.

1. Create a free account on [healthchecks.io](https://healthchecks.io) and add a check, e.g. "Floor heating".
2. Set its schedule: **Period 5 minutes**, **Grace time 30 minutes**. HA updates and restarts (typically 5–20 min) then raise no false alarm; a real outage is reported after about 35 minutes.
3. Choose how you want to be notified (email is on by default).
4. Copy the check's **ping URL** into HA's `secrets.yaml`. It is a secret: don't share it.
   ```yaml
   floor_heating_watchdog_url: https://hc-ping.com/<your-check-uuid>
   ```
5. Add it to the integration's YAML and restart HA:
   ```yaml
   multizone_floor_heating_manager:
     watchdog_ping_url: !secret floor_heating_watchdog_url
   ```
6. Within a minute the check shows its first ping and turns green. A check that never received a ping does not alert, so check this once.
7. Test it when convenient: stop HA for longer than the grace time. You get a "down" email, and an "up" email after the restart.

Other services with a push URL (e.g. an Uptime Kuma push monitor) work the same way: [External watchdog](configuration.md#external-watchdog).

## 10. Next steps

- [Schedules and holiday](configuration.md#schedules-and-holiday): warmer or cooler times, or forcing a zone to heat; holiday mode.
- [Shelly scripts](shelly-scripts.md), if you haven't installed them yet.
- [Troubleshooting](troubleshooting.md) when something looks wrong.

## Trying it out without hardware

To see how the logic behaves before any relay is installed, use template helpers as stand-ins. Leave Shadow mode ON, or switch it OFF: the integration then switches the stand-ins, so you can watch its commands safely.

**Stand-in switches:** *Settings → Devices & services → Helpers → Create helper → Template → Template a switch*. Leave the value template and both actions empty; the switch then remembers what it was switched to. **Switch each one OFF once** after creating it: a new template switch has no state ("unknown"), and the integration sends no command to a switch in that state. List the stand-ins in `relays_without_watchdog`.

**Stand-in sensors** (if a zone has no thermometer yet): a number helper (*Create helper → Number*, e.g. 15–30, step 0.1) to set the temperature by hand, and a template sensor that reports it every few minutes. The regular report matters: a template sensor that only changes when you move the slider looks dead after the sensor fault timeout. In `configuration.yaml`:

```yaml
template:
  - triggers:
      - trigger: time_pattern
        minutes: "/5"
      - trigger: state
        entity_id: input_number.try_living_room
    sensor:
      - name: Try living room temperature
        unique_id: try_living_room_temperature
        unit_of_measurement: "°C"
        device_class: temperature
        state: "{{ states('input_number.try_living_room') }}"
```

When the real devices arrive, replace the entity ids in the YAML, update `relays_without_watchdog` and `shellys_with_watchdog`, and restart. Settings, schedules and the zone state are kept, because they belong to the zone ids. Then delete the stand-ins.
