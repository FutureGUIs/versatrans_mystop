"""Shared student device identity and dynamic discovery."""
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN


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
        return DeviceInfo(identifiers={(DOMAIN, f"{self.coordinator.entry.entry_id}_{self.student_id}")},
                          name=self.student.name if self.student else "Student",
                          model="Student bus tracking")


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
