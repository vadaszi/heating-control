// The watchdog scripts use only the Shelly engine's JavaScript subset (see subset.mjs).

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { basename } from "node:path";
import { describe, test } from "node:test";

import { checkSubset } from "./subset.mjs";
import { HEAT_SOURCE_SCRIPT, VALVE_SCRIPT } from "./helpers.mjs";

// Documents of the user manual the scripts may point to.
const MANUAL = ["docs/shelly-scripts.md", "docs/heartbeat-protocol.md"];

describe("device scripts", () => {
  for (const file of [VALVE_SCRIPT, HEAT_SOURCE_SCRIPT]) {
    test(`${basename(file)} uses only the supported subset`, () => {
      assert.deepEqual(checkSubset(readFileSync(file, "utf8")), []);
    });

    test(`${basename(file)} refers only to the user manual`, () => {
      // The scripts are released; they refer only to the user manual.
      const source = readFileSync(file, "utf8");
      for (const pattern of [/design\.md/, /implementation-plan/, /\bD-\d+/, /§/]) {
        assert.doesNotMatch(source, pattern);
      }
      const docs = source.match(/docs\/[\w-]+\.md/g) ?? [];
      for (const doc of docs) {
        assert.ok(MANUAL.includes(doc), `${doc} is not part of the user manual`);
      }
    });

    test(`${basename(file)} has one CONFIG block at the top`, () => {
      const source = readFileSync(file, "utf8");
      const begin = source.indexOf("// ==== CONFIG BEGIN ====");
      const end = source.indexOf("// ==== CONFIG END ====");
      assert.ok(begin > 0 && end > begin);
      assert.equal(source.indexOf("// ==== CONFIG BEGIN ====", begin + 1), -1);
      const before = source.slice(0, begin).split("\n");
      assert.ok(
        before.every((line) => line.trim() === "" || line.trim().startsWith("//")),
        "only comments before the CONFIG block",
      );
      assert.match(source.slice(begin, end), /^let CONFIG = \{$/m);
    });
  }
});

describe("the checker rejects", () => {
  const rejected = {
    "arrow functions": "let f = function_ok; let g = (x) => x;",
    const: "const x = 1;",
    classes: "class A {}",
    "template literals": "let s = `a${1}`;",
    "anonymous functions": "Timer.set(1, false, function () {});",
    "nested functions": "function a() { function b() {} }",
    destructuring: "let {a} = {a: 1};",
    "array destructuring": "let [a] = [1];",
    spread: "let a = [1]; let b = [0, ...a];",
    "default parameters": "function f(a = 1) { return a; }",
    "rest parameters": "function f(...a) { return a; }",
    "for-of": "let a = []; for (let x of a) {}",
    "for-in": "let o = {}; for (let k in o) {}",
    "shorthand properties": "let a = 1; let o = {a};",
    "method properties": "let o = {f() {}};",
    getters: "let o = {get f() { return 1; }};",
    "regex literals": "let r = /a/;",
    "unicode escapes": 'let s = "\\u00e9";',
    promises: "let p = Promise;",
    setTimeout: "setTimeout(start, 1);",
    "newer methods": 'let s = "ab"; s.startsWith("a");',
    hoisting: "start(); function start() {}",
    "hoisted callback reference": "Timer.set(1, false, tick); function tick() {}",
    "ES2016+ syntax": "let a = 2 ** 3;",
    generators: "function* g() {}",
  };
  for (const [name, source] of Object.entries(rejected)) {
    test(name, () => {
      assert.notDeepEqual(checkSubset(source), [], source);
    });
  }

  test("nothing in a valid script", () => {
    const source = [
      "let CONFIG = {a: 1, b: [1, 2], c: null};",
      "function tick(ud) { let x = CONFIG.a > 0 ? 1 : 2; return x; }",
      'function start() { Timer.set(1000, true, tick); console.log("ok", "\\x41"); }',
      "start();",
    ].join("\n");
    assert.deepEqual(checkSubset(source), []);
  });
});
