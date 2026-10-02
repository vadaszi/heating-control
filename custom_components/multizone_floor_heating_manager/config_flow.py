"""Config flow: the YAML is imported into a single config entry.

The YAML stays the only configuration. The entry holds no data; it only lets the
integration appear under Devices & services and own its devices. Adding the
integration from the UI only points to the YAML.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN, NAME


class FloorHeatingConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_import(self, import_data: dict[str, Any]) -> ConfigFlowResult:
        """Create the entry for the YAML configuration (once)."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        return self.async_create_entry(title=NAME, data={})

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Set up in configuration.yaml only."""
        return self.async_abort(reason="yaml_only")
