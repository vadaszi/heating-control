# Getting started: install and a shadow-mode trial

This guide installs floorheat and runs it in **shadow mode** next to your existing heating controller. In shadow mode floorheat reads your sensors and makes all its decisions, but it switches nothing. You compare its decisions with what your current controller does before letting floorheat take over.

You don't need the valve and heat source switches (e.g. Shelly relays) yet: **stand-in switches** (HA helpers) take their place until the real ones are installed.

> floorheat controls a heating system. Keep your existing controller in charge until you have checked floorheat's decisions. How the logic works is described in the [design](design.md).

## 1. Install

The integration needs Home Assistant 2026.9.0 or newer.

**With HACS (custom repository)**
1. HACS → ⋮ → *Custom repositories* → add this repository's URL, type *Integration*.
2. Search for "Floor Heating Zone Control" and download it. Without a release, HACS offers the latest version of the default branch.

**Manually**
1. Copy the folder `custom_components/floorheat` from this repository into the `custom_components` folder of your HA configuration directory (next to `configuration.yaml`).

Don't restart yet; the configuration comes first.

## 2. Temperature sensors

Each zone needs a `sensor` entity with a temperature unit (°C, °F or K), e.g. a Bluetooth thermometer. Check in *Developer tools → States* that each one reports regularly. Each sensor must update its **last reported** time even when the value doesn't change (floorheat uses that time to detect a dead sensor).

## 3. Stand-in switches

Create one switch per valve and one for the heat source request:

1. *Settings → Devices & services → Helpers → Create helper → Template → Template a switch*.
2. Give it a name, e.g. "Valve living room (stand-in)" or "Heat source (stand-in)".
3. Leave **Value template** and both actions **empty**. The switch then simply remembers what it was switched to, also across restarts.
4. **Switch it OFF once** (e.g. on its entity page). A new Template switch has no state ("unknown") until it's switched the first time, and floorheat never sends commands to a switch with an unknown or unavailable state.

Note each switch's entity id (e.g. `switch.valve_living_room_stand_in`).

In shadow mode the stand-ins are never switched. If you switch Control active ON during the trial, floorheat switches the stand-ins instead of real devices, so you can watch its commands safely.

## 4. Configuration

Add to `configuration.yaml`, with your own entity ids:

```yaml
floorheat:
  heat_source_switch: switch.heat_source_stand_in
  notify:
    - notify.mobile_app_your_phone
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
- Every key is described in the [configuration reference](configuration.md).

Check the configuration (*Developer tools → YAML → Check configuration*), then restart HA.

## 5. After the restart

- Look for errors in *Settings → System → Logs* (filter "floorheat") and for a persistent notification about unknown entities or notify targets.
- The entities appear as `climate.floorheat_<zone>`, `sensor.floorheat_<zone>_state`, `sensor.floorheat_<zone>_reason` and so on (full list: [Entities](configuration.md#entities)).
- `switch.floorheat_control_active` is **OFF** (shadow mode) and `sensor.floorheat_mode` shows the attribute `shadow: true`.
- Set each zone's target temperature on its climate entity, and adjust the parameters (`number.floorheat_…`) if the defaults don't fit.

## 6. The shadow run

Run it for one to two weeks next to your current controller. Compare in the entity history:

- `binary_sensor.floorheat_heat_request`: when floorheat would request heat, and for how long (attribute `on_duration`);
- `sensor.floorheat_<zone>_state` and `sensor.floorheat_<zone>_reason`: which zone would call, wait or heat, and why;
- the `valve` attribute of `climate.floorheat_<zone>`: which valves floorheat would open;
- `sensor.floorheat_alerts` and the notifications: sensor faults are also reported in shadow mode.

Any climate or history card works for this; an example dashboard comes with v1.1.

## 7. Later: the real switches

When the Shelly relays are installed:

1. Install the watchdog scripts ([Shelly scripts](shelly-scripts.md)).
2. Replace the stand-in entity ids in `configuration.yaml` with the real switches and restart. Stored settings and zone state are kept, because they belong to the zone ids.
3. Delete the stand-in helpers.

Going live (Control active ON) follows the go-live checklist that comes with the v1 release. Note: from that moment the real switch states count. If shadow mode believed the heat source was running, the real switch reads OFF, which counts as a stop, so the minimum OFF time (default 60 min) runs before the first real heat request.
