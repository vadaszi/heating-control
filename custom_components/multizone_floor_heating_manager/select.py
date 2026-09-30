"""Schedule form selects (docs/design.md §5.3, D-74, D-138): type, zone and days of the
next schedule, and the existing schedule that "Delete schedule" deletes."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .controller import FloorheatController
from .core.schedule import ScheduleKind
from .core.units import TemperatureUnit
from .entity import FormEntity
from .form import ALL_ZONES, selected_schedule
from .runtime import FloorheatConfigEntry
from .schedules import DAY_OPTIONS, schedule_label


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FloorheatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    controller = entry.runtime_data.controller
    unit = TemperatureUnit(hass.config.units.temperature_unit)
    async_add_entities(
        [
            KindSelect(controller),
            ZoneSelect(controller),
            DaysSelect(controller),
            ExistingScheduleSelect(controller, unit),
        ]
    )


class KindSelect(FormEntity, SelectEntity):
    _attr_options = [kind.value for kind in ScheduleKind]  # noqa: RUF012 - HA's attribute convention

    def __init__(self, controller: FloorheatController) -> None:
        super().__init__(controller, "schedule_kind")

    @property
    def current_option(self) -> str:
        return self.controller.form.kind.value

    async def async_select_option(self, option: str) -> None:
        self.controller.form.kind = ScheduleKind(option)
        self.async_write_ha_state()


class ZoneSelect(FormEntity, SelectEntity):
    """`All zones` or one zone by name; several zones only through the service (D-138)."""

    def __init__(self, controller: FloorheatController) -> None:
        super().__init__(controller, "schedule_zone")
        self._attr_options = [ALL_ZONES, *(zone.name for zone in controller.config.core.zones)]

    @property
    def current_option(self) -> str:
        return self.controller.form.zone

    async def async_select_option(self, option: str) -> None:
        self.controller.form.zone = option
        self.async_write_ha_state()


class DaysSelect(FormEntity, SelectEntity):
    """Once (the form's date), every day, Monday to Friday, Saturday and Sunday, or one
    weekday."""

    _attr_options = list(DAY_OPTIONS)  # noqa: RUF012 - HA's attribute convention

    def __init__(self, controller: FloorheatController) -> None:
        super().__init__(controller, "schedule_days")

    @property
    def current_option(self) -> str:
        return self.controller.form.days

    async def async_select_option(self, option: str) -> None:
        self.controller.form.days = option
        self.async_write_ha_state()


class ExistingScheduleSelect(FormEntity, SelectEntity):
    """The schedules by label; the chosen one is deleted by "Delete schedule"."""

    def __init__(self, controller: FloorheatController, unit: TemperatureUnit) -> None:
        super().__init__(controller, "existing_schedule")
        self._unit = unit

    def _labels(self) -> dict[str, str]:
        config = self.controller.config.core
        return {
            schedule.id: schedule_label(schedule, config, self._unit)
            for schedule in self.controller.settings.schedules
        }

    @property
    def options(self) -> list[str]:
        return list(self._labels().values())

    @property
    def current_option(self) -> str | None:
        schedule_id = selected_schedule(self.controller)
        return None if schedule_id is None else self._labels()[schedule_id]

    async def async_select_option(self, option: str) -> None:
        by_label = {label: schedule_id for schedule_id, label in self._labels().items()}
        self.controller.form.selected = by_label[option]
        self.async_write_ha_state()
