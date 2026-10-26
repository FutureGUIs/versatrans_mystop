"""Account-wide manual polling override and return to schedule."""
from homeassistant.components.button import ButtonEntity
from .control import AccountControl

MODES = {"manual": "mdi:play", "paused": "mdi:stop", "scheduled": "mdi:calendar-clock"}


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([PollingButton(entry.runtime_data, mode) for mode in MODES])


class PollingButton(AccountControl, ButtonEntity):
    def __init__(self, coordinator, mode):
        super().__init__(coordinator, f"polling_{mode}")
        self.mode = mode
        self._attr_icon = MODES[mode]

    async def async_press(self):
        # Start polling lasts a limited time; pressing it again restarts the timer.
        until = self.coordinator.manual_polling_until() if self.mode == "manual" else None
        await self.coordinator.async_change_options({"polling_mode": self.mode, "manual_until": until})
