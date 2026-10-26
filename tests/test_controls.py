"""Exercise the real option-update and control code with HA test doubles."""
import asyncio
import importlib
import sys
import types
from pathlib import Path
from types import SimpleNamespace
import pytest
from test_protocol import FakeClient


@pytest.fixture
def controls(monkeypatch):
    def module(name, **attrs):
        mod=types.ModuleType(name);mod.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules,name,mod)
    class Error(Exception):pass
    class NotReady(Exception):pass
    class Entity:pass
    class CoordinatorEntity(Entity):
        def __init__(self,coordinator):self.coordinator=coordinator
    class Coordinator:
        def __init__(self,hass,logger,**kwargs):
            self.hass=hass;self.update_interval=kwargs['update_interval']
            self.refreshes=0;self.notifications=0;self.last_update_success=False
        def async_update_listeners(self):self.notifications+=1
        async def async_request_refresh(self):
            self.refreshes+=1;self.data=await self._async_update_data()
        async def async_config_entry_first_refresh(self):
            try:self.data=await self._async_update_data();self.last_update_success=True
            except Error as exc:raise NotReady from exc
    class Store:
        def __init__(self,hass,*args):self.hass=hass;self.saved=[]
        async def async_load(self):return getattr(self.hass,'cached',None)
        async def async_save(self,data):self.saved.append(data)
        async def async_remove(self):self.hass.cache_removed=True
    module('homeassistant')
    module('homeassistant.helpers')
    module('homeassistant.components')
    module('homeassistant.const',Platform=SimpleNamespace(DEVICE_TRACKER='device_tracker',SENSOR='sensor',
        BINARY_SENSOR='binary_sensor',NUMBER='number',BUTTON='button'),EntityCategory=SimpleNamespace(CONFIG='config'))
    module('homeassistant.exceptions',ConfigEntryAuthFailed=Error,ConfigEntryNotReady=NotReady,HomeAssistantError=Error)
    module('homeassistant.helpers.aiohttp_client',async_get_clientsession=lambda hass:None)
    module('homeassistant.helpers.update_coordinator',DataUpdateCoordinator=Coordinator,
        UpdateFailed=Error,CoordinatorEntity=CoordinatorEntity)
    module('homeassistant.helpers.device_registry',DeviceInfo=dict)
    module('homeassistant.helpers.storage',Store=Store)
    module('homeassistant.components.number',NumberEntity=type('NumberEntity',(Entity,),{}),
        NumberMode=SimpleNamespace(BOX='box'))
    module('homeassistant.components.button',ButtonEntity=type('ButtonEntity',(Entity,),{}))
    runtime=types.ModuleType('mystop_test.runtime');runtime.__package__='mystop_test'
    source=Path(__file__).parents[1]/'custom_components/versatrans_mystop/__init__.py'
    exec(compile(source.read_text(),str(source),'exec'),runtime.__dict__)
    modules=[importlib.import_module('mystop_test.'+name) for name in ('control','number','button')]
    entry=SimpleNamespace(entry_id='test',data={'district_name':'Synthetic','timezone':'America/Detroit'},options={'scan_interval':60,'show_stale':False})
    updates=[]
    def update_entry(entry, **kwargs):
        updates.append(kwargs);entry.options=kwargs['options']
    hass=SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update_entry))
    coordinator=runtime.PollingCoordinator(hass,entry,FakeClient())
    entry.runtime_data=coordinator
    yield runtime,modules,coordinator,updates,Error
    for name in ('control','number','button'):sys.modules.pop('mystop_test.'+name,None)


def test_editable_margins_preserve_options_and_apply_without_reload(controls):
    runtime,(_,number,_),c,updates,Error=controls
    before=number.PollingWindowNumber(c,'poll_before');after=number.PollingWindowNumber(c,'poll_after')
    assert before.available and after.available
    assert before.native_value==15 and after.native_value==30
    asyncio.run(before.async_set_native_value(45))
    assert before.native_value==45 and c.client.options['poll_before']==45
    assert c.entry.options['show_stale'] is False
    assert c.refreshes==1 and c.notifications==1
    # HA's separately invoked options listener must not refresh twice.
    asyncio.run(runtime.async_reload(c.hass,c.entry))
    assert c.refreshes==1
    for value in (-1,241,2.5,float('nan')):
        with pytest.raises(Error):asyncio.run(after.async_set_native_value(value))
    assert len(updates)==1


def test_account_buttons_persist_mode_and_share_control_device(controls):
    _,(_,number,button),c,_,_=controls
    start=button.PollingButton(c,'manual');stop=button.PollingButton(c,'paused')
    resume=button.PollingButton(c,'scheduled')
    for control in (start,stop,resume):
        assert control.available
        asyncio.run(control.async_press())
        assert c.entry.options['polling_mode']==control.mode
        assert c.client.options['polling_mode']==control.mode
    assert c.refreshes==3
    assert start.device_info==number.PollingWindowNumber(c,'poll_before').device_info
    assert c.data['1'].polling_status=='polling'


def test_control_platforms_add_one_shared_set(controls):
    _,(_,number,button),c,_,_=controls
    c.entry.runtime_data=c;added=[]
    asyncio.run(number.async_setup_entry(c.hass,c.entry,added.extend))
    asyncio.run(button.async_setup_entry(c.hass,c.entry,added.extend))
    assert len(added)==5
    assert len({x._attr_unique_id for x in added})==5


@pytest.mark.parametrize('login_works',[False,True])
def test_cached_setup_keeps_live_entities_failed_and_retries(controls,monkeypatch,login_works):
    from test_protocol import account
    from mystop_test.cache import account_snapshot
    from mystop_test.api import ApiError
    runtime,_,original,_,_=controls
    class Offline(FakeClient):
        async def request(self,path,body=None,*,login=False):
            self.calls.append((path,body,login))
            if login and login_works:return account()
            raise ApiError('API unavailable','connection_error')
    offline=Offline()
    async def create(hass,data):return offline
    monkeypatch.setattr(runtime,'create_client',create)
    monkeypatch.setattr(runtime,'remove_retired_entities',lambda *args:None)
    hass=original.hass;hass.cached=account_snapshot(account(),'America/Detroit')
    forwarded=[]
    async def forward(entry,platforms):forwarded.extend(platforms)
    hass.config_entries.async_forward_entry_setups=forward
    entry=original.entry
    entry.async_on_unload=lambda callback:None
    entry.add_update_listener=lambda callback:(lambda:None)
    assert asyncio.run(runtime.async_setup_entry(hass,entry)) is True
    assert entry.runtime_data.last_update_success is False
    assert entry.runtime_data.data['1'].position is None
    assert entry.runtime_data.data['1'].afternoon=='15:36'
    assert entry.runtime_data.client.sid is None
    assert 'MatchedBusData' not in entry.runtime_data.client.account['Students'][0]
    # A failed vehicle request is not retried while constructing offline data.
    assert sum(not login for _,_,login in offline.calls)==(1 if login_works else 0)
    assert 'button' in forwarded and 'number' in forwarded
    asyncio.run(runtime.async_remove_entry(hass,entry))
    assert hass.cache_removed


def test_start_polling_returns_to_schedule_after_three_hours(controls):
    from datetime import timedelta
    from test_protocol import NOW
    _,(_,_,button),c,_,_=controls
    start=button.PollingButton(c,'manual')
    asyncio.run(start.async_press())
    assert c.entry.options['manual_until']==(NOW+timedelta(hours=3)).isoformat()
    assert start.extra_state_attributes['manual_until']==c.entry.options['manual_until']
    c.client.now=lambda:NOW+timedelta(hours=2,minutes=59)
    asyncio.run(c.async_request_refresh())
    assert c.entry.options['polling_mode']=='manual'
    c.client.now=lambda:NOW+timedelta(hours=3)
    asyncio.run(c.async_request_refresh())
    assert c.entry.options['polling_mode']=='scheduled' and c.entry.options['manual_until'] is None
    assert c.client.options['polling_mode']=='scheduled'
    assert start.extra_state_attributes=={'polling_mode':'scheduled','manual_until':None}
    # Stop and Resume clear any timer; pressing Start again restarts it.
    asyncio.run(button.PollingButton(c,'paused').async_press())
    assert c.entry.options['manual_until'] is None


def test_manual_mode_from_older_version_gets_a_timer(controls):
    from datetime import timedelta
    from test_protocol import NOW
    _,_,c,_,_=controls
    c.entry.options={**c.entry.options,'polling_mode':'manual'}
    asyncio.run(c.async_request_refresh())
    assert c.entry.options['polling_mode']=='manual'
    assert c.entry.options['manual_until']==(NOW+timedelta(hours=3)).isoformat()
