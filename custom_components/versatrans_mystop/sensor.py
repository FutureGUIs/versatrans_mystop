"""Per-student bus, schedule, and last-update sensors."""
from datetime import datetime, timedelta, timezone
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, RestoreSensor
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.core import callback
from homeassistant.const import EntityCategory
from .entity import StudentEntity, setup_students
from .const import DEFAULT_TIMEZONE, DEFAULT_BEFORE, DEFAULT_AFTER
from .model import get
from .schedule import plan, schedule_snapshot

STOP_FIELDS = ("morning", "afternoon")


FIELDS = ("vehicle", "last_seen", "morning", "afternoon", "polling_status",
          "next_polling_window", "schedule_status")
# Troubleshooting values: shown under Diagnostic on the device page and left
# out of auto-generated dashboards. They can still be added to any card.
DIAGNOSTIC_FIELDS = ("polling_status", "next_polling_window", "schedule_status")
ICONS = {"vehicle": "mdi:bus-school", "last_seen": "mdi:crosshairs-gps", "morning": "mdi:bus-clock",
         "afternoon": "mdi:home-clock", "polling_status": "mdi:sync", "next_polling_window": "mdi:calendar-clock",
         "schedule_status": "mdi:calendar-check"}


async def async_setup_entry(hass, entry, async_add_entities):
    setup_students(entry, async_add_entities, lambda c, sid: [
        StopTimeSensor(c, sid, key) if key in STOP_FIELDS else BusSensor(c, sid, key) for key in FIELDS])


class BusSensor(StudentEntity, SensorEntity):
    # Change on every refresh; keep them out of the recorder's attribute history.
    _unrecorded_attributes = frozenset({"last_local_check", "last_login"})

    def __init__(self, coordinator, student_id, key):
        super().__init__(coordinator, student_id, key)
        self.key = key
        self._attr_icon = ICONS[key]
        self._attr_device_class = None
        if key in DIAGNOSTIC_FIELDS:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        if key in ("last_seen", "next_polling_window"):
            self._attr_device_class = SensorDeviceClass.TIMESTAMP

    @property
    def native_value(self):
        s = self.student
        if not s:
            return None
        if self.key == "last_seen":
            return s.position.timestamp if s.position else None
        return getattr(s, self.key)

    @property
    def extra_state_attributes(self):
        if self.key == "polling_status":
            client = getattr(self.coordinator, "client", None)
            check = getattr(client, "last_check", None)
            login = getattr(client, "authenticated_at", None)
            options = self.coordinator.entry.options
            return {"polling_mode": options.get("polling_mode", "scheduled"),
                    "manual_until": options.get("manual_until") if options.get("polling_mode") == "manual" else None,
                    "last_local_check": check.isoformat() if check else None,
                    "last_login": login.isoformat() if login else None,
                    "poll_before": self.coordinator.entry.options.get("poll_before", DEFAULT_BEFORE),
                    "poll_after": self.coordinator.entry.options.get("poll_after", DEFAULT_AFTER)}
        if self.key == "schedule_status" and self.student:
            client = getattr(self.coordinator, "client", None)
            students = get(getattr(client, "account", None), "Students", []) or []
            raw = next((s for s in students if str(get(s, "RecordID")) == self.student_id), {})
            routes = get(raw, "Routes", []) or []
            return {"api_service_days": [str(get(route, "Days", "") or "") for route in routes]
                    if isinstance(routes, list) else []}
        return None


class StopTimeSensor(StudentEntity, RestoreSensor):
    """Next service-day occurrence, available independently of live GPS/API."""
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    # Restore data for this entity, not history worth keeping in the recorder.
    _unrecorded_attributes = frozenset({"schedule_cache", "schedule_preferences"})

    def __init__(self, coordinator, student_id, key):
        super().__init__(coordinator, student_id, key)
        self.key = key
        self._attr_icon = ICONS[key]
        self._schedule = None
        self._preferences = {}
        self._using_cached_rules = False

    @property
    def district_timezone(self):
        return self.coordinator.entry.data.get("timezone", DEFAULT_TIMEZONE)

    def _update_schedule(self):
        client = getattr(self.coordinator, "client", None)
        account = getattr(client, "account", None)
        raw = next((s for s in (get(account, "Students", []) or [])
                    if str(get(s, "RecordID")) == self.student_id), None)
        if raw is None:
            return
        if hasattr(client, "schedule_record"):
            raw, self._using_cached_rules = client.schedule_record(raw)
        snapshot = schedule_snapshot(raw, self.district_timezone)
        if snapshot:
            self._schedule = snapshot
            preferences = client.schedule_preferences() if hasattr(client, "schedule_preferences") else get(account, "Preferences") or {}
            self._preferences = {key: get(preferences, key) for key in (
                "MSRouteShiftMinutes", "MSRouteShiftStart", "MSRouteShiftEnd")}

    def _plan(self):
        self._update_schedule()
        client = getattr(self.coordinator, "client", None)
        now = client.now() if client else datetime.now(timezone.utc)
        return plan(self._schedule or {}, now, self.district_timezone, self._preferences)

    @property
    def available(self):
        # Stays available through API outages (coordinator data is retained),
        # but a student the district no longer returns becomes unavailable.
        return self.student is not None and self.native_value is not None

    @property
    def native_value(self):
        result = self._plan()
        return result.next_morning if self.key == "morning" else result.next_afternoon

    @property
    def extra_state_attributes(self):
        result = self._plan()
        value = result.morning if self.key == "morning" else result.afternoon
        return {
            "scheduled_times": value.split(", ") if value else [],
            "district_timezone": self.district_timezone,
            "multiple_scheduled_times": bool(value and "," in value),
            "schedule_cache": self._schedule,
            "schedule_preferences": self._preferences,
            "service_date_confirmed": False,
            "uses_cached_schedule": self._using_cached_rules or not self.coordinator.last_update_success,
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self._update_schedule()
        if not self._schedule and (state := await self.async_get_last_state()):
            snapshot = state.attributes.get("schedule_cache")
            self._schedule = schedule_snapshot(snapshot, self.district_timezone)
            preferences = state.attributes.get("schedule_preferences")
            self._preferences = preferences if isinstance(preferences, dict) else {}
        self.async_on_remove(async_track_time_interval(self.hass, self._async_tick, timedelta(minutes=1)))

    @callback
    def _async_tick(self, now):
        self.async_write_ha_state()
