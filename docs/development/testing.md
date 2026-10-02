# Testing

```bash
.venv/bin/pytest --cov    # Python: core, simulation, adapter, repository checks
npm test                  # Shelly scripts
```

## Layout

| Folder | What | How |
|---|---|---|
| `tests/core/` | The control core: one test per rule, the acceptance scenarios (`test_scenarios.py`, named `test_aNN_…`), config, state, schedules, failsafe, exercise, alerts, heartbeat evaluation. | Plain pytest, no HA. `harness.py` provides `Scenario`: simulated time, sensor readings and actual switches, and a perfect reconcile loop (switches follow, a heat source change steps again at the same time). **Every step is checked to be idempotent.** Hypothesis property tests for schedules (overlap check against a brute-force minute scan) and state round trips. |
| `tests/core/test_core_purity.py` | The core imports nothing from Home Assistant and never reads the clock. | AST check of `core/`. |
| `tests/sim/` | Multi-day simulations against a simple thermal model per zone (heats while its valve is open and the heat source runs, cools otherwise; disturbances such as an open window, sensor dropouts, a week of schedules, 34 h of dead sensors). | Invariants checked on every step: min ON/OFF respected, every zone stays near its target, the sync rule fires at most once per run, at most one calling zone, idempotency. |
| `tests/adapter/` | The HA side: setup and config entry, inputs, reconcile and backoff, shadow mode, restart, entities and °F, notifications, heartbeat client, watchdog ping, schedules, services, form, dashboard YAML, upgrades. | `pytest-homeassistant-custom-component` with mocked sensor and switch entities, captured service calls and a frozen clock (`conftest.py`, `World`). |
| `tests/shelly/` | The Shelly scripts. | `node --test`. `shelly_mock.mjs` is a minimal Shelly runtime (`Timer`, `HTTPServer`, `Shelly.call`, `KVS`, uptime, device clock) with simulated time; the tests load the same files that go on the device. `subset.test.mjs` checks the language subset and that the scripts refer only to the user manual. |
| `tests/test_repository.py` | The repository: no file refers to private development documents, and every relative link in the Markdown files resolves. | Plain pytest. |

**Coverage:** CI fails below 95 % branch coverage of `core/` (it is 100 % today).

**CI** (`.github/workflows/ci.yml`): ruff, mypy, pytest with coverage, the Shelly tests, gitleaks over the full history, hassfest and the HACS validation on every push and pull request; a weekly job runs the Python tests against the newest Home Assistant.

## Acceptance scenarios

The minimum set; each has a test named after it. Defaults apply unless stated (target 22.0 °C, hysteresis 0.2 °C, wait time 30 min, minimum on/off 60 min, sensor fault timeout 60 min, manual max 25 °C). All zones have a valve unless stated. "HP" = heat source request.

| # | Scenario | Expected |
|---|---|---|
| A1 | HP off; zone 1 drops to its start temperature at 06:00 and stays there | waiting 06:00; heating + HP ON at 06:30; zone 1 = calling zone |
| A2 | As A1, but zone 1 is back above its start temperature at 06:30 (window closed) | idle at 06:30; HP stays OFF |
| A3 | As A1, but the temperature goes above and back below the start temperature during the wait; at or below it at 06:30 | heating at 06:30 (only the check at the end counts) |
| A4 | HP ON for zone 1; zone 2 drops to its start temperature | zone 2 heating immediately, no wait |
| A5 | Zone 2 is waiting when zone 1 starts the HP | zone 2 joins immediately |
| A6 | Zone 1 (calling) reaches its target; zone 3 at target − 0.1 (above its start temperature) | zone 3 joins; the sync rule doesn't fire again in this run |
| A7 | Sync fired; zone 3 reaches its stop temperature | zone 3 idle; HP stays ON while any zone heats |
| A8 | All zones reach their stop temperature 40 min after HP ON; zone 4 is at 25 °C | every valve except zone 4 open until 60 min; then HP OFF, valves by the normal logic |
| A9 | HP went OFF at 08:00; zone 2 hits its start temperature at 08:10 | wait 08:10–08:40; if the check passes: zone 2 heating, valve open at 08:40, HP ON at 09:00 (min OFF); if zone 2 reaches its stop temperature before 09:00 → idle, HP stays OFF |
| A10 | An auto schedule raises zone 1's target 22 → 23 at 13:00; room 22.1 | heating immediately (no wait), subject to min OFF; zone 1 = calling zone if it starts the HP |
| A11 | The auto schedule ends; target back to 22; room 22.5 | the zone stops |
| A12 | A new auto schedule overlaps an existing one for the same zone | rejected with an error; nothing stored |
| A13 | Manual schedule zone 2, 04:00–06:00; all zones satisfied | zone 2 forced, HP ON; no calling zone; HP OFF at 06:00 (min ON satisfied) |
| A14 | As A13; zone 3 drops to its start temperature at 05:00 | zone 3 joins and becomes the calling zone; the sync rule fires when zone 3 reaches its target |
| A15 | A forced zone reaches 25 °C | valve closes, no demand; resumes below 24 °C within the window |
| A16 | Holiday active until Sunday 15:00 | every effective target 18 °C; schedules ignored; at 15:00 the base targets apply and zones below their start temperature start immediately |
| A17 | Zone 4's sensor silent for 60 min | sensor fault; valve opens only when the HP runs; no demand; notification; 08:00 reminder next day |
| A18 | The calling zone's sensor fails mid-run | counts as having reached its target → the sync rule fires; the zone goes into sensor fault |
| A19 | Every sensor silent for 24 h, HA running, heating season ON | failsafe: every valve open + HP ON 10:00–15:00 daily; notification; ends with the first valid reading |
| A20 | Heating season OFF (also switched OFF 20 min into a run) | HP OFF and valves closed immediately, min ON ignored; no demand; no failsafe heating; valve exercise on Monday 08:00; sensor fault notified without daily reminder |
| A21 | Shadow mode | decisions and entities update with the commanded states as feedback; no switch commands; heartbeat still sent; Control active ON → OFF sends one final HP OFF + valves OFF, then nothing |
| A22 | HA restart while waiting (10 min left) and HP ON for 20 min | after the restart the wait continues with ~10 min left; min ON counts from the original start |
| A23 | A zone without a valve drops to its start temperature | behaves like A1 (can start the HP); no output command |
| A24 | Schedule window 22:00–02:00 across a DST change | correct local start and end times |
| A25 | A valve's actual state differs from the desired one (switched in a device app) | corrected within one reconcile interval |
| A26 | HP off; zones 1 and 2's wait times end in the same step; zone 1 at start − 0.1, zone 2 at start − 0.3 | both heating; zone 2 = calling zone (largest deficit); equal deficits → first in YAML order |
| A27 | A valve switch stays unavailable (or ignores commands) for 3 reconcile intervals | "switch not following command" notified once; retries with backoff; recovery notified |
| A28 | Manual schedule for zone 2, but zone 2 is in sensor fault | zone 2 stays in sensor fault (follows the house, no demand); no HP start because of it |
| A29 | The heat source switch becomes unavailable while ON | counts as HP OFF: min OFF starts, no zone joins, mismatch alert as A27. Back ON: it never stopped (min ON and the run continue); back OFF: OFF since it became unavailable |
| A30 | First start, nothing stored | outputs read back; no min OFF; a zone at its start temperature waits and starts the HP after the wait time |

**Shelly scripts** (simulated time in `tests/shelly/`; on real devices with shortened timeouts as in [Shelly scripts → Bench tests](../shelly-scripts.md#bench-tests-shortened-timeouts)):

| # | Scenario | Expected |
|---|---|---|
| S1 | Valve Shelly: heartbeat stops | every channel ON after the heartbeat timeout |
| S2 | Heat source Shelly: heartbeat stops | OFF after the heartbeat timeout; failsafe operation window after the failsafe trigger (season ON) |
| S3 | Heat source Shelly: reboot, no heartbeat, no valid time | OFF until the failsafe trigger after boot; then the uptime cycle starting with 5 h ON, then 19 h OFF (season ON) |
| S4 | Heartbeat returns | the scripts stop acting; the reconcile loop sets the outputs |
| S5 | Last heartbeat said season OFF | the heat source Shelly never heats in the failsafe |
| S6 | Heartbeat call to a running script | answered with the status (running, state, season flag, parameters); HA alerts if the parameters differ from its expected config |
| S7 | Script stopped / device offline for 3 calls | HA alerts once; recovery when calls succeed again |
