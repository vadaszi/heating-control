// Multizone Floor Heating Manager: heat source watchdog (Shelly script, Gen2 or newer)
//
// Runs on the Shelly that switches the heat source request. If Home Assistant's
// heartbeat stops for heartbeat_timeout_s, it switches the output OFF and keeps
// it OFF. After failsafe_trigger_s without a heartbeat, and only if the last
// heartbeat said "heating season ON", it runs the failsafe operation: heat every
// day from failsafe_start to failsafe_stop by the device clock, or, without a
// valid clock, by an uptime cycle (uptime_on_s ON, then uptime_off_s OFF). When
// heartbeats return it switches nothing: Home Assistant's reconcile loop sets the
// output again. Each heartbeat carries the heating season flag, which is kept in
// the device's KVS.
//
// Heartbeat and status: POST or GET http://<device>/script/<script id>/heartbeat
// Protocol: docs/heartbeat-protocol.md. Setup and bench tests: docs/shelly-scripts.md.
// Spec: docs/design.md §3.6 (failsafe case 2), §5.4, D-72, D-100 to D-105, D-153.
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
  kvs_season_key: "multizone_floor_heating_manager_season",
  // Failsafe operation: seconds without a heartbeat before it starts (24 h).
  // Longer than heartbeat_timeout_s.
  failsafe_trigger_s: 86400,
  // Failsafe operation start and stop, local time of the device ("HH:MM").
  // The window may cross midnight ("22:00" to "03:00"); start and stop differ.
  failsafe_start: "10:00",
  failsafe_stop: "15:00",
  // Without a valid clock (no NTP after a power cut): an uptime cycle of
  // uptime_on_s ON, then uptime_off_s OFF, starting with ON (5 h / 19 h).
  uptime_on_s: 18000,
  uptime_off_s: 68400,
  // Failsafe operation only: the shortest ON and OFF time the script switches
  // (1 h each), so it never short-cycles the heat source. 0 = no minimum.
  min_on_s: 3600,
  min_off_s: 3600
};
// ==== CONFIG END ====

let PROTOCOL_VERSION = 1;
let SCRIPT_VERSION = "1.1.0";
let ROLE = "heat_source";
let STATE_NORMAL = "normal";
let STATE_TIMED_OUT = "timed_out";
let STATE_FAILSAFE = "failsafe";
let MAX_SECONDS = 604800;
let MINUTES_PER_DAY = 1440;
let KVS_NOT_FOUND = -105;
let KVS_MAX_KEY_LENGTH = 42;
let JSON_HEADER = ["Content-Type", "application/json"];

let state = STATE_NORMAL;
let lastHeartbeatMs = 0;
let heartbeatSeen = false;
let switchPending = false;
// Output the script wants (true/false), or null while it leaves it to Home Assistant,
// and the uptime since when; the minimum ON/OFF times count from there.
let target = null;
let targetSinceMs = 0;
let seasonSkipLogged = false;
// Window in minutes after local midnight, from CONFIG (set by checkConfig).
let windowStart = 0;
let windowStop = 0;
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

// Minutes after midnight of "HH:MM", or -1 if the text is not a time of day.
function minutesOf(text) {
  if (typeof text !== "string" || text.length !== 5 || text.charAt(2) !== ":") {
    return -1;
  }
  let hours = digits(text.slice(0, 2));
  let minutes = digits(text.slice(3, 5));
  if (hours < 0 || hours > 23 || minutes < 0 || minutes > 59) {
    return -1;
  }
  return hours * 60 + minutes;
}

// The value of two decimal digits, or -1.
function digits(text) {
  let value = 0;
  for (let i = 0; i < text.length; i += 1) {
    let c = text.charCodeAt(i) - 48;
    if (c < 0 || c > 9) {
      return -1;
    }
    value = value * 10 + c;
  }
  return value;
}

function checkConfig() {
  if (!isWholeNumber(CONFIG.heartbeat_timeout_s, 1, MAX_SECONDS)) {
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
  if (!isWholeNumber(CONFIG.failsafe_trigger_s, CONFIG.heartbeat_timeout_s + 1, MAX_SECONDS)) {
    throw new Error("CONFIG.failsafe_trigger_s must be longer than heartbeat_timeout_s " +
      "(whole seconds, up to 604800)");
  }
  windowStart = minutesOf(CONFIG.failsafe_start);
  windowStop = minutesOf(CONFIG.failsafe_stop);
  if (windowStart < 0 || windowStop < 0) {
    throw new Error("CONFIG.failsafe_start and failsafe_stop must be times like \"10:00\"");
  }
  if (windowStart === windowStop) {
    throw new Error("CONFIG.failsafe_start and failsafe_stop must differ");
  }
  if (!isWholeNumber(CONFIG.uptime_on_s, 1, MAX_SECONDS) ||
      !isWholeNumber(CONFIG.uptime_off_s, 1, MAX_SECONDS)) {
    throw new Error("CONFIG.uptime_on_s and uptime_off_s must be 1 to 604800 seconds");
  }
  if (!isWholeNumber(CONFIG.min_on_s, 0, MAX_SECONDS) ||
      !isWholeNumber(CONFIG.min_off_s, 0, MAX_SECONDS)) {
    throw new Error("CONFIG.min_on_s and min_off_s must be 0 to 604800 seconds");
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

// The device's local time ("HH:MM"), or null without a valid time (no NTP yet).
function localTime() {
  let sys = Shelly.getComponentStatus("sys");
  if (sys === null || typeof sys.time !== "string") {
    return null;
  }
  return sys.time;
}

// The failsafe operation runs only with the season flag true; null counts as OFF (D-105).
function computeState() {
  let age = heartbeatAgeMs();
  if (age >= CONFIG.failsafe_trigger_s * 1000 && season === true) {
    return STATE_FAILSAFE;
  }
  if (age >= CONFIG.heartbeat_timeout_s * 1000) {
    return STATE_TIMED_OUT;
  }
  return STATE_NORMAL;
}

function inWindow(minutes) {
  if (windowStart < windowStop) {
    return minutes >= windowStart && minutes < windowStop;
  }
  return minutes >= windowStart || minutes < windowStop;
}

// Failsafe operation: the clock window, or without a valid clock the uptime cycle,
// which starts with its ON phase when the failsafe operation starts (D-72, D-153).
function failsafeWanted() {
  let time = localTime();
  let minutes = time === null ? -1 : minutesOf(time);
  if (minutes >= 0) {
    return inWindow(minutes);
  }
  let since = heartbeatAgeMs() - CONFIG.failsafe_trigger_s * 1000;
  let period = (CONFIG.uptime_on_s + CONFIG.uptime_off_s) * 1000;
  return since % period < CONFIG.uptime_on_s * 1000;
}

// Desired output in a state: true/false, or null = leave the output to Home Assistant.
// In the failsafe operation a change waits for min_on_s / min_off_s since the last one.
function targetOutput(s) {
  if (s === STATE_TIMED_OUT) {
    return false;
  }
  if (s !== STATE_FAILSAFE) {
    return null;
  }
  let wanted = failsafeWanted();
  if (target === true || target === false) {
    let minimum = target ? CONFIG.min_on_s : CONFIG.min_off_s;
    if (wanted !== target && Shelly.getUptimeMs() - targetSinceMs < minimum * 1000) {
      return target;
    }
  }
  return wanted;
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
    } else if (state === STATE_FAILSAFE) {
      log("no heartbeat for " + JSON.stringify(CONFIG.failsafe_trigger_s) +
        " s, the failsafe operation starts");
    }
  }
  if (state === STATE_TIMED_OUT && !seasonSkipLogged &&
      heartbeatAgeMs() >= CONFIG.failsafe_trigger_s * 1000) {
    seasonSkipLogged = true;
    log("no failsafe operation: the last heartbeat did not say heating season ON");
  }
  let nextTarget = targetOutput(state);
  if (nextTarget !== target) {
    target = nextTarget;
    targetSinceMs = Shelly.getUptimeMs();
    if (state === STATE_FAILSAFE) {
      log("failsafe operation: heat source " + (target ? "ON" : "OFF") +
        (localTime() === null ? " (uptime cycle, no valid time)" : " (" + localTime() + ")"));
    }
  }
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
  target = null;
  seasonSkipLogged = false;
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
    time: localTime(),
    switches: [{ id: CONFIG.switch_id, output: outputOf(CONFIG.switch_id) }],
    params: {
      heartbeat_timeout_s: CONFIG.heartbeat_timeout_s,
      check_interval_s: CONFIG.check_interval_s,
      switch_id: CONFIG.switch_id,
      failsafe_trigger_s: CONFIG.failsafe_trigger_s,
      failsafe_start: CONFIG.failsafe_start,
      failsafe_stop: CONFIG.failsafe_stop,
      uptime_on_s: CONFIG.uptime_on_s,
      uptime_off_s: CONFIG.uptime_off_s,
      min_on_s: CONFIG.min_on_s,
      min_off_s: CONFIG.min_off_s
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
