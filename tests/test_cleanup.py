"""Upgrade cleanup must respect account ownership and renamed entity IDs."""
import sys
import types
from types import SimpleNamespace
from mystop_test.cleanup import remove_retired_entities


def test_cleanup_scoped_and_idempotent(monkeypatch):
    def row(entity_id, key, account='account', platform='versatrans_mystop'):
        return SimpleNamespace(entity_id=entity_id, domain=entity_id.split('.')[0],
            unique_id=f'{account}_student_{key}', config_entry_id=account, platform=platform)
    rows=[row('sensor.renamed_gps_age','age'), row('sensor.renamed_gps_status','status'),
          row('sensor.old_stop','scheduled'), row('binary_sensor.renamed_fresh','fresh'),
          row('sensor.pickup','morning'), row('sensor.dropoff','afternoon'),
          row('device_tracker.bus','bus_location'), row('binary_sensor.active','active'),
          row('sensor.last_update','last_seen'), row('sensor.schedule_status','schedule_status'),
          row('sensor.polling_status','polling_status'), row('sensor.next_window','next_polling_window'),
          row('sensor.other_account_age','age','other'),
          row('sensor.other_platform_age','age',platform='other_integration')]
    removed=[]
    class Registry:
        def async_remove(self, entity_id):
            removed.append(entity_id)
            rows[:]=[r for r in rows if r.entity_id!=entity_id]
    registry=Registry()
    er=SimpleNamespace(async_get=lambda hass:registry,
        async_entries_for_config_entry=lambda reg, entry_id:[r for r in rows if r.config_entry_id==entry_id])
    helpers=types.ModuleType('homeassistant.helpers');helpers.entity_registry=er
    monkeypatch.setitem(sys.modules,'homeassistant',types.ModuleType('homeassistant'))
    monkeypatch.setitem(sys.modules,'homeassistant.helpers',helpers)
    entry=SimpleNamespace(entry_id='account')
    remove_retired_entities(None,entry)
    assert set(removed)=={'sensor.renamed_gps_age','sensor.renamed_gps_status',
                         'sensor.old_stop','binary_sensor.renamed_fresh'}
    remove_retired_entities(None,entry)
    assert len(removed)==4
