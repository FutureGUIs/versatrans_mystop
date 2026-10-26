"""Remove retired entities from this account's registry on upgrade."""
from .const import DOMAIN

RETIRED_FIELDS = {"sensor": ("age", "status", "scheduled"), "binary_sensor": ("fresh",)}


def remove_retired_entities(hass, entry):
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    prefix = f"{entry.entry_id}_"
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        fields = RETIRED_FIELDS.get(entity.domain, ())
        # These retained keys also end in "_status"; match them before the old
        # GPS status key so upgrade cleanup preserves schedule diagnostics.
        if entity.unique_id.endswith(("_polling_status", "_schedule_status")):
            continue
        if (entity.platform == DOMAIN and entity.unique_id.startswith(prefix)
                and any(entity.unique_id.endswith(f"_{key}") for key in fields)):
            registry.async_remove(entity.entity_id)
