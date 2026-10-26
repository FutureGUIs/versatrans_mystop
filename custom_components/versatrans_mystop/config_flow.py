"""UI setup, options, and credential repair."""
import hashlib
import logging
import uuid
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector
from .api import AuthError, ApiError
from .tls import create_client
from .districts import load_directory
from .const import DOMAIN, NAME, DEFAULT_URL, DEFAULT_SCHOOL, DEFAULT_TIMEZONE, DEFAULT_INTERVAL, DEFAULT_STALE, DEFAULT_BEFORE, DEFAULT_AFTER

LOGGER = logging.getLogger(__name__)


def schema(defaults=None, manual=True):
    d = defaults or {}
    fields = {
        vol.Required("username", default=d.get("username", "")): str,
        vol.Required("password"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        vol.Required("timezone", default=d.get("timezone", DEFAULT_TIMEZONE)): str,
    }
    if manual:
        fields[vol.Required("url", default=d.get("url", DEFAULT_URL))] = str
        fields[vol.Required("school_guid", default=d.get("school_guid", DEFAULT_SCHOOL))] = str
    return vol.Schema(fields)


class DistrictConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def validate(self, data):
        try:
            parsed = urlsplit(data["url"])
            parsed.port
        except ValueError:
            return "invalid_url"
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            return "invalid_url"
        try:
            ZoneInfo(data["timezone"])
        except (ZoneInfoNotFoundError, ValueError):
            return "invalid_timezone"
        if not data["username"].strip() or not data["password"]:
            return "invalid_auth"
        if any(ord(c) > 127 for c in data["password"]):
            return "unsupported_password"
        try:
            client = await create_client(self.hass, data)
            await client.authenticate()
        except AuthError:
            return "invalid_auth"
        except ApiError as exc:
            # Our fixed messages/status codes contain no credentials or server body.
            LOGGER.warning("Integration setup failed: %s", str(exc))
            return exc.code
        return None

    async def async_step_user(self, user_input=None):
        if not hasattr(self, "_districts"):
            self._districts, self._directory_fallback = await load_directory(self.hass)
        errors = {}
        if user_input is not None:
            chosen = user_input.get("district")
            if chosen == "manual":
                self._selected_district = None
                return await self.async_step_manual()
            if chosen in self._districts:
                self._selected_district = self._districts[chosen]
                if self._selected_district["url"].startswith("https://"):
                    return await self.async_step_credentials()
                errors["base"] = "district_http_only"
            else:
                errors["base"] = "invalid_district"
        options = [{"value": key, "label": row["label"]} for key, row in self._districts.items()]
        options.sort(key=lambda item: item["label"].casefold())
        options.append({"value": "manual", "label": "Manual setup (URL and GUID)"})
        return self.async_show_form(step_id="user", data_schema=vol.Schema({
            vol.Required("district"): selector.SelectSelector(selector.SelectSelectorConfig(
                options=options, mode=selector.SelectSelectorMode.DROPDOWN))
        }), errors=errors, description_placeholders={
            "directory_status": "Using the bundled district list (live directory unavailable)." if self._directory_fallback else "Using the current district directory."})

    async def async_step_credentials(self, user_input=None):
        return await self._async_account_step(user_input, manual=False)

    async def async_step_manual(self, user_input=None):
        return await self._async_account_step(user_input, manual=True)

    async def _async_account_step(self, user_input, manual):
        errors = {}
        selected = {} if manual else self._selected_district
        if user_input is not None:
            data = {**user_input, "device_id": str(uuid.uuid4())}
            if not manual:
                data.update({key: selected[key] for key in ("url", "school_guid", "district_id", "district_name")})
            data["username"] = data["username"].strip()
            data["url"] = data["url"].strip().rstrip("/") + "/"
            data["school_guid"] = data["school_guid"].strip()
            unique = hashlib.sha256((data["url"].lower() + "|" + data["school_guid"] + "|" + data["username"].lower()).encode()).hexdigest()
            await self.async_set_unique_id(unique)
            self._abort_if_unique_id_configured()
            error = await self.validate(data)
            if error is None:
                return self.async_create_entry(title=data.get("district_name", NAME), data=data)
            errors["base"] = error
        defaults = {"timezone": self.hass.config.time_zone, **(user_input or {})}
        return self.async_show_form(step_id="manual" if manual else "credentials", data_schema=schema(defaults, manual), errors=errors,
                                    description_placeholders={"district": selected.get("district_name", "")})

    async def async_step_reauth(self, entry_data):
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        errors = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            data = {**entry.data, **user_input}
            error = await self.validate(data)
            if error is None:
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
            errors["base"] = error
        return self.async_show_form(step_id="reauth_confirm", data_schema=vol.Schema({
            vol.Required("password"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))}), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return PollingOptionsFlow()


class PollingOptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data={**self.config_entry.options, **user_input})
        opts = self.config_entry.options
        return self.async_show_form(step_id="init", data_schema=vol.Schema({
            vol.Required("scan_interval", default=opts.get("scan_interval", DEFAULT_INTERVAL)): vol.All(vol.Coerce(int), vol.Range(min=30, max=900)),
            vol.Required("stale_after", default=opts.get("stale_after", DEFAULT_STALE)): vol.All(vol.Coerce(int), vol.Range(min=60, max=3600)),
            vol.Required("show_stale", default=opts.get("show_stale", True)): bool,
            vol.Required("poll_before", default=opts.get("poll_before", DEFAULT_BEFORE)): vol.All(vol.Coerce(int), vol.Range(min=0, max=240)),
            vol.Required("poll_after", default=opts.get("poll_after", DEFAULT_AFTER)): vol.All(vol.Coerce(int), vol.Range(min=0, max=240)),
        }))
