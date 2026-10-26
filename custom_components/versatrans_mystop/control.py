"""Local account controls remain available during API outages."""
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, NAME


class AccountControl(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, key):
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_account_{key}"
        # Display names come from the "entity" section of strings.json.
        self._attr_translation_key = key

    @property
    def available(self):
        return True

    @property
    def extra_state_attributes(self):
        options = self.coordinator.entry.options
        return {"polling_mode": options.get("polling_mode", "scheduled"),
                "manual_until": options.get("manual_until") if options.get("polling_mode") == "manual" else None}

    @property
    def device_info(self):
        entry = self.coordinator.entry
        return DeviceInfo(identifiers={(DOMAIN, f"{entry.entry_id}_account")},
            name=f"{entry.data.get('district_name', NAME)} polling controls",
            model="District account")
