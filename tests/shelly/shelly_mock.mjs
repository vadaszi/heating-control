// Minimal mock of the Shelly script runtime (Gen2 and newer) with simulated time.
//
// It loads the same script files that go on the device (shelly_scripts/*.js) into
// a `node:vm` context that exposes only the Shelly globals the scripts use:
// Timer, Shelly (call, getComponentStatus, getUptimeMs), HTTPServer, Script and
// console. Documented per-script resource limits are enforced by throwing, so a
// script that would fail on the device fails its tests too.
//
// Time: `uptimeMs` is the device uptime. Nothing happens until a test calls
// `advance(ms)`, which fires due timers and delivers RPC callbacks in time order.
// RPC callbacks are always asynchronous (delivered on the next `advance`, also
// `advance(0)`), as on the device.

import { readFileSync } from "node:fs";
import vm from "node:vm";

// Shelly script resource limits (docs: Script Language Reference, Resource Limits).
export const MAX_TIMERS = 5;
export const MAX_ENDPOINTS = 5;
export const MAX_RPC_IN_FLIGHT = 5;
// KVS limits (docs: KVS service).
export const KVS_MAX_KEY_LENGTH = 42;
export const KVS_MAX_VALUE_LENGTH = 253;
export const KVS_MAX_KEYS = 50;

// More events than this at one simulated instant means a script is looping.
const MAX_EVENTS_AT_ONE_TIME = 1000;

// Error code the firmware returns for a missing key or component.
export const ERR_NOT_FOUND = -105;

const CONFIG_BEGIN = "// ==== CONFIG BEGIN ====";
const CONFIG_END = "// ==== CONFIG END ====";

/**
 * Return `source` with values in the CONFIG block replaced.
 * Each key must appear exactly once in the block, as `key: value` on its own line.
 */
export function applyConfig(source, overrides) {
  if (Object.keys(overrides).length === 0) {
    return source;
  }
  const begin = source.indexOf(CONFIG_BEGIN);
  const end = source.indexOf(CONFIG_END);
  if (begin < 0 || end < begin) {
    throw new Error("CONFIG block markers not found");
  }
  let block = source.slice(begin, end);
  for (const [key, value] of Object.entries(overrides)) {
    const pattern = new RegExp(`^(\\s*${key}:\\s*)([^\\n]*?)(,?)$`, "gm");
    const matches = block.match(pattern) ?? [];
    if (matches.length !== 1) {
      throw new Error(`CONFIG key ${key} found ${matches.length} times`);
    }
    block = block.replace(pattern, (_m, head, _old, comma) => head + JSON.stringify(value) + comma);
  }
  return source.slice(0, begin) + block + source.slice(end);
}

export class Device {
  /**
   * @param {object} options
   * @param {number} options.switches number of switch components (ids 0..n-1)
   */
  constructor({ switches = 2 } = {}) {
    this.switchCount = switches;
    this.kvs = new Map(); // key -> JSON text; survives reboots
    this.kvsEtag = 0;
    this.scripts = []; // {file, overrides}; restarted on reboot ("run on startup")
    this.failures = []; // {method, code, message} consumed by the next matching call
    this.rpcDelayMs = 0;
    this.#powerOn();
  }

  // ------------------------------------------------------------------ lifecycle

  #powerOn() {
    this.uptimeMs = 0;
    this.outputs = new Array(this.switchCount).fill(false); // power-on default OFF
    this.#resetRuntime();
  }

  #resetRuntime() {
    this.timers = new Map(); // handle -> {due, period, repeat, callback, userdata, seq}
    this.nextHandle = 1;
    this.seq = 0;
    this.pending = []; // RPC callbacks: {due, seq, run}
    this.inFlight = 0;
    this.endpoints = new Map(); // name -> {callback, userdata}
    this.logs = [];
    this.rpcLog = []; // {method, params} of every Shelly.call
  }

  /** Load a script file (optionally with CONFIG overrides) and run it. */
  loadScript(file, overrides = {}) {
    this.scripts.push({ file, overrides });
    this.#run(file, overrides);
  }

  /** Power cycle: uptime 0, outputs at the power-on default, KVS kept, scripts restarted. */
  reboot() {
    this.#powerOn();
    for (const { file, overrides } of this.scripts) {
      this.#run(file, overrides);
    }
  }

  /** Stop and start the scripts without a reboot: uptime and outputs are kept. */
  restartScripts() {
    this.#resetRuntime();
    for (const { file, overrides } of this.scripts) {
      this.#run(file, overrides);
    }
  }

  #run(file, overrides) {
    const source = applyConfig(readFileSync(file, "utf8"), overrides);
    const context = vm.createContext(this.#globals());
    vm.runInContext(source, context, { filename: file });
  }

  // ------------------------------------------------------------------ time

  /** Advance simulated time, firing timers and delivering RPC callbacks in order. */
  advance(ms) {
    const target = this.uptimeMs + ms;
    let sameTime = 0;
    for (;;) {
      const next = this.#nextEvent();
      if (next === null || next.due > target) {
        break;
      }
      sameTime = next.due > this.uptimeMs ? 0 : sameTime + 1;
      if (sameTime > MAX_EVENTS_AT_ONE_TIME) {
        // e.g. a callback that immediately repeats its RPC: would starve the device
        throw new Error("runaway: too many timer/RPC events without time passing");
      }
      this.uptimeMs = Math.max(this.uptimeMs, next.due);
      next.run();
    }
    this.uptimeMs = target;
  }

  advanceSeconds(s) {
    this.advance(s * 1000);
  }

  #nextEvent() {
    let best = null;
    for (const [handle, t] of this.timers) {
      if (best === null || t.due < best.due || (t.due === best.due && t.seq < best.seq)) {
        best = { due: t.due, seq: t.seq, run: () => this.#fireTimer(handle) };
      }
    }
    for (const p of this.pending) {
      if (best === null || p.due < best.due || (p.due === best.due && p.seq < best.seq)) {
        best = { due: p.due, seq: p.seq, run: () => this.#deliver(p) };
      }
    }
    return best;
  }

  #fireTimer(handle) {
    const t = this.timers.get(handle);
    if (t.repeat) {
      t.due += t.period;
      t.seq = this.seq++;
    } else {
      this.timers.delete(handle);
    }
    t.callback(t.userdata);
  }

  #deliver(p) {
    this.pending.splice(this.pending.indexOf(p), 1);
    this.inFlight -= 1;
    p.run();
  }

  // ------------------------------------------------------------------ test controls

  /** Switch an output from outside the script (HA's reconcile, the Shelly app). */
  setOutput(id, on) {
    this.outputs[id] = on;
  }

  /** Make the next call of `method` fail with the given error code. */
  failNext(method, code = -1, message = "simulated failure") {
    this.failures.push({ method, code, message });
  }

  /** Delay RPC callbacks (0 = next `advance`). */
  setRpcDelay(ms) {
    this.rpcDelayMs = ms;
  }

  /** RPC calls of `method` made so far. */
  calls(method) {
    return this.rpcLog.filter((c) => c.method === method);
  }

  kvsValue(key) {
    return this.kvs.has(key) ? JSON.parse(this.kvs.get(key)) : undefined;
  }

  /**
   * Send an HTTP request to a script endpoint. The scripts answer synchronously,
   * so the response must have been sent when the handler returns.
   */
  request(name, method, { body, query, headers = [] } = {}) {
    const endpoint = this.endpoints.get(name);
    if (endpoint === undefined) {
      return { code: 404, body: "", headers: [], json: () => undefined };
    }
    if (body !== undefined && body.length > 3072) {
      throw new Error("request larger than 3072 bytes: the device resets the connection");
    }
    const request = { method, headers };
    if (query !== undefined) {
      request.query = query;
    }
    if (body !== undefined) {
      request.body = body;
    }
    let sent = 0;
    const response = {
      code: 200,
      body: "",
      headers: [],
      send() {
        sent += 1;
        if (sent > 1) {
          throw new Error("response sent twice");
        }
        return true;
      },
    };
    endpoint.callback(request, response, endpoint.userdata);
    if (sent !== 1) {
      throw new Error("no response sent (the device would time out with 504)");
    }
    return {
      code: response.code,
      body: response.body,
      headers: JSON.parse(JSON.stringify(response.headers)), // into this realm
      json: () => JSON.parse(response.body),
    };
  }

  // ------------------------------------------------------------------ the runtime

  #globals() {
    const device = this;
    const log = (...args) => device.logs.push(args.map(String).join(" "));
    return {
      console: { log },
      Script: { id: 1 },
      Timer: {
        set(period, repeat, callback, userdata) {
          if (typeof period !== "number" || !(period >= 0) || typeof callback !== "function") {
            throw new Error("Timer.set: invalid arguments");
          }
          if (device.timers.size >= MAX_TIMERS) {
            throw new Error(`Timer.set: more than ${MAX_TIMERS} timers`);
          }
          const handle = device.nextHandle++;
          device.timers.set(handle, {
            due: device.uptimeMs + period,
            period,
            repeat: Boolean(repeat),
            callback,
            userdata,
            seq: device.seq++,
          });
          return handle;
        },
        clear(handle) {
          return device.timers.delete(handle);
        },
      },
      HTTPServer: {
        registerEndpoint(name, callback, userdata) {
          if (typeof name !== "string" || name === "" || typeof callback !== "function") {
            throw new Error("HTTPServer.registerEndpoint: invalid arguments");
          }
          if (device.endpoints.has(name)) {
            throw new Error(`HTTPServer.registerEndpoint: ${name} already registered`);
          }
          if (device.endpoints.size >= MAX_ENDPOINTS) {
            throw new Error(`HTTPServer.registerEndpoint: more than ${MAX_ENDPOINTS} endpoints`);
          }
          device.endpoints.set(name, { callback, userdata });
          return `/script/1/${name}`;
        },
      },
      Shelly: {
        getUptimeMs() {
          return device.uptimeMs;
        },
        getComponentStatus(typeOrKey, id) {
          let type = typeOrKey;
          let index = id;
          if (id === undefined && typeOrKey.includes(":")) {
            [type, index] = typeOrKey.split(":");
            index = Number(index);
          }
          if (type === "switch") {
            if (!Number.isInteger(index) || index < 0 || index >= device.switchCount) {
              return null;
            }
            return { id: index, source: "mock", output: device.outputs[index] };
          }
          if (type === "sys") {
            return { uptime: Math.floor(device.uptimeMs / 1000), unixtime: null, time: null };
          }
          throw new Error(`getComponentStatus: component ${typeOrKey} not mocked`);
        },
        call(method, params, callback, userdata) {
          if (device.inFlight >= MAX_RPC_IN_FLIGHT) {
            throw new Error(`Shelly.call: more than ${MAX_RPC_IN_FLIGHT} calls in flight`);
          }
          if (callback !== undefined && typeof callback !== "function") {
            throw new Error("Shelly.call: callback must be a function");
          }
          device.rpcLog.push({ method, params: structuredClone(params) });
          device.inFlight += 1;
          device.pending.push({
            due: device.uptimeMs + device.rpcDelayMs,
            seq: device.seq++,
            run: () => {
              const [result, code, message] = device.#execute(method, params);
              if (callback !== undefined) {
                callback(result, code, message, userdata);
              }
            },
          });
        },
      },
    };
  }

  // Runs an RPC when its callback is delivered; returns [result, error_code, error_message].
  #execute(method, params) {
    const failure = this.failures.findIndex((f) => f.method === method);
    if (failure >= 0) {
      const [{ code, message }] = this.failures.splice(failure, 1);
      return [undefined, code, message];
    }
    switch (method) {
      case "Switch.Set": {
        const { id, on } = params;
        if (!Number.isInteger(id) || id < 0 || id >= this.switchCount) {
          return [undefined, ERR_NOT_FOUND, `Component switch:${id} not found!`];
        }
        if (typeof on !== "boolean") {
          return [undefined, -103, "Missing or invalid argument 'on'!"];
        }
        const wasOn = this.outputs[id];
        this.outputs[id] = on;
        return [{ was_on: wasOn }, 0, ""];
      }
      case "KVS.Get": {
        if (!this.kvs.has(params.key)) {
          return [undefined, ERR_NOT_FOUND, `Argument 'key', value '${params.key}' not found!`];
        }
        return [{ etag: "e", value: JSON.parse(this.kvs.get(params.key)) }, 0, ""];
      }
      case "KVS.Set": {
        const { key, value } = params;
        const text = JSON.stringify(value);
        if (typeof key !== "string" || key.length > KVS_MAX_KEY_LENGTH) {
          throw new Error(`KVS.Set: key longer than ${KVS_MAX_KEY_LENGTH}`);
        }
        if (text === undefined || text.length > KVS_MAX_VALUE_LENGTH) {
          throw new Error(`KVS.Set: value longer than ${KVS_MAX_VALUE_LENGTH}`);
        }
        if (!this.kvs.has(key) && this.kvs.size >= KVS_MAX_KEYS) {
          throw new Error(`KVS.Set: more than ${KVS_MAX_KEYS} keys`);
        }
        this.kvs.set(key, text);
        this.kvsEtag += 1;
        return [{ etag: String(this.kvsEtag), rev: this.kvsEtag }, 0, ""];
      }
      default:
        throw new Error(`Shelly.call: RPC method ${method} not mocked`);
    }
  }
}
