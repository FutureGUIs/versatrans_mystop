import asyncio
import importlib
import sys
import types
from types import SimpleNamespace

import pytest
from mystop_test.districts import bundled_directory, parse_directory, load_directory


def test_complete_snapshot_and_lake_orion():
    rows = bundled_directory()
    assert len(rows) == 316
    assert sum(r['url'].startswith('https:') for r in rows.values()) == 313
    lake = rows['1261']
    assert lake['url'] == 'https://mystop.lok12.org/MyStopWebAPI/'
    assert lake['school_guid'] == '32fc1664-8521-4825-a60f-d0059483b315'


def test_bad_directory():
    for payload in ([], {}, {'Clients': []}, {'Clients': [None, {'Account': 'Bad'}]}):
        with pytest.raises(ValueError):
            parse_directory(payload)


@pytest.fixture
def flow_module(monkeypatch):
    def module(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        monkeypatch.setitem(sys.modules, name, m)
        return m
    class Flow:
        def __init_subclass__(cls, **kwargs): pass
        def async_show_form(self, **kwargs): return kwargs
        def async_create_entry(self, **kwargs): return kwargs
        async def async_set_unique_id(self, value): self.unique = value
        def _abort_if_unique_id_configured(self): pass
    selectors = SimpleNamespace(TextSelector=lambda x: str, TextSelectorConfig=dict,
        TextSelectorType=SimpleNamespace(PASSWORD='password'), SelectSelector=lambda x: str,
        SelectSelectorConfig=dict, SelectSelectorMode=SimpleNamespace(DROPDOWN='dropdown'))
    module('homeassistant', config_entries=SimpleNamespace(ConfigFlow=Flow, OptionsFlow=Flow))
    module('homeassistant.core', callback=lambda x:x)
    module('homeassistant.helpers', selector=selectors)
    module('homeassistant.helpers.aiohttp_client', async_get_clientsession=lambda hass:hass.session)
    mod = importlib.import_module('mystop_test.config_flow')
    yield mod
    sys.modules.pop('mystop_test.config_flow', None)


def test_picker_credentials_retry_and_manual(flow_module):
    async def run():
        flow = flow_module.DistrictConfigFlow()
        flow.hass = SimpleNamespace(config=SimpleNamespace(time_zone='America/Detroit'))
        flow._districts = bundled_directory()
        flow._directory_fallback = False
        form = await flow.async_step_user({'district':'1261'})
        assert form['step_id'] == 'credentials'
        assert {str(k) for k in form['data_schema'].schema} == {'username','password','timezone'}
        captured = []
        async def validate(data):
            captured.append(data)
            return 'invalid_auth' if len(captured) == 1 else None
        flow.validate = validate
        user = {'username':' test ', 'password':'synthetic', 'timezone':'America/Detroit'}
        assert (await flow.async_step_credentials(user))['errors']['base'] == 'invalid_auth'
        result = await flow.async_step_credentials(user)
        assert result['data']['url'] == flow._districts['1261']['url']
        assert result['data']['username'] == 'test'
        assert result['title'] == 'Lake Orion Community School District'
        http = next(k for k,v in flow._districts.items() if v['url'].startswith('http:'))
        assert (await flow.async_step_user({'district':http}))['errors']['base'] == 'district_http_only'
        assert len(captured) == 2
        assert (await flow.async_step_user({'district':'manual'}))['step_id'] == 'manual'
    asyncio.run(run())


def test_directory_network_failure_falls_back(flow_module):
    class Session:
        def get(self, *args, **kwargs): raise asyncio.TimeoutError
    async def executor(func): return func()
    rows, fallback = asyncio.run(load_directory(SimpleNamespace(session=Session(), async_add_executor_job=executor)))
    assert fallback and len(rows) == 316
