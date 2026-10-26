import asyncio
from datetime import datetime, timedelta, timezone
import pytest
from mystop_test.model import position, latest, freshness, password_encode
from mystop_test.api import DistrictApiClient, AuthError, ApiError, SessionExpired

CFG=dict(url='https://example.invalid/MyStopWebAPI/', username='synthetic', password='synthetic', device_id='test', school_guid='test', timezone='America/Detroit')
NOW=datetime(2026,9,28,19,36,tzinfo=timezone.utc)
def gps(age=10, **changes):
    return {'Latitude':42.0,'Longitude':-83.0,'LogTime':(NOW-timedelta(seconds=age)).isoformat(), **changes}
def account(vehicle='65'):
    return {'SessionID':'secret-session','LoginGUID':'secret-guid','Students':[
        {'RecordID':1,'FirstName':'Alpha','Routes':[{'Direction':'O','Days':'Mon Tue Wed Thu Fri','DropoffTime':'15:36'}],'MatchedBusData':{'RPVehicleId':vehicle,'IsActive':False},'MatchedRoute':{'Route':'R1','ScheduledBusStopTime':'03:36 PM'}},
        {'RecordID':2,'FirstName':'Beta','Routes':[{'Direction':'O','Days':'Mon Tue Wed Thu Fri','DropoffTime':'15:36'}],'MatchedBusData':{'RPVehicleId':vehicle,'IsActive':False}},
        {'RecordID':3,'FirstName':'Gamma'}]}

class FakeClient(DistrictApiClient):
    def __init__(self):
        super().__init__(None,CFG)
        self.calls=[]
        self.vehicle='65'
        self.rows=[gps(30),gps(10)]
        self.expire=0
    def now(self):return NOW
    async def request(self,path,body=None,*,login=False):
        self.calls.append((path,body,login))
        if login:
            return account(self.vehicle)
        if self.expire:
            self.expire-=1
            raise SessionExpired()
        if 'recentvehicledata' in path:
            return {'BusData':self.rows}
        return {}

def run(coro):return asyncio.run(coro)

def test_timestamp_conversion_and_placeholders():
    p=position({'Latitude':42.,'Longitude':-83.,'LogTime':'2026-09-28T15:05:12'},'America/Detroit')
    assert p.timestamp.isoformat()=='2026-09-28T19:05:12+00:00'
    for row in [gps(Latitude=0,Longitude=0),gps(LogTime='0001-01-01T00:00:00'),gps(Latitude=float('nan')),gps(Longitude=181),gps(LogTime=None)]:
        assert position(row,'America/Detroit') is None

def test_freshness_and_newest():
    p=latest([gps(400),gps(10)],'America/Detroit')
    assert freshness(p,NOW,300)=='fresh'
    assert freshness(p,NOW+timedelta(minutes=10),300)=='stale'
    assert freshness(position(gps(-120),'America/Detroit'),NOW,300)=='clock_mismatch'
    assert freshness(None,NOW,300)=='no_position'
    with pytest.raises(ValueError):password_encode('nonascii\u2603')

def test_shared_bus_and_student_without_vehicle():
    c=FakeClient();data=run(c.fetch(300))
    assert len(data)==3 and data['3'].status=='no_assigned_vehicle'
    assert data['1'].position==data['2'].position
    assert data['1'].active is False and data['1'].status=='fresh'
    calls=[x for x in c.calls if 'recentvehicledata' in x[0]]
    assert len(calls)==1 and calls[0][0].endswith('rpVehicleId=65') and calls[0][1] is None
    assert data['1'].scheduled=='03:36 PM'

def test_same_bus_retains_position_new_bus_clears():
    c=FakeClient();first=run(c.fetch(300))
    c.rows=[]
    assert run(c.fetch(300))['1'].position==first['1'].position
    c.vehicle='99';c.account=None
    assert run(c.fetch(300))['1'].position is None

def test_single_session_renewal():
    c=FakeClient();run(c.authenticate());c.active_students={"1","2"};c.expire=1
    assert run(c.fetch(300))['1'].vehicle=='65'
    assert sum(login for _,_,login in c.calls)==2
    c.expire=2
    with pytest.raises(AuthError):run(c.fetch(300))

class Response:
    def __init__(self,status,payload):self.status,self.payload=status,payload
    async def __aenter__(self):return self
    async def __aexit__(self,*args):pass
    async def json(self,**kwargs):return self.payload
class HTTP:
    def __init__(self,status=200,payload=None):self.status,self.payload,self.calls=status,payload,[]
    def post(self,url,**kwargs):
        self.calls.append((url,kwargs));return Response(self.status,self.payload)

def test_http_empty_body_headers_and_redaction():
    http=HTTP(payload={'BusData':[]});c=DistrictApiClient(http,CFG);c.sid='session'
    run(c.request('api/student/recentvehicledata?rpVehicleId=65'))
    _,args=http.calls[-1]
    assert args['data']==b'' and args['headers']['X-SID']=='session'
    assert args['allow_redirects'] is False
    http.status=500;http.payload={'ErrorMessage':'sensitive response'}
    with pytest.raises(ApiError) as exc:run(c.request('test'))
    assert 'sensitive' not in str(exc.value)
    http.status=401
    with pytest.raises(AuthError):run(c.request('login',{},login=True))
    with pytest.raises(SessionExpired):run(c.request('tracking',{}))

def test_login_posts_encoded_password_no_old_session():
    http=HTTP(payload=account());c=DistrictApiClient(http,CFG);c.sid='old'
    run(c.authenticate())
    _,args=http.calls[-1]
    assert args['json']['Password']==password_encode(CFG['password'])
    assert 'X-SID' not in args['headers']
    assert c.sid=='secret-session'

@pytest.mark.parametrize('status,code',[(404,'endpoint_not_found'),(429,'rate_limited'),(302,'redirect_error'),(500,'server_error'),(400,'http_error')])
def test_setup_http_error_categories(status,code):
    c=DistrictApiClient(HTTP(status,{}),CFG)
    with pytest.raises(ApiError) as exc:run(c.request('test',{},login=True))
    assert exc.value.code==code

@pytest.mark.parametrize('failure,code',[(asyncio.TimeoutError(),'timeout'),(ValueError('raw private data'),'invalid_response')])
def test_safe_exception_categories(failure,code):
    class BrokenHTTP:
        def post(self,*args,**kwargs):raise failure
    with pytest.raises(ApiError) as exc:run(DistrictApiClient(BrokenHTTP(),CFG).request('test'))
    assert exc.value.code==code
    assert 'private' not in str(exc.value)

def test_explicit_tls_context_sent_to_aiohttp():
    import ssl
    ctx=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    http=HTTP(payload={'BusData':[]})
    run(DistrictApiClient(http,CFG,ssl_context=ctx).request('test'))
    assert http.calls[0][1]['ssl'] is ctx


def test_polling_override_start_stop_and_resume():
    c=FakeClient();c.now=lambda:datetime(2026,9,28,16,tzinfo=timezone.utc)
    assert run(c.fetch(300))['1'].polling_status=='outside_window'
    count=len(c.calls)
    c.options={'polling_mode':'manual'}
    data=run(c.fetch(300))
    assert data['1'].polling_status=='manual_polling'
    assert data['2'].polling_status=='manual_polling'
    assert len(c.calls)>count
    assert sum('recentvehicledata' in path for path,_,_ in c.calls)==1
    c.options={'polling_mode':'paused'};count=len(c.calls)
    assert run(c.fetch(300))['1'].polling_status=='paused'
    assert len(c.calls)==count
    c.options={'polling_mode':'scheduled'}
    assert run(c.fetch(300))['1'].polling_status=='outside_window'
    assert len(c.calls)==count
    c.now=lambda:NOW
    assert run(c.fetch(300))['1'].polling_status=='polling'
    assert len(c.calls)>count


def test_changed_window_starts_polling_and_stop_prevents_followup_requests():
    c=FakeClient();c.now=lambda:datetime(2026,9,28,19,10,tzinfo=timezone.utc)
    assert run(c.fetch(300))['1'].polling_status=='outside_window'
    c.options={'poll_before':30,'poll_after':60}
    assert run(c.fetch(300))['1'].polling_status=='polling'
    original=c.request
    async def stop_in_flight(path,body=None,*,login=False):
        result=await original(path,body,login=login)
        if 'vehicledataEncrypted' in path:c.options={'polling_mode':'paused'}
        return result
    c.request=stop_in_flight
    count=sum('recentvehicledata' in path for path,_,_ in c.calls)
    assert run(c.fetch(300))['1'].polling_status=='paused'
    assert sum('recentvehicledata' in path for path,_,_ in c.calls)==count


def test_incomplete_morning_login_does_not_erase_valid_polling_schedule():
    import copy
    class PartialClient(FakeClient):
        incomplete=False
        stamp=datetime(2026,10,5,16,tzinfo=timezone.utc)
        def now(self):return self.stamp
        async def request(self,path,body=None,*,login=False):
            if login:
                self.calls.append((path,body,login))
                record=copy.deepcopy(account())
                for student in record['Students'][:2]:
                    student['Routes']=[{'Direction':'I','Days':'MTWHF','PickupTime':'07:53'},
                                       {'Direction':'O','Days':'MTWHF','DropoffTime':'15:36'}]
                    if self.incomplete:student['Routes']=[]
                return record
            return await super().request(path,body,login=login)
    c=PartialClient()
    run(c.fetch(300))  # Monday, working schedules learned.
    c.incomplete=True;c.stamp=datetime(2026,10,6,8,tzinfo=timezone.utc)
    run(c.fetch(300))  # Tuesday 04:00 login returns students without routes.
    c.stamp=datetime(2026,10,6,11,38,tzinfo=timezone.utc)
    result=run(c.fetch(300))  # Tuesday 07:38, automatic pickup window.
    assert result['1'].polling_status=='polling'
    assert result['1'].schedule_status=='cached_schedule'
    assert any('recentvehicledata' in path for path,_,_ in c.calls)


def test_missing_login_session_is_retryable_and_preserves_existing_account():
    http=HTTP(payload=account());c=DistrictApiClient(http,CFG)
    run(c.authenticate());previous=c.account
    http.payload={'Students':[]}
    with pytest.raises(ApiError) as exc:run(c.authenticate())
    assert not isinstance(exc.value,AuthError)
    assert c.account is previous
    http.payload=account('99')
    run(c.authenticate())
    assert c.account['Students'][0]['MatchedBusData']['RPVehicleId']=='99'


def test_restored_schedule_fallback_keeps_live_assignment_and_excludes_removed_students():
    from mystop_test.cache import account_snapshot
    cached=account_snapshot(account('65'),CFG['timezone'])
    live=account('99');live['Students']=live['Students'][:1]
    live['Students'][0]['Routes']=[]
    c=DistrictApiClient(HTTP(payload=live),CFG);c.now=lambda:NOW
    c.account=cached;c.remember_schedules(cached)
    run(c.authenticate())
    assert set(c.schedule_cache)=={'1'}
    record,using_cache=c.schedule_record(c.account['Students'][0])
    assert using_cache and record['MatchedBusData']['RPVehicleId']=='99'
    assert c.plans()['1'].active and c.plans()['1'].status=='cached_schedule'
    snapshot=account_snapshot(c.schedule_account(),CFG['timezone'])
    assert [s['RecordID'] for s in snapshot['Students']]==[1]
    assert snapshot['Students'][0]['Routes']==cached['Students'][0]['Routes']
    assert 'MatchedBusData' not in snapshot['Students'][0]
    assert 'SessionID' not in snapshot


def test_nonempty_unsupported_schedule_does_not_silently_use_cached_rules():
    live=account();c=DistrictApiClient(HTTP(payload=live),CFG);c.now=lambda:NOW
    run(c.authenticate())
    live=account()
    live['Students'][0]['Routes'][0]['Days']='12345'
    c.http.payload=live;run(c.authenticate())
    record,using_cache=c.schedule_record(c.account['Students'][0])
    assert not using_cache and record['Routes'][0]['Days']=='12345'
    assert not c.plans()['1'].active
    assert c.plans()['1'].status=='unsupported_service_days'


def test_failed_vehicle_request_does_not_repeat_window_login_every_tick():
    class Failing(FakeClient):
        t=NOW-timedelta(hours=8)
        def now(self):return self.t
        async def request(self,path,body=None,*,login=False):
            if 'recentvehicledata' in path:
                raise ApiError('down')
            return await super().request(path,body,login=login)
    c=Failing();run(c.fetch(300))  # daily login outside the window
    for minute in range(3):
        c.t=NOW+timedelta(minutes=minute)
        with pytest.raises(ApiError):run(c.fetch(300))
    assert sum(1 for call in c.calls if call[2])==2


def test_user_agent_matches_manifest_version():
    import json
    from pathlib import Path
    from mystop_test.const import VERSION
    manifest=json.loads((Path(__file__).parents[1]/'custom_components'/'versatrans_mystop'/'manifest.json').read_text())
    assert manifest['version']==VERSION


def test_one_failing_bus_does_not_fail_other_students():
    class TwoBuses(FakeClient):
        async def request(self,path,body=None,*,login=False):
            if login:
                record=account('65');record['Students'][1]['MatchedBusData']={'RPVehicleId':'77','IsActive':True}
                self.calls.append((path,body,login));return record
            if 'rpVehicleId=77' in path or (body and body.get('VehicleId')=='77'):
                raise ApiError('bus 77 down','server_error')
            return await super().request(path,body,login=login)
    c=TwoBuses();data=run(c.fetch(300))
    assert data['1'].polling_status=='polling' and data['1'].status=='fresh'
    assert data['2'].polling_status=='request_failed' and data['2'].active is None
    assert data['3'].polling_status=='schedule_unavailable'
    assert c.failed_vehicles=={'77'}


def test_session_expiry_still_renews_with_per_bus_isolation():
    c=FakeClient();c.expire=1;data=run(c.fetch(300))
    assert data['1'].polling_status=='polling'
    assert sum(1 for call in c.calls if call[2])==2
