// floorheat valve watchdog (Shelly script, Gen2 or newer)
//
// Runs on every Shelly that switches zone valves. If Home Assistant's heartbeat
// stops for heartbeat_timeout_s, it switches all valve channels ON (open) and
// keeps them open. When heartbeats return it switches nothing: Home Assistant's
// reconcile loop sets the outputs again.
//
// Heartbeat and status: POST or GET http://<device>/script/<script id>/heartbeat
// Protocol: docs/heartbeat-protocol.md. Setup and bench tests: docs/shelly-scripts.md.
// Spec: docs/design.md §3.6 (failsafe case 2), §5.4, D-100 to D-104.
//
// Edit only the CONFIG block. The script engine runs a JavaScript subset: use
// let, named top-level functions and declare them before calling them.

// ==== CONFIG BEGIN ====
let CONFIG = {
  // Seconds without a heartbeat before all valve channels are switched ON (5 h).
  heartbeat_timeout_s: 18000,
  // Seconds between watchdog checks; the timeout is acted on within one check.
  check_interval_s: 60,
  // Valve channels: null = every switch component of the device, or a list like [0, 1].
  switch_ids: null,
  // Endpoint name in the URL.
  endpoint: "heartbeat"
};
// ==== CONFIG END ====

let PROTOCOL_VERSION = 1;
let SCRIPT_VERSION = "1.0.0";
let ROLE = "valve";
let STATE_NORMAL = "normal";
let STATE_TIMED_OUT = "timed_out";
let MAX_SWITCH_PROBE = 8;
let MAX_PENDING_CALLS = 4;
let JSON_HEADER = ["Content-Type", "application/json"];

let switchIds = [];
let state = STATE_NORMAL;
let lastHeartbeatMs = 0;
let heartbeatSeen = false;
let pendingCalls = 0;

function log(message) {
  console.log("floorheat valve watchdog:", message);
}

function isWholeNumber(value, min, max) {
  return typeof value === "number" && Math.floor(value) === value && value >= min && value <= max;
}

function checkConfig() {
  if (!isWholeNumber(CONFIG.heartbeat_timeout_s, 1, 604800)) {
    throw new Error("CONFIG.heartbeat_timeout_s must be a whole number of seconds (1 to 604800)");
  }
  if (!isWholeNumber(CONFIG.check_interval_s, 1, CONFIG.heartbeat_timeout_s)) {
    throw new Error("CONFIG.check_interval_s must be 1 to heartbeat_timeout_s seconds");
  }
  if (CONFIG.switch_ids !== null) {
    if (typeof CONFIG.switch_ids !== "object" || typeof CONFIG.switch_ids.length !== "number") {
      throw new Error("CONFIG.switch_ids must be null or a list of switch ids");
    }
    for (let i = 0; i < CONFIG.switch_ids.length; i++) {
      if (!isWholeNumber(CONFIG.switch_ids[i], 0, 255)) {
        throw new Error("CONFIG.switch_ids must contain switch ids (0, 1, ...)");
      }
    }
  }
  if (typeof CONFIG.endpoint !== "string" || CONFIG.endpoint === "") {
    throw new Error("CONFIG.endpoint must be a non-empty string");
  }
}

function findSwitchIds() {
  if (CONFIG.switch_ids !== null) {
    return CONFIG.switch_ids;
  }
  let ids = [];
  for (let i = 0; i < MAX_SWITCH_PROBE; i++) {
    if (Shelly.getComponentStatus("switch", i) !== null) {
      ids.push(i);
    }
  }
  return ids;
}

// Current output of a switch: true, false, or null if the switch does not exist.
function outputOf(id) {
  let status = Shelly.getComponentStatus("switch", id);
  if (status === null) {
    return null;
  }
  return status.output;
}

function heartbeatAgeMs() {
  return Shelly.getUptimeMs() - lastHeartbeatMs;
}

function computeState() {
  if (heartbeatAgeMs() >= CONFIG.heartbeat_timeout_s * 1000) {
    return STATE_TIMED_OUT;
  }
  return STATE_NORMAL;
}

// Desired output in a state: true/false, or null = leave the outputs to Home Assistant.
function targetOutput(s) {
  if (s === STATE_TIMED_OUT) {
    return true;
  }
  return null;
}

function onSwitchSet(result, errorCode, errorMessage, id) {
  pendingCalls = pendingCalls - 1;
  if (errorCode !== 0) {
    log("switching channel " + JSON.stringify(id) + " failed: " + errorMessage);
  }
}

// Switch every channel that differs from `on`; the rest follow at the next check.
function enforce(on) {
  for (let i = 0; i < switchIds.length; i++) {
    let id = switchIds[i];
    let output = outputOf(id);
    if (output !== null && output !== on && pendingCalls < MAX_PENDING_CALLS) {
      pendingCalls = pendingCalls + 1;
      Shelly.call("Switch.Set", { id: id, on: on }, onSwitchSet, id);
    }
  }
}

function check() {
  let next = computeState();
  if (next !== state) {
    state = next;
    if (state === STATE_TIMED_OUT) {
      log("no heartbeat for " + JSON.stringify(CONFIG.heartbeat_timeout_s) +
        " s, opening all valves");
    }
  }
  let target = targetOutput(state);
  if (target !== null) {
    enforce(target);
  }
}

function heartbeat() {
  lastHeartbeatMs = Shelly.getUptimeMs();
  heartbeatSeen = true;
  if (state !== STATE_NORMAL) {
    state = STATE_NORMAL;
    log("heartbeat is back, Home Assistant sets the outputs");
  }
}

function status() {
  let switches = [];
  for (let i = 0; i < switchIds.length; i++) {
    switches.push({ id: switchIds[i], output: outputOf(switchIds[i]) });
  }
  return {
    v: PROTOCOL_VERSION,
    role: ROLE,
    script_version: SCRIPT_VERSION,
    running: true,
    state: state,
    heartbeat_seen: heartbeatSeen,
    heartbeat_age_s: Math.floor(heartbeatAgeMs() / 1000),
    uptime_s: Math.floor(Shelly.getUptimeMs() / 1000),
    switches: switches,
    params: {
      heartbeat_timeout_s: CONFIG.heartbeat_timeout_s,
      check_interval_s: CONFIG.check_interval_s,
      switch_ids: CONFIG.switch_ids
    }
  };
}

function reply(response, code, body, extraHeader) {
  response.code = code;
  response.headers = [JSON_HEADER];
  if (extraHeader !== undefined) {
    response.headers.push(extraHeader);
  }
  response.body = JSON.stringify(body);
  response.send();
}

// The request body as an object ({} when empty), or null when it is not a JSON object.
function parseBody(body) {
  if (body === undefined || body === null || body === "") {
    return {};
  }
  let message = null;
  try {
    message = JSON.parse(body);
  } catch (e) {
    return null;
  }
  if (typeof message !== "object" || message === null || typeof message.length === "number") {
    return null;
  }
  return message;
}

function onRequest(request, response) {
  if (request.method === "POST") {
    if (parseBody(request.body) === null) {
      reply(response, 400, { error: "the body must be a JSON object" });
      return;
    }
    heartbeat();
  } else if (request.method !== "GET") {
    reply(response, 405, { error: "use GET or POST" }, ["Allow", "GET, POST"]);
    return;
  }
  reply(response, 200, status());
}

function start() {
  checkConfig();
  switchIds = findSwitchIds();
  // Script start counts as the last heartbeat (D-102); outputs keep their state.
  lastHeartbeatMs = Shelly.getUptimeMs();
  HTTPServer.registerEndpoint(CONFIG.endpoint, onRequest);
  Timer.set(CONFIG.check_interval_s * 1000, true, check);
  log("started, channels " + JSON.stringify(switchIds));
}

start();
