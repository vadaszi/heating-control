# CLAUDE.md — Multizone Floor Heating Manager

Home Assistant custom integration **Multizone Floor Heating Manager** (domain `multizone_floor_heating_manager`, folder `custom_components/multizone_floor_heating_manager`; working name until P7b: `floorheat`) that controls underfloor heating zone by zone.

- **Spec (ground truth):** [`docs/design.md`](docs/design.md) — read all of it before writing code.
- **Work plan:** [`docs/implementation-plan.md`](docs/implementation-plan.md) — work phases P0–P12.

If the spec and anything else (this file, the plan, the code) disagree, the spec wins.

---

## ⚠️ Mandatory rules (spec §0) — override everything else

### No secrets in the repository (§0.1, D-49)
- Never commit a secret: passwords, API keys, tokens (HA long-lived tokens, healthchecks.io ping URLs/UUIDs, SMTP credentials, Shelly passwords), Wi-Fi credentials, IP addresses or hostnames of the private network, email addresses, personal names.
- Secrets live only in HA (`secrets.yaml`, integration options) or on the devices. The repo holds only placeholders and examples (`examples/secrets.example.yaml`).
- `.gitignore` covers secret files. GitHub secret scanning + push protection are on; gitleaks runs in pre-commit and CI.
- The cloud development environment gets **no** credentials to the home system.
- If a secret is ever committed: rotate it immediately. Deleting the commit is not enough.
- **License:** MIT (D-63). The copyright holder is named only in `LICENSE`. Everywhere else refer to "the copyright holder in `LICENSE`"; never repeat the name. The owner's GitHub handle is allowed where tooling needs it (`manifest.json` codeowners, repo URLs).

### Generic, publishable design (§0.2, D-50)
- Number of zones is configurable (≥ 1); any zone may have no valve.
- All entities are user-mapped (`sensor` with temperature device class, `switch`). No entity ID, device name or brand in the code.
- The heat source is just a switch; nothing Mitsubishi-specific.
- Every parameter has a default and is user-changeable (§4).
- Shelly scripts are generic: role, timeouts and failsafe window live in a config block at the top or in device storage.
- English everywhere (code, comments, docs, entity names).
- Units (D-77): the core computes in °C; the adapter converts to and from HA's unit system.
- Hydraulics (D-80): the logic assumes a flow path whenever the heat pump request is ON; documented prerequisite, startup warning if every zone has a valve.
- The values in spec §2 and the defaults in §4 describe the reference installation, not constants.

---

## Architecture (spec §5, fixed)

1. **Control core** — `custom_components/multizone_floor_heating_manager/core/`
   - Pure Python, **no Home Assistant imports**.
   - One deterministic step function: `step(config, state, inputs, now) -> (desired_outputs, new_state, events)`.
   - **Never reads the clock**; `now` (aware datetime) and the time zone are passed in.
   - Both rules are enforced by `tests/core/test_core_purity.py`. mypy is strict for the core.
2. **Thin HA adapter** — the rest of `custom_components/multizone_floor_heating_manager/`: YAML config, input collection (sensor `last_reported`, unavailable switch = OFF), entities, services, notifications, persistence via `helpers.storage.Store`. Async only; never block the event loop.
3. **Reconcile loop** — every `ReconcileInterval` (60 s) and on sensor updates: call `step`, compare desired vs actual outputs, correct differences with backoff. Idempotent. No commands in shadow mode (§5.5).
4. **Shelly watchdog scripts** — `shelly_scripts/` (JavaScript on the devices): heartbeat endpoint, failsafe on heartbeat loss (§3.6, §5.4). Protocol: `docs/heartbeat-protocol.md`; user guide: `docs/shelly-scripts.md`.
   - Only the Shelly engine's JavaScript subset: `let`/`var`, named top-level functions declared before use, no arrow functions, `const`, classes, template literals, destructuring, promises. `tests/shelly/subset.test.mjs` enforces it (acorn AST check).
   - Tests load the same files that go on the device into a mock Shelly runtime with simulated time (`tests/shelly/shelly_mock.mjs`).

Repository layout: spec §5.9.

---

## Working rules

- **Follow the spec exactly.** If something is ambiguous, contradictory or missing: stop and ask the owner. Do not guess.
- **Write decisions back:** every decision made in a session goes into `docs/design.md` (§3–§5 and the decision log §7) in the same change.
- **One work phase at a time** (`docs/implementation-plan.md`), committed **directly to `main`** (D-83): no branches, no pull requests. `main` must stay green: run ruff, mypy, pytest and `npm test` (pre-commit runs on commit) before every push, check CI after pushing, and fix a red run immediately. At the end of a phase, give the owner a summary and stop. Do not start the next phase unasked.
- **Test first for the core.** Every §3 rule is covered by unit tests with simulated time; the §6 acceptance scenarios are the minimum set (`test_a01_...`). Core branch coverage ≥ 95 % is enforced.
- **Docs with code:** user docs (§5.8) are updated in the same commit(s) as the code they describe.
- **Every phase summary** lists the spec questions that came up.
- **Hardware:** anything that needs real devices is marked for the owner to verify (spec §8). Never ask for or store credentials to the home network.
- **HA APIs:** use only long-standing, stable APIs; watch for deprecation warnings.

---

## Development setup

- Python **3.14** (HA 2026.9.x needs ≥ 3.14.2), managed with `uv`. Minimum supported HA: **2026.9.0** (`hacs.json`). Tests run against the HA version pinned through `pytest-homeassistant-custom-component` in `requirements_test.txt`.
- Commits use the owner's GitHub noreply address, never a real email.

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv -r requirements_test.txt
pre-commit install                  # gitleaks + ruff on every commit

.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest --cov              # fails below 95 % branch coverage of core/

npm ci && npm test                  # Shelly script tests (Node >= 24, node:test)
```

CI (`.github/workflows/ci.yml`): ruff, mypy, pytest + coverage, Shelly script tests (`npm test`), gitleaks (full history), hassfest, HACS validation.
