// Shared helpers for the Shelly script tests.

import { fileURLToPath } from "node:url";

import { Device } from "./shelly_mock.mjs";

// The same files that are uploaded to the devices.
export const VALVE_SCRIPT = fileURLToPath(
  new URL("../../shelly_scripts/valve_watchdog.js", import.meta.url),
);
export const HEAT_SOURCE_SCRIPT = fileURLToPath(
  new URL("../../shelly_scripts/heat_source_watchdog.js", import.meta.url),
);

export const MINUTE = 60;
export const HOUR = 3600;
// Defaults from docs/design.md §4: HeartbeatTimeout 5 h; one check per minute.
export const TIMEOUT_S = 5 * HOUR;
export const CHECK_S = 60;

export function newDevice(file, { switches = 2, config = {} } = {}) {
  const device = new Device({ switches });
  device.loadScript(file, config);
  device.advance(0); // deliver RPC callbacks made at start
  return device;
}

export function heartbeat(device, message = { v: 1 }) {
  return device.request("heartbeat", "POST", { body: JSON.stringify(message) });
}

export function status(device) {
  const response = device.request("heartbeat", "GET");
  if (response.code !== 200) {
    throw new Error(`status returned ${response.code}`);
  }
  return response.json();
}
