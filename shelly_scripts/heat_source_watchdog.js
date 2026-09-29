// Multizone Floor Heating Manager: heat source watchdog (Shelly script, Gen2 or newer)
//
// Runs on the Shelly that switches the heat source request. If Home Assistant's
// heartbeat stops for heartbeat_timeout_s, it switches the output OFF and keeps
// it OFF. When heartbeats return it switches nothing: Home Assistant's reconcile
// loop sets the output again. Each heartbeat carries the heating season flag,
// which is kept in the device's KVS for the failsafe (added in v1.2).
//
// Heartbeat and status: POST or GET http://<device>/script/<script id>/heartbeat
// Protocol: docs/heartbeat-protocol.md. Setup and bench tests: docs/shelly-scripts.md.
// Spec: docs/design.md §3.6 (failsafe case 2), §5.4, D-72, D-100 to D-105.
//
// Edit only the CONFIG block. The script engine runs a JavaScript subset: use
// let, named top-level functions and declare them before calling them.

// ==== CONFIG BEGIN ====
let CONFIG = {
  // Seconds without a heartbeat before the output is switched OFF (5 h).
  heartbeat_timeout_s: 18000,
  // Seconds between watchdog checks; the timeout is acted on within one check.
  check_interval_s: 60,
  // The switch that requests heat.
  switch_id: 0,
  // Endpoint name in the URL.
  endpoint: "heartbeat",
  // KVS key that keeps the heating season flag from the last heartbeat.
  kvs_season_key: "multizone_floor_heating_manager_season"
};
// ==== CONFIG END ====

let PROTOCOL_VERSION = 1;
let SCRIPT_VERSION = "1.0.0";
let ROLE = "heat_source";
let STATE_NORMAL = "normal";
let STATE_TIMED_OUT = "timed_out";
let KVS_NOT_FOUND = -105;
let KVS_MAX_KEY_LENGTH = 42;
let JSON_HEADER = ["Content-Type", "application/json"];

let state = STATE_NORMAL;
let lastHeartbeatMs = 0;
let heartbeatSeen = false;
let switchPending = false;
// Season flag from the last heartbeat: true, false, or null (never set).
let season = null;
let seasonFromHeartbeat = false;
// What KVS holds: true, false, null (no key), or undefined (unknown).
let storedSeason;
let seasonLoaded = false;
let seasonWriting = false;

function log(message) {
  console.log("Floor heating heat source watchdog:", message);
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
  if (!isWholeNumber(CONFIG.switch_id, 0, 255)) {
    throw new Error("CONFIG.switch_id must be a switch id (0, 1, ...)");
  }
  if (typeof CONFIG.endpoint !== "string" || CONFIG.endpoint === "") {
    throw new Error("CONFIG.endpoint must be a non-empty string");
  }
  let key = CONFIG.kvs_season_key;
  if (typeof key !== "string" || key === "" || key.length > KVS_MAX_KEY_LENGTH) {
    throw new Error("CONFIG.kvs_season_key must be a string of 1 to 42 characters");
  }
}

// Current output: true, false, or null if the switch does not exist.
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

// v1.2 (P12) adds the failsafe state here: after FailsafeTrigger, heat in the
// daily window by the device clock or by the uptime cycle, only with season true.
function computeState() {
  if (heartbeatAgeMs() >= CONFIG.heartbeat_timeout_s * 1000) {
    return STATE_TIMED_OUT;
  }
  return STATE_NORMAL;
}

// Desired output in a state: true/false, or null = leave the output to Home Assistant.
function targetOutput(s) {
  if (s === STATE_TIMED_OUT) {
    return false;
  }
  return null;
}

function onSwitchSet(result, errorCode, errorMessage) {
  switchPending = false;
  if (errorCode !== 0) {
    log("switching failed: " + errorMessage);
  }
}

function enforce(on) {
  let output = outputOf(CONFIG.switch_id);
  if (output !== null && output !== on && !switchPending) {
    switchPending = true;
    Shelly.call("Switch.Set", { id: CONFIG.switch_id, on: on }, onSwitchSet);
  }
}

function check() {
  let next = computeState();
  if (next !== state) {
    state = next;
    if (state === STATE_TIMED_OUT) {
      log("no heartbeat for " + JSON.stringify(CONFIG.heartbeat_timeout_s) +
        " s, switching the heat source OFF");
    }
  }
  let target = targetOutput(state);
  if (target !== null) {
    enforce(target);
  }
}

function onSeasonStored(result, errorCode, errorMessage, value) {
  seasonWriting = false;
  if (errorCode !== 0) {
    log("storing the season flag failed: " + errorMessage);
    return;
  }
  storedSeason = value;
  persistSeason();
}

// Write the flag to KVS only when it differs from the stored value (flash wear).
function persistSeason() {
  if (!seasonLoaded || seasonWriting || season === null || season === storedSeason) {
    return;
  }
  seasonWriting = true;
  Shelly.call("KVS.Set", { key: CONFIG.kvs_season_key, value: season }, onSeasonStored, season);
}

function onSeasonLoaded(result, errorCode, errorMessage) {
  seasonLoaded = true;
  if (errorCode === 0) {
    if (result.value === true || result.value === false) {
      storedSeason = result.value;
    } else {
      log("ignoring an invalid stored season flag");
    }
  } else if (errorCode === KVS_NOT_FOUND) {
    storedSeason = null;
  } else {
    log("reading the season flag failed: " + errorMessage);
  }
  // A heartbeat that arrived during the read is newer than the stored value.
  if (!seasonFromHeartbeat && storedSeason !== undefined) {
    season = storedSeason;
  }
  persistSeason();
}

function heartbeat(newSeason) {
  lastHeartbeatMs = Shelly.getUptimeMs();
  heartbeatSeen = true;
  season = newSeason;
  seasonFromHeartbeat = true;
  if (state !== STATE_NORMAL) {
    state = STATE_NORMAL;
    log("heartbeat is back, Home Assistant sets the output");
  }
  persistSeason();
}

function status() {
  return {
    v: PROTOCOL_VERSION,
    role: ROLE,
    script_version: SCRIPT_VERSION,
    running: true,
    state: state,
    heartbeat_seen: heartbeatSeen,
    heartbeat_age_s: Math.floor(heartbeatAgeMs() / 1000),
    uptime_s: Math.floor(Shelly.getUptimeMs() / 1000),
    season: season,
    switches: [{ id: CONFIG.switch_id, output: outputOf(CONFIG.switch_id) }],
    params: {
      heartbeat_timeout_s: CONFIG.heartbeat_timeout_s,
      check_interval_s: CONFIG.check_interval_s,
      switch_id: CONFIG.switch_id
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

// The season flag of a heartbeat body, or null when the body is not valid.
function parseSeason(body) {
  if (body === undefined || body === null || body === "") {
    return null;
  }
  let message = null;
  try {
    message = JSON.parse(body);
  } catch (e) {
    return null;
  }
  if (typeof message !== "object" || message === null) {
    return null;
  }
  if (message.season !== true && message.season !== false) {
    return null;
  }
  return message.season;
}

function onRequest(request, response) {
  if (request.method === "POST") {
    let newSeason = parseSeason(request.body);
    if (newSeason === null) {
      reply(response, 400, { error: "the body must be a JSON object with season true or false" });
      return;
    }
    heartbeat(newSeason);
  } else if (request.method !== "GET") {
    reply(response, 405, { error: "use GET or POST" }, ["Allow", "GET, POST"]);
    return;
  }
  reply(response, 200, status());
}

function start() {
  checkConfig();
  // Script start counts as the last heartbeat (D-72, D-102); the output keeps its state.
  lastHeartbeatMs = Shelly.getUptimeMs();
  Shelly.call("KVS.Get", { key: CONFIG.kvs_season_key }, onSeasonLoaded);
  HTTPServer.registerEndpoint(CONFIG.endpoint, onRequest);
  Timer.set(CONFIG.check_interval_s * 1000, true, check);
  log("started, switch " + JSON.stringify(CONFIG.switch_id));
}

start();
