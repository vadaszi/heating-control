"""Schedule form buttons: "Add schedule" adds the draft,
"Delete schedule" deletes the schedule chosen in "Existing schedule". A rejected
schedule is shown as a persistent notification."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import FloorHeatingController
from .entity import FormEntity
from .form import async_add_from_form, async_delete_from_form
from .runtime import FloorHeatingConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FloorHeatingConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = entry.runtime_data.controller
    async_add_entities(
        [
            FormButton(controller, "add_schedule", async_add_from_form),
            FormButton(controller, "delete_schedule", async_delete_from_form),
        ]
    )


class FormButton(FormEntity, ButtonEntity):
    def __init__(
        self,
        controller: FloorHeatingController,
        key: str,
        action: Callable[[HomeAssistant, FloorHeatingController], Awaitable[None]],
    ) -> None:
        super().__init__(controller, key)
        self._action = action

    async def async_press(self) -> None:
        await self._action(self.hass, self.controller)
