// Valve watchdog script (shelly_scripts/valve_watchdog.js), simulated time.
// docs/design.md §3.6 case 2, §5.4, D-100…D-104; docs/heartbeat-protocol.md.

import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  CHECK_S,
  HOUR,
  MINUTE,
  TIMEOUT_S,
  VALVE_SCRIPT,
  heartbeat,
  newDevice,
  status,
} from "./helpers.mjs";
import { Device } from "./shelly_mock.mjs";

function valve(options) {
  return newDevice(VALVE_SCRIPT, options);
}

describe("valve watchdog: timeout (S1)", () => {
  test("the file carries the §4 defaults", () => {
    assert.deepEqual(status(valve()).params, {
      heartbeat_timeout_s: 5 * HOUR,
      check_interval_s: 60,
      switch_ids: null,
    });
  });

  test("nothing happens before HeartbeatTimeout", () => {
    const d = valve();
    d.advanceSeconds(TIMEOUT_S - CHECK_S);
    assert.deepEqual(d.outputs, [false, false]);
    assert.equal(d.calls("Switch.Set").length, 0);
    assert.equal(status(d).state, "normal");
  });

  test("all channels ON after HeartbeatTimeout without a heartbeat", () => {
    const d = valve();
    d.advanceSeconds(TIMEOUT_S);
    assert.deepEqual(d.outputs, [true, true]);
    assert.equal(status(d).state, "timed_out");
    assert.ok(d.logs.some((line) => line.includes("opening all valves")));
  });

  test("the timeout counts from the last heartbeat", () => {
    const d = valve();
    d.advanceSeconds(3 * HOUR);
    heartbeat(d);
    d.advanceSeconds(TIMEOUT_S - CHECK_S);
    assert.deepEqual(d.outputs, [false, false]);
    d.advanceSeconds(CHECK_S);
    assert.deepEqual(d.outputs, [true, true]);
  });

  test("regular heartbeats keep the watchdog quiet", () => {
    const d = valve();
    for (let i = 0; i < 24 * 12; i += 1) {
      d.advanceSeconds(5 * MINUTE);
      assert.equal(heartbeat(d).code, 200);
    }
    assert.equal(d.calls("Switch.Set").length, 0);
    assert.equal(status(d).state, "normal");
  });

  test("channels already ON get no command", () => {
    const d = valve();
    d.setOutput(0, true);
    d.setOutput(1, true);
    d.advanceSeconds(TIMEOUT_S + HOUR);
    assert.equal(d.calls("Switch.Set").length, 0);
    assert.equal(status(d).state, "timed_out");
  });

  test("only differing channels are switched", () => {
    const d = valve();
    d.setOutput(1, true);
    d.advanceSeconds(TIMEOUT_S);
    assert.deepEqual(
      d.calls("Switch.Set").map((c) => c.params),
      [{ id: 0, on: true }],
    );
  });
});

describe("valve watchdog: timed out (D-103)", () => {
  test("keeps the valves open: an external OFF is re-asserted at the next check", () => {
    const d = valve();
    d.advanceSeconds(TIMEOUT_S);
    d.setOutput(0, false); // HA or the app switches a valve OFF
    d.advanceSeconds(CHECK_S - 1);
    assert.equal(d.outputs[0], false);
    d.advanceSeconds(1);
    assert.deepEqual(d.outputs, [true, true]);
    assert.equal(d.calls("Switch.Set").length, 3);
  });

  test("a failed command is retried at the next check", () => {
    const d = valve();
    d.failNext("Switch.Set");
    d.advanceSeconds(TIMEOUT_S);
    assert.deepEqual(d.outputs, [false, true]);
    assert.ok(d.logs.some((line) => line.includes("failed")));
    d.advanceSeconds(CHECK_S);
    assert.deepEqual(d.outputs, [true, true]);
  });

  test("more channels than RPC slots are opened over successive checks", () => {
    const d = valve({ switches: 6 });
    d.advanceSeconds(TIMEOUT_S);
    assert.ok(d.outputs.some((on) => on));
    d.advanceSeconds(CHECK_S);
    assert.deepEqual(d.outputs, [true, true, true, true, true, true]);
  });
});

describe("valve watchdog: heartbeat returns (S4)", () => {
  test("the script stops acting and switches nothing", () => {
    const d = valve();
    d.advanceSeconds(TIMEOUT_S);
    const sent = d.calls("Switch.Set").length;
    const response = heartbeat(d);
    assert.equal(response.code, 200);
    assert.equal(response.json().state, "normal");
    d.advanceSeconds(0);
    assert.deepEqual(d.outputs, [true, true]); // unchanged: HA's reconcile sets them
    d.setOutput(0, false); // HA's reconcile closes a valve
    d.advanceSeconds(HOUR);
    assert.deepEqual(d.outputs, [false, true]);
    assert.equal(d.calls("Switch.Set").length, sent);
    assert.ok(d.logs.some((line) => line.includes("heartbeat is back")));
  });

  test("the watchdog is armed again after the heartbeat returns", () => {
    const d = valve();
    d.advanceSeconds(TIMEOUT_S);
    d.advanceSeconds(10 * MINUTE);
    heartbeat(d);
    d.setOutput(0, false);
    d.setOutput(1, false);
    d.advanceSeconds(TIMEOUT_S - CHECK_S);
    assert.deepEqual(d.outputs, [false, false]);
    d.advanceSeconds(CHECK_S);
    assert.deepEqual(d.outputs, [true, true]);
  });
});

describe("valve watchdog: status (S6)", () => {
  test("a heartbeat is answered with the full status", () => {
    const d = valve();
    d.setOutput(1, true);
    d.advanceSeconds(90);
    const response = heartbeat(d);
    assert.equal(response.code, 200);
    assert.deepEqual(response.headers, [["Content-Type", "application/json"]]);
    assert.deepEqual(response.json(), {
      v: 1,
      role: "valve",
      script_version: "1.0.0",
      running: true,
      state: "normal",
      heartbeat_seen: true,
      heartbeat_age_s: 0,
      uptime_s: 90,
      switches: [
        { id: 0, output: false },
        { id: 1, output: true },
      ],
      params: { heartbeat_timeout_s: 18000, check_interval_s: 60, switch_ids: null },
    });
  });

  test("GET returns the status without counting as a heartbeat", () => {
    const d = valve();
    d.advanceSeconds(2 * HOUR + 30);
    const s = status(d);
    assert.equal(s.heartbeat_seen, false);
    assert.equal(s.heartbeat_age_s, 2 * HOUR + 30);
    assert.equal(s.uptime_s, 2 * HOUR + 30);
    for (let i = 0; i < 5; i += 1) {
      d.advanceSeconds(HOUR);
      status(d);
    }
    assert.deepEqual(d.outputs, [true, true]);
  });

  test("heartbeat age counts from the last heartbeat", () => {
    const d = valve();
    d.advanceSeconds(HOUR);
    heartbeat(d);
    d.advanceSeconds(125);
    const s = status(d);
    assert.equal(s.heartbeat_seen, true);
    assert.equal(s.heartbeat_age_s, 125);
  });

  test("an empty body and unknown fields are accepted", () => {
    const d = valve();
    assert.equal(d.request("heartbeat", "POST").code, 200);
    assert.equal(d.request("heartbeat", "POST", { body: "" }).code, 200);
    assert.equal(heartbeat(d, { v: 1, sent_by: "test", season: true }).code, 200);
  });

  test("an invalid body is rejected and does not count as a heartbeat", () => {
    const d = valve();
    d.advanceSeconds(TIMEOUT_S - 10 * MINUTE);
    for (const body of ["{", "[1]", "42", "null", '"x"']) {
      const response = d.request("heartbeat", "POST", { body });
      assert.equal(response.code, 400, body);
      assert.ok(response.json().error);
    }
    d.advanceSeconds(10 * MINUTE);
    assert.deepEqual(d.outputs, [true, true]);
  });

  test("other methods get 405", () => {
    const d = valve();
    const response = d.request("heartbeat", "PUT", { body: "{}" });
    assert.equal(response.code, 405);
    assert.deepEqual(response.headers, [
      ["Content-Type", "application/json"],
      ["Allow", "GET, POST"],
    ]);
  });

  test("uses one timer and one endpoint", () => {
    const d = valve();
    assert.equal(d.timers.size, 1);
    assert.deepEqual([...d.endpoints.keys()], ["heartbeat"]);
  });
});

describe("valve watchdog: channels (D-104)", () => {
  test("default: every switch component of the device", () => {
    for (const n of [1, 2, 4]) {
      const d = valve({ switches: n });
      assert.deepEqual(
        status(d).switches.map((s) => s.id),
        [...Array(n).keys()],
      );
      d.advanceSeconds(TIMEOUT_S);
      assert.ok(d.outputs.every((on) => on));
    }
  });

  test("a configured list limits the channels", () => {
    const d = valve({ config: { switch_ids: [1] } });
    assert.deepEqual(status(d).params.switch_ids, [1]);
    d.advanceSeconds(TIMEOUT_S + HOUR);
    assert.deepEqual(d.outputs, [false, true]);
  });

  test("a configured channel that does not exist is reported and skipped", () => {
    const d = valve({ config: { switch_ids: [0, 5] } });
    d.advanceSeconds(TIMEOUT_S + HOUR);
    assert.deepEqual(d.outputs, [true, false]);
    assert.deepEqual(status(d).switches, [
      { id: 0, output: true },
      { id: 5, output: null },
    ]);
    assert.ok(d.calls("Switch.Set").every((c) => c.params.id === 0));
  });
});

describe("valve watchdog: start (D-102)", () => {
  test("after a reboot the valves stay at the power-on default until the timeout", () => {
    const d = valve();
    d.advanceSeconds(TIMEOUT_S + HOUR);
    assert.deepEqual(d.outputs, [true, true]);
    d.reboot();
    d.advance(0);
    assert.deepEqual(d.outputs, [false, false]);
    const s = status(d);
    assert.equal(s.state, "normal");
    assert.equal(s.heartbeat_seen, false);
    assert.equal(s.uptime_s, 0);
    d.advanceSeconds(TIMEOUT_S - CHECK_S);
    assert.deepEqual(d.outputs, [false, false]);
    assert.equal(d.calls("Switch.Set").length, 0);
    d.advanceSeconds(CHECK_S);
    assert.deepEqual(d.outputs, [true, true]);
  });

  test("a script restart counts as a heartbeat and leaves the outputs alone", () => {
    const d = valve();
    d.setOutput(0, true);
    d.advanceSeconds(4 * HOUR);
    d.restartScripts();
    d.advance(0);
    assert.deepEqual(d.outputs, [true, false]);
    assert.equal(status(d).heartbeat_age_s, 0);
    d.advanceSeconds(TIMEOUT_S - CHECK_S);
    assert.deepEqual(d.outputs, [true, false]);
    d.advanceSeconds(CHECK_S);
    assert.deepEqual(d.outputs, [true, true]);
  });
});

describe("valve watchdog: configuration", () => {
  test("shortened timeouts for the bench", () => {
    const d = valve({ config: { heartbeat_timeout_s: 120, check_interval_s: 5 } });
    assert.deepEqual(status(d).params, {
      heartbeat_timeout_s: 120,
      check_interval_s: 5,
      switch_ids: null,
    });
    d.advanceSeconds(115);
    assert.deepEqual(d.outputs, [false, false]);
    d.advanceSeconds(5);
    assert.deepEqual(d.outputs, [true, true]);
    heartbeat(d);
    assert.equal(status(d).state, "normal");
  });

  test("an invalid configuration stops the script at start", () => {
    const bad = [
      { heartbeat_timeout_s: 0 },
      { heartbeat_timeout_s: "5h" },
      { check_interval_s: 0 },
      { check_interval_s: 20000 },
      { switch_ids: "all" },
      { switch_ids: [0, "1"] },
      { endpoint: "" },
    ];
    for (const config of bad) {
      const d = new Device();
      assert.throws(() => d.loadScript(VALVE_SCRIPT, config), /CONFIG/, JSON.stringify(config));
    }
  });
});
