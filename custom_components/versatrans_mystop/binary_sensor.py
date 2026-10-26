"""Per-student server route-active flag."""
from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import EntityCategory
from .entity import StudentEntity, setup_students


async def async_setup_entry(hass, entry, async_add_entities):
    setup_students(entry, async_add_entities, lambda c, sid: [BusBinary(c, sid, "active")])


class BusBinary(StudentEntity, BinarySensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:map-marker-path"

    def __init__(self, coordinator, student_id, key):
        super().__init__(coordinator, student_id, key)
        self.key = key

    @property
    def is_on(self):
        if not self.student:
            return None
        return self.student.active
