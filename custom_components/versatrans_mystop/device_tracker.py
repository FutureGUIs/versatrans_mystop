"""GPS map tracker for each student's assigned bus."""
from homeassistant.components.device_tracker import TrackerEntity
from homeassistant.components.device_tracker.const import SourceType
from .entity import StudentEntity, setup_students


async def async_setup_entry(hass, entry, async_add_entities):
    setup_students(entry, async_add_entities, lambda c, sid: [BusTracker(c, sid)])


class BusTracker(StudentEntity, TrackerEntity):
    _attr_icon = "mdi:bus-school"

    def __init__(self, coordinator, student_id):
        super().__init__(coordinator, student_id, "bus_location")

    @property
    def source_type(self):
        return SourceType.GPS

    @property
    def available(self):
        if not super().available or self.student.position is None or self.student.status == "clock_mismatch":
            return False
        return self.coordinator.entry.options.get("show_stale", True) or self.student.status == "fresh"

    @property
    def latitude(self):
        return self.student.position.latitude if self.student and self.student.position else None

    @property
    def longitude(self):
        return self.student.position.longitude if self.student and self.student.position else None

    @property
    def extra_state_attributes(self):
        student = self.student
        if not student:
            return {}
        return {"vehicle_id": student.vehicle, "gps_status": student.status,
                "last_gps_update": student.position.timestamp.isoformat() if student.position else None,
                "route_active": student.active, "location_represents": "assigned_bus_not_student"}
