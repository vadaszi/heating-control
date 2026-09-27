// Heat source watchdog script (shelly_scripts/heat_source_watchdog.js), simulated time.
// docs/design.md §3.6 case 2, §5.4, D-72, D-100…D-105; docs/heartbeat-protocol.md.
// The failsafe window and uptime cycle (S2 second half, S3, S5) come in P12.

import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  CHECK_S,
  HEAT_SOURCE_SCRIPT,
  HOUR,
  MINUTE,
  TIMEOUT_S,
  heartbeat,
  newDevice,
  status,
} from "./helpers.mjs";
import { Device, ERR_NOT_FOUND } from "./shelly_mock.mjs";

const KEY = "floorheat_season";

function heatSource(options = {}) {
  return newDevice(HEAT_SOURCE_SCRIPT, { switches: 1, ...options });
}

function beat(device, season = true) {
  return heartbeat(device, { v: 1, season });
}

describe("heat source watchdog: timeout", () => {
  test("the file carries the §4 defaults", () => {
    assert.deepEqual(status(heatSource()).params, {
      heartbeat_timeout_s: 5 * HOUR,
      check_interval_s: 60,
      switch_id: 0,
    });
  });

  test("OFF after HeartbeatTimeout without a heartbeat", () => {
    const d = heatSource();
    beat(d);
    d.setOutput(0, true); // HA requests heat
    d.advanceSeconds(TIMEOUT_S - CHECK_S);
    assert.equal(d.outputs[0], true);
    assert.equal(d.calls("Switch.Set").length, 0);
    d.advanceSeconds(CHECK_S);
    assert.equal(d.outputs[0], false);
    assert.equal(status(d).state, "timed_out");
    assert.ok(d.logs.some((line) => line.includes("switching the heat source OFF")));
  });

  test("regular heartbeats keep the output as HA set it", () => {
    const d = heatSource();
    d.setOutput(0, true);
    for (let i = 0; i < 24 * 12; i += 1) {
      d.advanceSeconds(5 * MINUTE);
      assert.equal(beat(d).code, 200);
    }
    assert.equal(d.outputs[0], true);
    assert.equal(d.calls("Switch.Set").length, 0);
  });

  test("an output already OFF gets no command", () => {
    const d = heatSource();
    d.advanceSeconds(TIMEOUT_S + HOUR);
    assert.equal(status(d).state, "timed_out");
    assert.equal(d.calls("Switch.Set").length, 0);
  });

  test("stays OFF: an external ON is re-asserted at the next check (D-103)", () => {
    const d = heatSource();
    d.advanceSeconds(TIMEOUT_S);
    d.setOutput(0, true);
    d.advanceSeconds(CHECK_S);
    assert.equal(d.outputs[0], false);
    assert.deepEqual(
      d.calls("Switch.Set").map((c) => c.params),
      [{ id: 0, on: false }],
    );
  });

  test("a failed command is retried at the next check", () => {
    const d = heatSource();
    d.setOutput(0, true);
    d.failNext("Switch.Set");
    d.advanceSeconds(TIMEOUT_S);
    assert.equal(d.outputs[0], true);
    d.advanceSeconds(CHECK_S);
    assert.equal(d.outputs[0], false);
  });
});

describe("heat source watchdog: heartbeat returns (S4)", () => {
  test("the script stops acting and switches nothing", () => {
    const d = heatSource();
    d.setOutput(0, true);
    d.advanceSeconds(TIMEOUT_S);
    assert.equal(d.outputs[0], false);
    const response = beat(d);
    assert.equal(response.json().state, "normal");
    d.advanceSeconds(HOUR);
    assert.equal(d.outputs[0], false); // stays OFF until HA's reconcile switches it
    d.setOutput(0, true);
    d.advanceSeconds(HOUR);
    assert.equal(d.outputs[0], true);
    assert.equal(d.calls("Switch.Set").length, 1);
  });
});

describe("heat source watchdog: season flag", () => {
  test("stored in KVS when it changes, not on every heartbeat", () => {
    const d = heatSource();
    assert.equal(status(d).season, null);
    beat(d, true);
    d.advance(0);
    assert.equal(d.kvsValue(KEY), true);
    for (let i = 0; i < 10; i += 1) {
      d.advanceSeconds(5 * MINUTE);
      beat(d, true);
    }
    assert.equal(d.calls("KVS.Set").length, 1);
    beat(d, false);
    d.advance(0);
    assert.equal(d.kvsValue(KEY), false);
    assert.equal(status(d).season, false);
    assert.equal(d.calls("KVS.Set").length, 2);
  });

  test("the heartbeat response already carries the new flag", () => {
    const d = heatSource();
    assert.equal(beat(d, true).json().season, true);
    assert.equal(beat(d, false).json().season, false);
  });

  test("never set: reported as null (treated as OFF, D-105)", () => {
    const d = heatSource();
    d.advanceSeconds(HOUR);
    assert.equal(status(d).season, null);
    assert.equal(d.kvsValue(KEY), undefined);
  });

  test("survives a reboot", () => {
    const d = heatSource();
    beat(d, true);
    d.advanceSeconds(HOUR);
    d.reboot();
    d.advance(0);
    assert.equal(status(d).season, true);
    assert.deepEqual(
      d.calls("KVS.Get").map((c) => c.params),
      [{ key: KEY }],
    );
    beat(d, true);
    d.advance(0);
    assert.equal(d.calls("KVS.Set").length, 0); // same value: no write after reboot
  });

  test("a heartbeat during the boot-time KVS read wins over the stored value", () => {
    const d = heatSource();
    beat(d, true);
    d.advance(0);
    d.setRpcDelay(500);
    d.reboot();
    assert.equal(beat(d, false).json().season, false);
    d.advance(1000);
    assert.equal(status(d).season, false);
    assert.equal(d.kvsValue(KEY), false);
  });

  test("a heartbeat during the read with the stored value causes no write", () => {
    const d = heatSource();
    beat(d, true);
    d.advance(0);
    d.setRpcDelay(500);
    d.reboot();
    beat(d, true);
    d.advance(1000);
    assert.equal(d.calls("KVS.Set").length, 0);
    assert.equal(status(d).season, true);
  });

  test("a failed write is retried with the next heartbeat", () => {
    const d = heatSource();
    d.failNext("KVS.Set");
    beat(d, true);
    d.advance(0);
    assert.equal(d.kvsValue(KEY), undefined);
    assert.ok(d.logs.some((line) => line.includes("storing the season flag failed")));
    assert.equal(status(d).season, true);
    d.advanceSeconds(5 * MINUTE);
    beat(d, true);
    d.advance(0);
    assert.equal(d.kvsValue(KEY), true);
  });

  test("a change during a pending write is written afterwards", () => {
    const d = heatSource();
    d.setRpcDelay(500);
    beat(d, true);
    beat(d, false);
    d.advance(2000);
    assert.equal(d.kvsValue(KEY), false);
    assert.deepEqual(
      d.calls("KVS.Set").map((c) => c.params.value),
      [true, false],
    );
  });

  test("a failed read leaves the flag unknown until a heartbeat sets it", () => {
    const d = new Device({ switches: 1 });
    d.failNext("KVS.Get", -1, "busy");
    d.loadScript(HEAT_SOURCE_SCRIPT);
    d.advance(0);
    assert.equal(status(d).season, null);
    beat(d, false);
    d.advance(0);
    assert.equal(d.kvsValue(KEY), false);
  });

  test("an invalid stored value is ignored", () => {
    const d = new Device({ switches: 1 });
    d.kvs.set(KEY, JSON.stringify("on"));
    d.loadScript(HEAT_SOURCE_SCRIPT);
    d.advance(0);
    assert.equal(status(d).season, null);
    beat(d, true);
    d.advance(0);
    assert.equal(d.kvsValue(KEY), true);
  });

  test("a missing key is the normal first-start case", () => {
    const d = new Device({ switches: 1 });
    d.failNext("KVS.Get", ERR_NOT_FOUND, "not found");
    d.loadScript(HEAT_SOURCE_SCRIPT);
    d.advance(0);
    assert.equal(status(d).season, null);
    assert.ok(!d.logs.some((line) => line.includes("failed")));
  });
});

describe("heat source watchdog: requests", () => {
  test("a heartbeat without a valid season flag is rejected and does not count", () => {
    const d = heatSource();
    d.setOutput(0, true);
    d.advanceSeconds(TIMEOUT_S - 10 * MINUTE);
    const bodies = [
      undefined,
      "",
      "{",
      "[]",
      JSON.stringify({ v: 1 }),
      JSON.stringify({ v: 1, season: "on" }),
      JSON.stringify({ v: 1, season: 1 }),
      JSON.stringify({ v: 1, season: null }),
    ];
    for (const body of bodies) {
      const response = d.request("heartbeat", "POST", { body });
      assert.equal(response.code, 400, String(body));
      assert.ok(response.json().error);
    }
    assert.equal(status(d).season, null);
    d.advanceSeconds(10 * MINUTE);
    assert.equal(d.outputs[0], false);
  });

  test("unknown fields are ignored", () => {
    const d = heatSource();
    assert.equal(heartbeat(d, { v: 1, season: true, extra: [1, 2] }).code, 200);
  });

  test("status content (S6)", () => {
    const d = heatSource();
    d.setOutput(0, true);
    d.advanceSeconds(300);
    beat(d, true);
    d.advanceSeconds(42);
    const response = d.request("heartbeat", "GET");
    assert.equal(response.code, 200);
    assert.deepEqual(response.headers, [["Content-Type", "application/json"]]);
    assert.deepEqual(response.json(), {
      v: 1,
      role: "heat_source",
      script_version: "1.0.0",
      running: true,
      state: "normal",
      heartbeat_seen: true,
      heartbeat_age_s: 42,
      uptime_s: 342,
      season: true,
      switches: [{ id: 0, output: true }],
      params: { heartbeat_timeout_s: 18000, check_interval_s: 60, switch_id: 0 },
    });
  });

  test("GET does not count as a heartbeat", () => {
    const d = heatSource();
    beat(d, true);
    d.setOutput(0, true);
    for (let i = 0; i < 5; i += 1) {
      d.advanceSeconds(HOUR);
      assert.equal(status(d).season, true);
    }
    assert.equal(d.outputs[0], false);
  });

  test("other methods get 405", () => {
    const d = heatSource();
    assert.equal(d.request("heartbeat", "DELETE").code, 405);
  });

  test("uses one timer and one endpoint", () => {
    const d = heatSource();
    assert.equal(d.timers.size, 1);
    assert.deepEqual([...d.endpoints.keys()], ["heartbeat"]);
  });
});

describe("heat source watchdog: start (D-72, D-102)", () => {
  test("after a reboot the output stays OFF and the timeout counts from boot", () => {
    const d = heatSource();
    beat(d, true);
    d.setOutput(0, true);
    d.advanceSeconds(2 * HOUR);
    d.reboot();
    d.advance(0);
    assert.equal(d.outputs[0], false); // power-on default
    const s = status(d);
    assert.equal(s.state, "normal");
    assert.equal(s.heartbeat_seen, false);
    assert.equal(s.heartbeat_age_s, 0);
    d.setOutput(0, true); // someone switches it ON without HA
    d.advanceSeconds(TIMEOUT_S - CHECK_S);
    assert.equal(d.outputs[0], true);
    assert.equal(d.calls("Switch.Set").length, 0);
    d.advanceSeconds(CHECK_S);
    assert.equal(d.outputs[0], false);
  });

  test("a script restart counts as a heartbeat and keeps output and flag", () => {
    const d = heatSource();
    beat(d, true);
    d.setOutput(0, true);
    d.advanceSeconds(4 * HOUR);
    d.restartScripts();
    d.advance(0);
    assert.equal(d.outputs[0], true);
    assert.equal(status(d).season, true);
    d.advanceSeconds(TIMEOUT_S - CHECK_S);
    assert.equal(d.outputs[0], true);
    d.advanceSeconds(CHECK_S);
    assert.equal(d.outputs[0], false);
  });
});

describe("heat source watchdog: configuration", () => {
  test("shortened timeouts for the bench", () => {
    const d = heatSource({ config: { heartbeat_timeout_s: 120, check_interval_s: 5 } });
    beat(d, true);
    d.setOutput(0, true);
    d.advanceSeconds(115);
    assert.equal(d.outputs[0], true);
    d.advanceSeconds(5);
    assert.equal(d.outputs[0], false);
  });

  test("another switch id", () => {
    const d = heatSource({ switches: 2, config: { switch_id: 1 } });
    d.setOutput(0, true);
    d.setOutput(1, true);
    d.advanceSeconds(TIMEOUT_S);
    assert.deepEqual(d.outputs, [true, false]);
    assert.deepEqual(status(d).switches, [{ id: 1, output: false }]);
  });

  test("a switch id that does not exist is reported and never commanded", () => {
    const d = heatSource({ config: { switch_id: 3 } });
    d.advanceSeconds(TIMEOUT_S + HOUR);
    assert.deepEqual(status(d).switches, [{ id: 3, output: null }]);
    assert.equal(d.calls("Switch.Set").length, 0);
  });

  test("an invalid configuration stops the script at start", () => {
    const bad = [
      { heartbeat_timeout_s: -1 },
      { heartbeat_timeout_s: 1.5 },
      { check_interval_s: "60" },
      { switch_id: null },
      { switch_id: -1 },
      { endpoint: 5 },
      { kvs_season_key: "" },
      { kvs_season_key: "k".repeat(43) },
    ];
    for (const config of bad) {
      const d = new Device({ switches: 1 });
      assert.throws(
        () => d.loadScript(HEAT_SOURCE_SCRIPT, config),
        /CONFIG/,
        JSON.stringify(config),
      );
    }
  });
});
