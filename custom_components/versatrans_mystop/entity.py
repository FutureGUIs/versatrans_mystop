"""Shared device identities and dynamic student discovery.

Each config entry has one school device holding the polling controls. Every
student is a child device linked to it with via_device.
"""
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, NAME

MANUFACTURER = "Versatrans My Stop"


def school_identifier(entry):
    # Same identifier as the earlier "polling controls" device, so upgrades
    # rename that device in place instead of creating a new one.
    return (DOMAIN, f"{entry.entry_id}_account")


def school_device_info(entry):
    return DeviceInfo(identifiers={school_identifier(entry)},
                      name=entry.data.get("district_name") or getattr(entry, "title", None) or NAME,
                      manufacturer=MANUFACTURER, model="School")


def register_school_device(hass, entry):
    """Create the parent device before students reference it with via_device."""
    dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **school_device_info(entry))


class StudentEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, student_id, key):
        super().__init__(coordinator)
        self.student_id = student_id
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{student_id}_{key}"
        # Display names come from the "entity" section of strings.json.
        self._attr_translation_key = key

    @property
    def student(self):
        return self.coordinator.data.get(self.student_id)

    @property
    def available(self):
        return super().available and self.student is not None

    @property
    def device_info(self):
        entry = self.coordinator.entry
        return DeviceInfo(identifiers={(DOMAIN, f"{entry.entry_id}_{self.student_id}")},
                          name=self.student.name if self.student else "Student",
                          manufacturer=MANUFACTURER, model="Student",
                          via_device=school_identifier(entry))


def setup_students(entry, async_add_entities, factory):
    coordinator = entry.runtime_data
    known = set()

    def add_new():
        new = set(coordinator.data) - known
        if new:
            async_add_entities([entity for sid in sorted(new) for entity in factory(coordinator, sid)])
            known.update(new)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))
