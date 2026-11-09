"""Local account controls remain available during API outages."""
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .entity import school_device_info


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
        return school_device_info(self.coordinator.entry)
