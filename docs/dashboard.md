# Example dashboard

Multizone Floor Heating Manager ships an example dashboard: [`examples/dashboard.example.yaml`](../examples/dashboard.example.yaml). It uses only Home Assistant's built-in cards, so there is nothing extra to install. Screenshots will be added before the first release.

## What it shows

**Daily view ("Floor heating"):**
- **Alerts**: a list of the active alerts (sensor faults, switches not following, Shellys not answering). The card appears only while there is at least one alert.
- **House**: the heat request with its running time, the mode (normal / holiday), and the Heating season and Control active switches.
- **One block per zone**:
  - a thermostat card: the zone temperature and the base target, which you can change;
  - the zone's state and reason, the end of a running timer ("Until"), the effective target, and the valve relay (zones with a valve);
  - a graph of the last 24 hours: the temperature and the effective target as lines, and the state, reason and valve as coloured bars on the same time axis, so you can see what the integration decided and when. The temperature line is the sensor's raw reading, without the zone's sensor offset.
- **Holiday**: the Holiday switch, the holiday end, and each zone's holiday temperature ([how holiday works](configuration.md#holiday)).
- **Schedules**: the list of schedules by label, and the form to add and delete them ([how schedules work](configuration.md#schedules-and-holiday), [the form](configuration.md#schedule-form-device-floor-heating)).
- **What the states and reasons mean**: a card that explains every zone state and every reason in plain words. It is meant for learning; delete it when you no longer need it.

**Settings view:** every parameter: heat source minimum on/off times, long run alarm, sensor fault timeout and reminder time, failsafe delay, manual max temperature and resume difference, valve exercise duration, and each zone's hysteresis and wait time.

## Installing it

1. Open the example file and replace the example zones with yours:
   - the example has two zones, "Living room" (with the valve `switch.valve_living_room`) and "Bathroom" (no valve), with the sensors `sensor.living_room_temperature` and `sensor.bathroom_temperature`;
   - the integration's entity ids start with the zone name, e.g. `climate.kitchen_floor_heating` and `sensor.kitchen_floor_heating_reason` for a zone named "Kitchen" (full list: [Entities](configuration.md#entities)). If you renamed an entity id, use yours;
   - blocks that belong to a zone are marked with a `# zone: …` comment. Copy them for every further zone, and remove the valve rows for a zone without a valve;
   - add each zone's holiday temperature to the Holiday card.
2. In Home Assistant: *Settings → Dashboards → Add dashboard → New dashboard from scratch*, open the new dashboard, then *pencil → three dots → Raw configuration editor*, paste the file and save.

The ids of the global entities (`…floor_heating_…` without a zone) are the same in every installation, unless you renamed them.
