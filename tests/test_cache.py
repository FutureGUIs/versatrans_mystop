import json
from mystop_test.cache import account_snapshot
from test_protocol import account


def test_schedule_cache_excludes_live_data_and_credentials():
    raw=account();raw['Password']='private-password';raw['UserName']='private-user'
    raw['Students'][0]['MatchedBusData']['Latitude']=42.123
    raw['Preferences']={'MSRouteShiftMinutes':-60,'MSRouteShiftStart':'2026-10-01',
                       'MSRouteShiftEnd':'2026-10-01','private_field':'private-value'}
    snapshot=account_snapshot(raw,'America/Detroit');text=json.dumps(snapshot)
    assert len(snapshot['Students'])==2
    assert snapshot['Students'][0]['Routes'][0]['Days']=='Mon Tue Wed Thu Fri'
    for private in ('secret-session','secret-guid','private-password','private-user',
                    'private-value','Latitude','MatchedBusData','LoginGUID'):
        assert private not in text
    assert account_snapshot(snapshot,'America/Detroit')==snapshot
    for bad in (None,{}, {'Students':None},{'Students':{}},{'Students':[None]}):
        assert account_snapshot(bad,'America/Detroit') is None
