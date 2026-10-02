# CLAUDE.md — guidelines for AI agents

Multizone Floor Heating Manager is a Home Assistant custom integration (domain `multizone_floor_heating_manager`) that controls underfloor heating zone by zone, plus two Shelly watchdog scripts that keep the house safe when Home Assistant stops.

## Critical context

**This code controls the heating of real homes.** A mistake can mean a cold house, a heat pump that short-cycles, high energy bills, or damaged equipment. Correctness comes first. When something is unclear, ask instead of guessing.

## Setup and checks

Python 3.14 (Home Assistant 2026.9 needs ≥ 3.14.2), managed with `uv`; Node.js ≥ 24 for the Shelly script tests.

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv -r requirements_test.txt
pre-commit install          # gitleaks + ruff on every commit
npm ci
```

Run **all** of these before every commit; CI runs the same:

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest --cov        # fails below 95 % branch coverage of core/
npm test                      # Shelly scripts: simulated time + language subset check
```

## Architecture (fixed)

Details: [`docs/development/`](docs/development/index.md).

1. **Control core** — `custom_components/multizone_floor_heating_manager/core/`
   - Pure Python, **no Home Assistant imports**. One deterministic step function: `step(config, state, inputs, now) -> (outputs, new_state, events)`.
   - **Never reads the clock**: `now` (aware datetime) and the time zone come in through the inputs.
   - Both rules are enforced by `tests/core/test_core_purity.py`; mypy is strict for the core.
2. **Thin HA adapter** — the rest of the package: YAML config, input collection, entities, services, notifications, persistence. Async only; never block the event loop.
3. **Reconcile loop** — every reconcile interval and on every state change: call `step`, compare desired and actual outputs, correct differences with backoff. Idempotent. No commands in shadow mode.
4. **Shelly watchdog scripts** — `shelly_scripts/`, JavaScript that runs on the devices.
   - Only the Shelly engine's JavaScript subset: `let`/`var`, named top-level functions declared before use; no arrow functions, `const`, classes, template literals, destructuring or promises. `tests/shelly/subset.test.mjs` enforces it.
   - The tests load the same files that go on the device into a mock Shelly runtime with simulated time.

## Rules

- **No secrets in the repository:** no passwords, tokens, ping URLs, IP addresses or host names of a private network, email addresses or personal names. Examples use placeholders and documentation addresses (`192.0.2.x`). gitleaks runs in pre-commit and CI.
- **Generic:** any number of zones (≥ 1), any zone may have no valve, every entity is user-mapped, the heat source is just a switch. No entity id, device name or brand in the logic. Every parameter has a default and can be changed.
- **Units:** the core computes in °C; the adapter converts to and from HA's unit system.
- **English** everywhere: code, comments, docs, entity names.
- **Test first for the core.** Every behaviour rule has a unit test with simulated time; the acceptance scenarios in [`docs/development/testing.md`](docs/development/testing.md) are the minimum set.
- **Docs with code:** a change that affects behaviour, settings, entities or services updates the user manual ([`docs/`](docs/index.md)) and the developer docs ([`docs/development/`](docs/development/index.md)) in the same change, and adds a line to [`CHANGELOG.md`](CHANGELOG.md).
- **Docs describe the current state.** No history in the docs or code comments ("since 0.8", "was changed because…"); that belongs in the changelog and the git history.
- **Stable HA APIs only;** watch for deprecation warnings (the weekly CI job runs the tests against the newest Home Assistant).
- **Hardware:** anything that needs real devices is marked for a human to verify.
