# Contributing

Thanks for helping. Bug reports, ideas and pull requests are welcome.

## Issues

- **Bug reports:** use the bug report form. Include the Home Assistant version, the integration version, your YAML (without passwords, the ping URL or addresses), the log lines (see [Troubleshooting → Where to look](docs/troubleshooting.md#where-to-look)) and what happened compared with what you expected.
- **Ideas:** describe the problem first, then your idea. The integration controls real heating systems, so a change in behaviour needs a good reason and a clear rule for every case.

## Pull requests

1. **For anything larger than a small fix, open an issue first** and agree on the approach. That saves you writing code that does not fit.
2. Fork the repository and branch from `main`.
3. Make the change **with tests and docs in the same pull request**:
   - tests for every changed rule (the core is tested with simulated time, see [Testing](docs/development/testing.md));
   - the **user manual** (`docs/`) for anything a user sees: behaviour, settings, entities, services, notifications;
   - the **developer docs** (`docs/development/`) for changes to the architecture, the control core, the stored data or the protocol;
   - a line in [`CHANGELOG.md`](CHANGELOG.md), including every added, renamed or removed entity.

   A pull request without the matching docs is not merged.
4. Run every check (below) and open the pull request. The template has a short checklist.

## Development setup

You need Python 3.14 (with [uv](https://docs.astral.sh/uv/)) and Node.js 24 or newer.

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv -r requirements_test.txt
pre-commit install          # gitleaks + ruff before every commit
npm ci
```

## Checks

Run all of them before you push; CI runs the same and must be green:

```bash
.venv/bin/ruff check . && .venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest --cov        # fails below 95 % branch coverage of the control core
npm test                      # Shelly scripts
```

## Rules

- **The control core** (`custom_components/multizone_floor_heating_manager/core/`) has no Home Assistant imports and never reads the clock; `tests/core/test_core_purity.py` checks both. Start with a failing test.
- **The Shelly scripts** use only the device's JavaScript subset (no arrow functions, `const`, classes, template literals, destructuring or promises); `tests/shelly/subset.test.mjs` checks it.
- **No secrets** in any file: no passwords, tokens, ping URLs, private IP addresses or host names, email addresses or personal names. Use placeholders and documentation addresses (`192.0.2.x`). gitleaks runs in pre-commit and CI.
- **Generic:** nothing specific to one house, brand or entity id in the logic; every parameter has a default.
- **English** in code, comments and docs. The docs describe the current state; history belongs in the changelog.
- Only long-standing Home Assistant APIs; no deprecation warnings.

The architecture and the reasons behind it: [developer documentation](docs/development/index.md).

## License

By contributing you agree that your contribution is licensed under the [MIT license](LICENSE).
