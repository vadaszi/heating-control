# Developer documentation

How Multizone Floor Heating Manager is built, for anyone who changes it. The user manual ([`docs/`](../index.md)) says *what* it does; these pages say *how* and *why it is built this way*.

- [Architecture](architecture.md): the four parts, the modules, the rules that hold everywhere.
- [Control core](control-core.md): the step function, its inputs, outputs, state and events.
- [Reconcile loop and outputs](reconcile-loop.md): how decisions become switch commands, shadow mode, settings.
- [Heartbeat and watchdogs](heartbeat.md): the Shelly heartbeat client, the scripts, the external watchdog ping.
- [Stored data](storage.md): what is persisted, its format and how it evolves.
- [Testing](testing.md): test layout, simulated time, the acceptance scenarios, the Shelly mock.

Setup, checks and the rules for pull requests: [CONTRIBUTING.md](../../CONTRIBUTING.md).
