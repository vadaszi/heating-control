// The mock enforces what the device enforces; these tests keep it honest.

import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, test } from "node:test";

import {
  Device,
  MAX_ENDPOINTS,
  MAX_RPC_IN_FLIGHT,
  MAX_TIMERS,
  applyConfig,
} from "./shelly_mock.mjs";

function script(source) {
  const file = join(mkdtempSync(join(tmpdir(), "shelly-mock-")), "script.js");
  writeFileSync(file, source);
  return file;
}

describe("shelly mock", () => {
  test("timers fire in time order and repeat", () => {
    const d = new Device();
    d.loadScript(
      script(
        [
          "let n = 0;",
          "function tick() { n = n + 1; console.log(n, Shelly.getUptimeMs()); }",
          "function once() { console.log('once', Shelly.getUptimeMs()); }",
          "Timer.set(1000, true, tick);",
          "Timer.set(2500, false, once);",
        ].join("\n"),
      ),
    );
    d.advance(3000);
    assert.deepEqual(d.logs, ["1 1000", "2 2000", "once 2500", "3 3000"]);
    assert.equal(d.uptimeMs, 3000);
  });

  test(`more than ${MAX_TIMERS} timers throw`, () => {
    const d = new Device();
    const source = "function f() {}\nfor (let i = 0; i < 6; i++) { Timer.set(1, false, f); }";
    assert.throws(() => d.loadScript(script(source)), /timers/);
  });

  test(`more than ${MAX_ENDPOINTS} endpoints throw`, () => {
    const d = new Device();
    const source =
      "function f(q, r) { r.send(); }\n" +
      "for (let i = 0; i < 6; i++) { HTTPServer.registerEndpoint('e' + i, f); }";
    assert.throws(() => d.loadScript(script(source)), /endpoints/);
  });

  test(`more than ${MAX_RPC_IN_FLIGHT} RPC calls in flight throw`, () => {
    const d = new Device({ switches: 8 });
    const source = "for (let i = 0; i < 6; i++) { Shelly.call('Switch.Set', {id: i, on: true}); }";
    assert.throws(() => d.loadScript(script(source)), /in flight/);
  });

  test("RPC callbacks are asynchronous", () => {
    const d = new Device();
    d.loadScript(
      script(
        "function done(r, code) { console.log('done', code, r.was_on); }\n" +
          "Shelly.call('Switch.Set', {id: 1, on: true}, done);\nconsole.log('called');",
      ),
    );
    assert.deepEqual(d.logs, ["called"]);
    assert.deepEqual(d.outputs, [false, false]);
    d.advance(0);
    assert.deepEqual(d.logs, ["called", "done 0 false"]);
    assert.deepEqual(d.outputs, [false, true]);
  });

  test("KVS limits and persistence across a reboot", () => {
    const d = new Device();
    d.loadScript(script("Shelly.call('KVS.Set', {key: 'k', value: true});"));
    d.advance(0);
    d.setOutput(0, true);
    d.advance(5000);
    d.reboot();
    assert.equal(d.uptimeMs, 0);
    assert.deepEqual(d.outputs, [false, false]);
    assert.equal(d.kvsValue("k"), true);
    const long = new Device();
    long.loadScript(script(`Shelly.call('KVS.Set', {key: '${"k".repeat(43)}', value: 1});`));
    assert.throws(() => long.advance(0), /key longer/);
  });

  test("the clock: null without NTP, advances with the uptime, lost on a power cycle", () => {
    const d = new Device();
    const sys = () => {
      d.loadScript(script("console.log(JSON.stringify(Shelly.getComponentStatus('sys')));"));
      return JSON.parse(d.logs[d.logs.length - 1]);
    };
    assert.deepEqual(sys(), { uptime: 0, unixtime: null, time: null });
    d.advance(90 * 1000);
    d.setClock("23:59");
    assert.equal(sys().time, "23:59");
    const unixtime = sys().unixtime;
    assert.equal(typeof unixtime, "number");
    d.advance(2 * 60 * 1000);
    assert.equal(sys().time, "00:01"); // across midnight
    assert.equal(sys().unixtime, unixtime + 120);
    d.restartScripts();
    assert.equal(d.localTime(), "00:01"); // a script restart keeps it
    d.reboot();
    assert.equal(d.localTime(), null); // no backup clock
  });

  test("a request must be answered exactly once", () => {
    const d = new Device();
    d.loadScript(script("function f(q, r) {}\nHTTPServer.registerEndpoint('e', f);"));
    assert.throws(() => d.request("e", "GET"), /no response/);
    assert.equal(d.request("missing", "GET").code, 404);
  });

  test("CONFIG overrides must match exactly one key", () => {
    const source = [
      "// ==== CONFIG BEGIN ====",
      "let CONFIG = {",
      "  a: 1,",
      '  b: "x"',
      "};",
      "// ==== CONFIG END ====",
      "",
    ].join("\n");
    assert.equal(
      applyConfig(source, { a: 5, b: [1] }),
      source.replace("a: 1,", "a: 5,").replace('b: "x"', "b: [1]"),
    );
    assert.throws(() => applyConfig(source, { c: 1 }), /found 0 times/);
    assert.throws(() => applyConfig("let x = 1;", { a: 1 }), /markers/);
  });
});
