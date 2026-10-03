# Stored data

One `helpers.storage.Store` file: `.storage/multizone_floor_heating_manager` (Store version 1). It is written at most once per 30 s (delayed and coalesced) and flushed when HA stops. Removing the config entry keeps it, so the YAML imported again finds its settings.

| Key | Contents |
|---|---|
| `core` | `CoreState.to_dict()`: the logic state (see [Control core](control-core.md#state-corestatepy)). |
| `settings` | The UI values: `zones` (per zone id: hysteresis, wait time, holiday temperature, base target), `global` (the global parameters, times of day as `HH:MM:SS`, the valve exercise weekday), `heating_season`, `shadow_mode`, `schedules` and `schedule_counter`, `holiday_on`, `holiday_end_date` (ISO date or null), `holiday_end_time`. |
| `pending_off` | Switches that still owe the final OFF after Shadow mode was switched ON. |
| `heartbeat` | Per Shelly: failed calls in a row and the alerts sent. |

## Format rules

- Everything is keyed by the stable zone id, never by entity id or name. A zone no longer in the YAML is dropped (with a warning); a new zone starts IDLE with default settings; a calling zone that no longer exists is cleared. Schedules lose removed zones; a schedule left without zones is deleted.
- Datetimes are ISO 8601 in UTC, dates ISO 8601.
- **Adding a field needs no version bump:** a missing field gets its default when loading.
- **A breaking change** bumps `SCHEMA_VERSION` in `core/state.py` and adds a migration in `from_dict`.
- Data from a newer version (after a downgrade) or corrupt data is discarded with a warning, and the core starts as on a first start (no minimum off time applied). Unusable settings fall back to their defaults with a warning; settings without a usable `shadow_mode` start in shadow mode.
- Older stored settings are migrated when loading (e.g. a single global holiday temperature becomes every zone's value; a combined holiday end date-time becomes a local date and time).

## Defaults on a first install

Heating season ON, Shadow mode ON, every parameter at its default from `PARAM_SPECS` (`core/config.py`).
