# Example dashboard

Multizone Floor Heating Manager ships an example dashboard: [`examples/dashboard.example.yaml`](../examples/dashboard.example.yaml). It uses only Home Assistant's built-in cards, so there is nothing extra to install.

## What it shows

Two views, both of the "sections" type (each section is a column; Home Assistant places as many side by side as the screen allows, and stacks them on a phone).

**"Floor heating" (daily use):**
- **At a glance** (the badges at the top, always shown): the heat request with its running time, the heat source status (what it does and why), the heat pump relay, the number of alerts; per zone its temperature, state and reason; per zone with a valve the valve the integration wants ("valve wanted") and what the relay does ("valve relay").
- **One section per zone**, at most three side by side, so the zones fill the rows (with five zones: three, then two):
  - a thermostat card: the zone temperature and the base target, which you can change;
  - the zone's state and reason, the end of a running timer ("Until"), the effective target, and the valve relay (zones with a valve);
  - a graph of the last 24 hours: the temperature and the effective target as lines, and the state, reason and valve as coloured bars on the same time axis, so you can see what the integration decided and when. The temperature line is the sensor's raw reading, without the zone's sensor offset.
- **House**, after the zones (with five zones next to the fifth): the alerts (always shown: the active alerts, or "No active alerts."), the heat request with its running time, the heat source status with the end of a running minimum on/off time ("Until"), the mode (normal / holiday / failsafe), and the Heating season and Shadow mode switches.
- **What the states and reasons mean**, after the house: a card that explains every heat source status, the mode, every zone state and reason in plain words. It is meant for learning; delete it when you no longer need it.

**"Setup" (rarely used):**
- **One card per group of parameters**: Hysteresis (every zone), Wait time (every zone), Heat source (minimum on and off time, long run alarm), Sensors and failsafe (sensor fault timeout and reminder time, failsafe operation delay, start and stop), Manual schedules (max temperature, resume difference), Off season (valve exercise day, time and duration per valve).
- **Holiday**: the Holiday switch, the end date and end time, each zone's holiday temperature, and a line that shows the stored end, e.g. "Ends: Fri 2026-10-02 15:00" or "No end date: holiday runs until you switch it off" ([how holiday works](configuration.md#holiday)).
- **Schedules**: the list of schedules by label, and the form to add and delete them ([how schedules work](configuration.md#schedules-and-holiday), [the form](configuration.md#schedule-form-device-floor-heating)).
No card has a "toggle all" switch in its header, so one tap can never switch several settings at once.

## Installing it

1. Open the example file and replace the example zones with yours:
   - the example has two zones, "Living room" (with the valve `switch.valve_living_room`) and "Bathroom" (no valve), with the sensors `sensor.living_room_temperature` and `sensor.bathroom_temperature`;
   - the integration's entity ids start with the zone name, e.g. `climate.kitchen_floor_heating` and `sensor.kitchen_floor_heating_reason` for a zone named "Kitchen" (full list: [Entities](configuration.md#entities)). If you renamed an entity id, use yours;
   - blocks that belong to a zone are marked with a `# zone: …` comment: the zone's section in the daily view, its badges in the `badges:` list, and its rows in the Hysteresis and Wait time cards. Copy them for every further zone (a zone's section goes before the house section), and remove the valve rows and badges for a zone without a valve;
   - add each zone's holiday temperature to the Holiday card;
   - replace `switch.heat_pump_request` (the heat pump relay badge) with your heat source switch.
2. In Home Assistant: *Settings → Dashboards → Add dashboard → New dashboard from scratch*, open the new dashboard, then *pencil → three dots → Raw configuration editor*, paste the file and save.

The ids of the global entities (`…floor_heating_…` without a zone) are the same in every installation, unless you renamed them.
