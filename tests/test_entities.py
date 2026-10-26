"""Entity contract tests with minimal HA doubles, not a full HA runtime."""
import importlib
import sys
import types
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from mystop_test.model import Student, Position

@pytest.fixture
def entities(monkeypatch):
    def module(name, **attrs):
        m=types.ModuleType(name)
        for key,value in attrs.items():setattr(m,key,value)
        monkeypatch.setitem(sys.modules,name,m)
    class Entity:
        def async_on_remove(self, f):
            self.remove_callbacks = getattr(self, 'remove_callbacks', []) + [f]
        def async_write_ha_state(self):self.writes = getattr(self, 'writes', 0) + 1
    class CoordinatorEntity(Entity):
        def __init__(self,coordinator):self.coordinator=coordinator
        async def async_added_to_hass(self):pass
        @property
        def available(self):return self.coordinator.last_update_success
    class TrackerEntity(Entity):pass
    class SensorEntity(Entity):pass
    class RestoreSensor(SensorEntity):
        async def async_get_last_state(self):return getattr(self,'restored_state',None)
    class BinarySensorEntity(Entity):pass
    for name in ('homeassistant','homeassistant.helpers','homeassistant.components'):
        module(name)
    module('homeassistant.helpers.device_registry',DeviceInfo=dict)
    module('homeassistant.const',EntityCategory=SimpleNamespace(CONFIG='config',DIAGNOSTIC='diagnostic'))
    module('homeassistant.helpers.update_coordinator',CoordinatorEntity=CoordinatorEntity)
    module('homeassistant.core',callback=lambda f:f)
    ticks=[]
    def track(hass, action, interval):
        ticks.append((action, interval))
        return lambda:ticks.remove((action, interval))
    module('homeassistant.helpers.event',async_track_time_interval=track)
    module('homeassistant.components.device_tracker',TrackerEntity=TrackerEntity)
    module('homeassistant.components.device_tracker.const',SourceType=SimpleNamespace(GPS='gps'))
    module('homeassistant.components.sensor',SensorEntity=SensorEntity,RestoreSensor=RestoreSensor,SensorDeviceClass=SimpleNamespace(TIMESTAMP='timestamp',DURATION='duration',ENUM='enum'))
    module('homeassistant.components.binary_sensor',BinarySensorEntity=BinarySensorEntity)
    modules=[importlib.import_module('mystop_test.'+name) for name in ('entity','device_tracker','sensor','binary_sensor')]
    modules[2].test_ticks=ticks
    yield modules
    for name in ('entity','device_tracker','sensor','binary_sensor'):
        sys.modules.pop('mystop_test.'+name,None)

def coordinator():
    pos=Position(42.,-83.,datetime.now(timezone.utc))
    data={'1':Student('1','Synthetic One','65','3:36 PM',False,pos,'stale'),
          '2':Student('2','Synthetic Two','65','3:36 PM',False,pos,'stale'),
          '3':Student('3','Synthetic Three',None,None,None,None,'no_assigned_vehicle')}
    listeners=[]
    entry=SimpleNamespace(entry_id='entry-test',options={},data={'timezone':'America/Detroit'},async_on_unload=lambda f:None)
    c=SimpleNamespace(data=data,entry=entry,last_update_success=True)
    c.async_add_listener=lambda cb: listeners.append(cb) or (lambda:None)
    c.listeners=listeners
    entry.runtime_data=c
    return c

def test_separate_devices_shared_bus(entities):
    _,tracker,sensor,_=entities;c=coordinator()
    a,b=tracker.BusTracker(c,'1'),tracker.BusTracker(c,'2')
    assert a.device_info['identifiers']!=b.device_info['identifiers']
    assert a._attr_unique_id!=b._attr_unique_id
    assert (a.latitude,a.longitude)==(b.latitude,b.longitude)
    assert sensor.BusSensor(c,'1','vehicle').device_info==a.device_info
    assert a.source_type=='gps'

def test_stale_visibility_and_failed_fetch(entities):
    _,tracker,_,_=entities;c=coordinator();a=tracker.BusTracker(c,'1')
    assert a.available and a.extra_state_attributes['gps_status']=='stale'
    c.entry.options['show_stale']=False
    assert not a.available
    c.data['1'].status='fresh'
    assert a.available
    c.last_update_success=False
    assert not a.available
    assert not tracker.BusTracker(c,'3').available

def test_all_students_and_dynamic_discovery(entities):
    entity,tracker,_,_=entities;c=coordinator();added=[]
    entity.setup_students(c.entry,added.extend,lambda c,s:[tracker.BusTracker(c,s)])
    assert len(added)==3
    c.listeners[0]()
    assert len(added)==3
    c.data['4']=Student('4','New Student',None,None,None,None,'no_assigned_vehicle')
    c.listeners[0]()
    assert len(added)==4
    del c.data['1']
    assert not added[0].available

def test_sensor_values_and_independent_active_flag(entities):
    _,_,sensor,binary=entities;c=coordinator();c.data['1'].status='fresh'
    assert binary.BusBinary(c,'1','active').is_on is False
    assert sensor.BusSensor(c,'1','last_seen').native_value.tzinfo is not None
    assert sensor.BusSensor(c,'3','last_seen').native_value is None


def test_stop_timestamps_survive_outage_and_advance_to_next_weekday(entities):
    _,_,sensor,_=entities;c=coordinator()
    c.client=SimpleNamespace(account={'Students':[{'RecordID':1,'Routes':[
        {'Direction':'I','PickupTime':'07:53','Days':'MTWHF'},
        {'Direction':'O','DropoffTime':'15:36','Days':'MTWHF'}]}]},
        now=lambda:datetime(2026,10,1,14,tzinfo=timezone.utc))
    sensors={key:sensor.StopTimeSensor(c,'1',key) for key in ('morning','afternoon')}
    for key in ('morning','afternoon'):
        entity=sensors[key]
        assert entity._attr_device_class=='timestamp'
        assert entity.available
        assert entity.extra_state_attributes['district_timezone']=='America/Detroit'
    assert sensors['morning'].native_value.isoformat()=='2026-10-02T07:53:00-04:00'
    assert sensors['afternoon'].native_value.isoformat()=='2026-10-01T15:36:00-04:00'
    c.last_update_success=False;c.client.account=None
    assert not sensor.BusSensor(c,'1','vehicle').available
    c.client.now=lambda:datetime(2026,10,2,20,tzinfo=timezone.utc)
    for key in sensors:
        assert sensors[key].available
        assert sensors[key].native_value.date().isoformat()=='2026-10-05'


def test_stop_restore_rules_and_local_timer(entities):
    import asyncio
    _,_,sensor,_=entities;c=coordinator();c.last_update_success=False
    c.client=SimpleNamespace(account=None, now=lambda:datetime(2026,10,3,16,tzinfo=timezone.utc))
    entity=sensor.StopTimeSensor(c,'1','morning');entity.hass=object()
    entity.restored_state=SimpleNamespace(attributes={'schedule_cache':{'Routes':[
        {'Direction':'I','BusStopTime':'07:53','Days':'Mon Tue Wed Thu Fri'}]}})
    asyncio.run(entity.async_added_to_hass())
    assert entity.native_value.isoformat()=='2026-10-05T07:53:00-04:00'
    assert entity.available and len(sensor.test_ticks)==1
    action,interval=sensor.test_ticks[0]
    assert interval.total_seconds()==60
    action(None);assert entity.writes==1
    for cancel in entity.remove_callbacks:cancel()
    assert not sensor.test_ticks


def test_setup_only_exposes_retained_entities(entities):
    import asyncio
    _,_,sensor,binary=entities;c=coordinator();added=[]
    asyncio.run(sensor.async_setup_entry(None,c.entry,added.extend))
    asyncio.run(binary.async_setup_entry(None,c.entry,added.extend))
    assert len(added)==3*8
    assert {e.key for e in added}=={'vehicle','last_seen','morning','afternoon',
        'polling_status','next_polling_window','schedule_status','active'}


def test_schedule_status_exposes_only_day_codes(entities):
    _,_,sensor,_=entities;c=coordinator()
    c.client=SimpleNamespace(account={'SessionID':'private','Students':[
        {'RecordID':1,'FirstName':'private','Routes':[{'Days':'MTWHF','Bus':'private'}]},
        {'RecordID':2,'Routes':[{'Days':'Mon'}]}]})
    assert sensor.BusSensor(c,'1','schedule_status').extra_state_attributes=={'api_service_days':['MTWHF'],'schedule_issues':[]}


def test_polling_diagnostics_and_stop_sensor_share_cached_schedule(entities):
    from test_protocol import FakeClient, account, run, NOW
    _,_,sensor,_=entities;c=coordinator();client=FakeClient()
    run(client.authenticate())
    client.account=account('99');client.account['Students'][0]['Routes']=[]
    client.last_check=NOW;c.client=client
    stop=sensor.StopTimeSensor(c,'1','afternoon')
    assert stop.available and stop.native_value.hour==15
    assert stop.extra_state_attributes['uses_cached_schedule'] is True
    assert client.plans()['1'].status=='cached_schedule'
    attrs=sensor.BusSensor(c,'1','polling_status').extra_state_attributes
    assert attrs['polling_mode']=='scheduled' and attrs['poll_before']==15
    assert attrs['last_local_check']==NOW.isoformat()
    assert attrs['last_login']==client.authenticated_at.isoformat()


def test_diagnostic_entity_categories(entities):
    _,tracker,sensor,binary=entities;c=coordinator()
    diagnostic={'polling_status','next_polling_window','schedule_status'}
    for key in sensor.FIELDS:
        entity=sensor.StopTimeSensor(c,'1',key) if key in sensor.STOP_FIELDS else sensor.BusSensor(c,'1',key)
        expected='diagnostic' if key in diagnostic else None
        assert getattr(entity,'_attr_entity_category',None)==expected,key
    assert binary.BusBinary(c,'1','active')._attr_entity_category=='diagnostic'
    assert getattr(tracker.BusTracker(c,'1'),'_attr_entity_category',None) is None


def test_frequently_changing_attributes_are_not_recorded(entities):
    _,_,sensor,_=entities
    assert {'last_local_check','last_login'}<=sensor.BusSensor._unrecorded_attributes
    assert {'schedule_cache','schedule_preferences'}<=sensor.StopTimeSensor._unrecorded_attributes


def test_removed_student_stop_sensor_becomes_unavailable(entities):
    _,_,sensor,_=entities;c=coordinator()
    c.client=SimpleNamespace(account={'Students':[{'RecordID':1,'Routes':[
        {'Direction':'O','DropoffTime':'15:36','Days':'MTWHF'}]}]},
        now=lambda:datetime(2026,10,1,14,tzinfo=timezone.utc))
    stop=sensor.StopTimeSensor(c,'1','afternoon')
    assert stop.available
    c.client.account={'Students':[]};del c.data['1']
    assert not stop.available


def test_every_translation_key_has_an_english_name(entities):
    import json
    from pathlib import Path
    _,tracker,sensor,binary=entities;c=coordinator()
    root=Path(__file__).parents[1]/'custom_components'/'versatrans_mystop'
    strings=json.loads((root/'strings.json').read_text())
    assert strings==json.loads((root/'translations'/'en.json').read_text())
    names=strings['entity']
    entities_by_platform={'device_tracker':[tracker.BusTracker(c,'1')],'binary_sensor':[binary.BusBinary(c,'1','active')],
        'sensor':[sensor.StopTimeSensor(c,'1',k) if k in sensor.STOP_FIELDS else sensor.BusSensor(c,'1',k) for k in sensor.FIELDS]}
    for platform,items in entities_by_platform.items():
        for entity in items:
            assert names[platform][entity._attr_translation_key]['name']
            assert not hasattr(entity,'_attr_name')
    assert set(names['button'])=={'polling_manual','polling_paused','polling_scheduled'}
    assert set(names['number'])=={'poll_before','poll_after'}


def test_status_sensors_are_enums_with_plain_language_text(entities):
    import json
    from pathlib import Path
    _,_,sensor,_=entities;c=coordinator()
    strings=json.loads((Path(__file__).parents[1]/'custom_components'/'versatrans_mystop'/'strings.json').read_text())
    for key in ('polling_status','schedule_status'):
        entity=sensor.BusSensor(c,'1',key)
        assert entity._attr_device_class=='enum'
        text=strings['entity']['sensor'][key]['state']
        assert set(text)==set(entity._attr_options)
        assert all('_' not in label and label[0].isupper() for label in text.values())
    status=sensor.BusSensor(c,'1','schedule_status')
    c.data['1'].schedule_status='weekdays_assumed, unsupported_direction'
    assert status.native_value=='unsupported_direction'
    assert status.extra_state_attributes['schedule_issues']==['weekdays_assumed','unsupported_direction']
    c.data['1'].schedule_status='cached_schedule'
    assert status.native_value=='cached_schedule'
    c.data['1'].schedule_status='something new'
    assert status.native_value=='unknown'
    polling=sensor.BusSensor(c,'1','polling_status')
    c.data['1'].polling_status='request_failed'
    assert polling.native_value=='request_failed'
    c.data['1'].polling_status='future_value'
    assert polling.native_value=='unknown'
