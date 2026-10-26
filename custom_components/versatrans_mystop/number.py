"""Editable polling window margins, in minutes, for all students."""
from math import isfinite
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory
from homeassistant.exceptions import HomeAssistantError
from .control import AccountControl
from .const import DEFAULT_BEFORE, DEFAULT_AFTER

WINDOWS = {"poll_before": DEFAULT_BEFORE, "poll_after": DEFAULT_AFTER}


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([PollingWindowNumber(entry.runtime_data, key) for key in WINDOWS])


class PollingWindowNumber(AccountControl, NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_native_unit_of_measurement = "min"
    _attr_native_min_value = 0
    _attr_native_max_value = 240
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator, key):
        super().__init__(coordinator, key)
        self.key = key

    @property
    def native_value(self):
        return self.coordinator.entry.options.get(self.key, WINDOWS[self.key])

    async def async_set_native_value(self, value):
        if not isfinite(value) or not 0 <= value <= 240 or int(value) != value:
            raise HomeAssistantError("Enter a whole number of minutes from 0 through 240")
        await self.coordinator.async_change_options({self.key: int(value)})
