# Getting started: install and a shadow-mode trial

This guide installs Multizone Floor Heating Manager and runs it in **shadow mode** next to your existing heating controller. In shadow mode the integration reads your sensors and makes all its decisions, but it switches nothing. You compare its decisions with what your current controller does before letting the integration take over.

You don't need the valve and heat source switches (e.g. Shelly relays) yet: **stand-in switches** (HA helpers) take their place until the real ones are installed.

> The integration controls a heating system. Keep your existing controller in charge until you have checked its decisions. How the logic works is described in the [design](design.md).

## 1. Install

The integration needs Home Assistant 2026.9.0 or newer.

**With HACS (custom repository)**
1. HACS → ⋮ → *Custom repositories* → add this repository's URL, type *Integration*.
2. Search for "Multizone Floor Heating Manager" and download it. There is no release yet, so HACS offers the latest version of the default branch.

**Manually**
1. Copy the folder `custom_components/multizone_floor_heating_manager` from this repository into the `custom_components` folder of your HA configuration directory (next to `configuration.yaml`).

Don't restart yet; the configuration comes first.

## 2. Temperature sensors

Each zone needs a `sensor` entity with a temperature unit (°C, °F or K), e.g. a Bluetooth thermometer. Check in *Developer tools → States* that each one reports regularly. Each sensor must update its **last reported** time even when the value doesn't change (the integration uses that time to detect a dead sensor).

## 3. Stand-in switches

Create one switch per valve and one for the heat source request:

1. *Settings → Devices & services → Helpers → Create helper → Template → Template a switch*.
2. Give it a name, e.g. "Valve living room (stand-in)" or "Heat source (stand-in)".
3. Leave **Value template** and both actions **empty**. The switch then simply remembers what it was switched to, also across restarts.
4. **Switch it OFF once** (e.g. on its entity page). A new Template switch has no state ("unknown") until it's switched the first time, and the integration never sends commands to a switch with an unknown or unavailable state.

Note each switch's entity id (e.g. `switch.valve_living_room_stand_in`).

In shadow mode the stand-ins are never switched. If you switch Control active ON during the trial, the integration switches the stand-ins instead of real devices, so you can watch its commands safely.

## 4. Configuration

Add to `configuration.yaml`, with your own entity ids:

```yaml
multizone_floor_heating_manager:
  heat_source_switch: switch.heat_source_stand_in
  notify:
    - notify.mobile_app_your_phone
  no_watchdog:               # the stand-ins are no Shellys: no heartbeat
    - switch.heat_source_stand_in
    - switch.valve_living_room_stand_in
  zones:
    - id: living_room
      name: Living room
      sensor: sensor.living_room_temperature
      valve: switch.valve_living_room_stand_in
    - id: bathroom
      name: Bathroom
      sensor: sensor.bathroom_temperature
      valve: none            # a zone without a valve
```

- Choose each zone `id` carefully (lowercase, digits, `_`): the entity ids and the stored state are based on it, so it must not change later.
- `notify` is optional; see [Notifications](configuration.md#notifications).
- `no_watchdog` lists every stand-in. The integration expects every other switch to be a Shelly running the watchdog script, and refuses a configuration where a switch is in neither place ([Shelly watchdogs](configuration.md#shelly-watchdogs)).
- Every key is described in the [configuration reference](configuration.md).

Check the configuration (*Developer tools → YAML → Check configuration*), then restart HA.

## 5. After the restart

- Look for errors in *Settings → System → Logs* (filter "multizone_floor_heating_manager") and for a persistent notification about unknown entities or notify targets.
- *Settings → Devices & services* shows "Multizone Floor Heating Manager" with one device per zone ("<zone name> floor heating") and a "Floor heating" device. Assign each zone device to its area in the device settings if you like ([Config entry and devices](configuration.md#config-entry-and-devices)).
- The entities appear as `climate.<zone>_floor_heating`, `sensor.<zone>_floor_heating_state`, `sensor.<zone>_floor_heating_reason` and so on, built from the zone name (full list: [Entities](configuration.md#entities)).
- `switch.floor_heating_control_active` is **OFF** (shadow mode) and `sensor.floor_heating_mode` shows the attribute `shadow: true`.
- Set each zone's target temperature on its climate entity, and adjust the parameters (the configuration entities of the "Floor heating" device) if the defaults don't fit.

## 6. The shadow run

Run it for one to two weeks next to your current controller. Compare in the entity history:

- `binary_sensor.floor_heating_heat_request`: when the integration would request heat, and for how long (attribute `on_duration`);
- `sensor.<zone>_floor_heating_state` and `sensor.<zone>_floor_heating_reason`: which zone would call, wait or heat, and why;
- the `valve` attribute of `climate.<zone>_floor_heating`: which valves the integration would open;
- `sensor.floor_heating_alerts` and the notifications: sensor faults are also reported in shadow mode.

Any climate or history card works for this; an example dashboard comes with v1.1.

A step-by-step checklist with space for notes: [trial checklist](trial-checklist.md).

## 7. Later: the real switches

When the Shelly relays are installed:

1. Install the watchdog scripts ([Shelly scripts](shelly-scripts.md)) and note each script's id.
2. Replace the stand-in entity ids in `configuration.yaml` with the real switches. Remove them from `no_watchdog` and add each Shelly under `shellys` with its address, script id and switches ([Shelly watchdogs](configuration.md#shelly-watchdogs)). Restart. Stored settings and zone state are kept, because they belong to the zone ids.
3. Check that the heartbeats arrive: the HA log shows "Shelly … answers" for each Shelly, and `sensor.floor_heating_alerts` stays at 0.
4. Delete the stand-in helpers.

Going live means switching Control active ON ([Shadow mode](configuration.md#shadow-mode)). Note: from that moment the real switch states count. If shadow mode believed the heat source was running, the real switch reads OFF, which counts as a stop, so the minimum OFF time (default 60 min) runs before the first real heat request.
