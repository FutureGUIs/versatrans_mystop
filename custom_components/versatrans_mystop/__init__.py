"""Versatrans My Stop integration."""
from datetime import datetime, timedelta
import logging
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.storage import Store
from .api import ApiError, AuthError
from .const import DOMAIN, DEFAULT_INTERVAL, DEFAULT_STALE, MANUAL_POLLING_HOURS
from .tls import create_client
from .cleanup import remove_retired_entities
from .cache import account_snapshot

PLATFORMS = [Platform.DEVICE_TRACKER, Platform.SENSOR, Platform.BINARY_SENSOR, Platform.NUMBER, Platform.BUTTON]
LOGGER = logging.getLogger(__name__)


class PollingCoordinator(DataUpdateCoordinator):
    def __init__(self, hass, entry, client):
        super().__init__(hass, LOGGER, name=DOMAIN, config_entry=entry,
                         update_interval=timedelta(seconds=entry.options.get("scan_interval", DEFAULT_INTERVAL)))
        self.entry = entry
        self.client = client
        self._applied_options = dict(entry.options)
        self.client.options = dict(entry.options)
        self.schedule_store = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.schedule")
        self._cached_snapshot = None

    async def async_change_options(self, updates):
        self.hass.config_entries.async_update_entry(self.entry, options={**self.entry.options, **updates})
        await self.async_options_updated()

    async def async_options_updated(self):
        if self._applied_options == dict(self.entry.options):
            return
        self._applied_options = dict(self.entry.options)
        self.client.options = dict(self.entry.options)
        self.update_interval = timedelta(seconds=self.entry.options.get("scan_interval", DEFAULT_INTERVAL))
        self.async_update_listeners()
        await self.async_request_refresh()

    def manual_polling_until(self, now=None):
        """Start an override that returns to scheduled polling on its own."""
        now = now or self.client.now()
        return (now + timedelta(hours=MANUAL_POLLING_HOURS)).isoformat()

    def _expire_manual_polling(self):
        """Return to scheduled polling once a manual override has run its course."""
        options = self.entry.options
        if options.get("polling_mode") != "manual":
            return
        now = self.client.now()
        try:
            until = datetime.fromisoformat(options.get("manual_until"))
        except (TypeError, ValueError):
            until = None
        if until is not None and until.tzinfo is not None and now < until:
            return
        if until is None or until.tzinfo is None:
            # Manual mode started before this limit existed: begin it now.
            updated = {**options, "manual_until": self.manual_polling_until(now)}
        else:
            updated = {**options, "polling_mode": "scheduled", "manual_until": None}
            LOGGER.info("Manual polling ended after %s hours; returning to scheduled polling",
                        MANUAL_POLLING_HOURS)
        # Apply directly so the options listener does not request a second refresh.
        self._applied_options = dict(updated)
        self.client.options = dict(updated)
        self.hass.config_entries.async_update_entry(self.entry, options=updated)

    async def _async_update_data(self):
        self._expire_manual_polling()
        try:
            data = await self.client.fetch(self.entry.options.get("stale_after", DEFAULT_STALE))
        except AuthError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from None
        except ApiError as exc:
            await self.async_store_schedule()
            raise UpdateFailed(str(exc)) from None
        await self.async_store_schedule()
        return data

    async def async_store_schedule(self):
        snapshot = account_snapshot(self.client.schedule_account(), self.entry.data["timezone"])
        if snapshot and snapshot != self._cached_snapshot:
            try:
                await self.schedule_store.async_save(snapshot)
                self._cached_snapshot = snapshot
            except OSError:
                LOGGER.warning("Integration could not save the local schedule cache")


async def async_setup_entry(hass, entry):
    coordinator = PollingCoordinator(hass, entry, await create_client(hass, entry.data))
    cached = account_snapshot(await coordinator.schedule_store.async_load(), entry.data["timezone"])
    coordinator._cached_snapshot = cached
    if cached:
        coordinator.client.account = cached
        coordinator.client.remember_schedules(cached)
    try:
        await coordinator.async_config_entry_first_refresh()
    except ConfigEntryNotReady:
        usable = account_snapshot(coordinator.client.account, entry.data["timezone"]) or cached
        if not usable:
            raise
        # Cached records contain no vehicle, GPS or session. Expose their
        # schedule sensors while live entities remain unavailable and retry.
        coordinator.client.account = usable
        coordinator.client.sid = None
        coordinator.client.authenticated_at = None
        coordinator.client.schedule_day = None
        coordinator.data = await coordinator.client._fetch(
            entry.options.get("stale_after", DEFAULT_STALE), coordinator.client.plans())
        coordinator.last_update_success = False
        LOGGER.info("Integration is using cached schedules while the district API is unavailable")
    entry.runtime_data = coordinator
    remove_retired_entities(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(async_reload))
    return True


async def async_reload(hass, entry):
    await entry.runtime_data.async_options_updated()


async def async_unload_entry(hass, entry):
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_entry(hass, entry):
    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.schedule").async_remove()
