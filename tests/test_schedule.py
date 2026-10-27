from datetime import datetime,timedelta,timezone
import pytest
from mystop_test.schedule import plan, service_days, WEEKDAYS
from test_protocol import FakeClient, run

TZ='America/Detroit'
def student(days='Mon Tue Wed Thu Fri'):
    return {'Routes':[{'Direction':'I','PickupTime':'07:30 AM','DropoffTime':'08:00 AM','Days':days},
                      {'Direction':'O','PickupTime':'15:00','DropoffTime':'15:36','Days':days}]}
def at(hour,minute,day=28):return datetime(2026,9,day,hour,minute,tzinfo=timezone.utc)

def test_boundaries_and_correct_stop_end():
    for stamp in [at(11,15),at(12,0),at(19,21),at(20,6)]:
        assert plan(student(),stamp,TZ).active
    for stamp in [at(11,14),at(12,1),at(19,20),at(20,7),at(15,0)]:
        assert not plan(student(),stamp,TZ).active
    p=plan(student(),at(15,0),TZ)
    assert (p.morning,p.afternoon)==('07:30','15:36')
    assert p.next_start.hour==15 and p.next_start.minute==21

def test_weekend_missing_and_unsupported_days():
    assert not plan(student(),at(19,36,27),TZ).active
    assert plan(student(''),at(19,36),TZ).status=='weekdays_assumed'
    p=plan(student('12345'),at(19,36),TZ)
    assert not p.active and p.status=='unsupported_service_days'
    assert plan({},at(19,36),TZ).status=='missing_schedule'

def test_shift_and_midnight_window():
    prefs={'MSRouteShiftMinutes':'-60','MSRouteShiftStart':'2026-09-28','MSRouteShiftEnd':'2026-09-28'}
    assert plan(student(),at(18,36),TZ,prefs).active
    assert not plan(student(),at(19,36),TZ,prefs).active
    s={'Routes':[{'Direction':'I','BusStopTime':'00:05','Days':'Mon'}]}
    assert plan(s,at(3,55),TZ).active  # Sunday 23:55, Monday stop window.

def test_no_vehicle_requests_outside_window_and_daily_refresh():
    c=FakeClient();c.now=lambda:at(15,0)
    result=run(c.fetch(300))
    assert result['1'].polling_status=='outside_window'
    assert len(c.calls)==1 and c.calls[0][2]
    for _ in range(3):run(c.fetch(300))
    assert len(c.calls)==1
    c.now=lambda:at(19,21)
    run(c.fetch(300))
    assert sum(login for _,_,login in c.calls)==2
    assert len([p for p,_,_ in c.calls if 'vehicledataEncrypted' in p])==1
    count=len(c.calls)
    c.now=lambda:at(20,7)
    result=run(c.fetch(300))
    assert len(c.calls)==count and result['1'].status=='stale'
    c.now=lambda:at(9,0,29)  # next day's refresh after 04:00 local
    run(c.fetch(300))
    assert len(c.calls)==count+1 and c.calls[-1][2]


@pytest.mark.parametrize('value', ['MTWHF', 'mtwhf', 'M T W H F', 'M,T,W,TH,F',
                                 'MTWTHF', 'MTWRF', 'Mon, Tue, Wed, Thu, Fri'])
def test_compact_weekday_formats(value):
    assert service_days(value)==(WEEKDAYS,'available')


def test_partial_codes_do_not_invent_days():
    assert service_days('TH')==(frozenset({3}),'available')
    assert service_days('MWF')==(frozenset({0,2,4}),'available')
    assert service_days('T,H')==(frozenset({1,3}),'available')
    for value in ['12345', 'S', 'MTWHFS', 'invalid', ',']:
        assert service_days(value)==(frozenset(),'unsupported_service_days')
    assert not plan(student('MTWHF'),at(19,36,27),TZ).active


def test_service_day_schedule_starts_polling_without_reload():
    # Reproduce the reported 7:16 AM / 2:28 PM schedule on Thursday Oct 1.
    from test_protocol import account
    class ScheduledClient(FakeClient):
        stamp=datetime(2026,10,1,16,0,tzinfo=timezone.utc)
        def now(self):return self.stamp
        async def request(self,path,body=None,*,login=False):
            if login:
                self.calls.append((path,body,login))
                record=account('77')
                for s in record['Students'][:2]:
                    s['Routes']=[{'Direction':'I','Days':'MTWHF','PickupTime':'07:16'},
                                 {'Direction':'O','Days':'MTWHF','DropoffTime':'14:28'}]
                return record
            return await super().request(path,body,login=login)
    c=ScheduledClient()
    result=run(c.fetch(300))
    assert result['1'].schedule_status=='available'
    assert result['1'].polling_status=='outside_window'
    assert result['1'].next_polling_window.isoformat()=='2026-10-01T14:13:00-04:00'
    assert len(c.calls)==1
    c.stamp=datetime(2026,10,1,18,13,tzinfo=timezone.utc)
    assert run(c.fetch(300))['1'].polling_status=='polling'
    assert len([p for p,_,_ in c.calls if 'vehicledataEncrypted' in p])==1
    assert len([p for p,_,_ in c.calls if 'recentvehicledata' in p])==1
    c.stamp=datetime(2026,10,1,18,41,tzinfo=timezone.utc)
    assert run(c.fetch(300))['1'].polling_status=='polling'
    assert len([p for p,_,_ in c.calls if 'recentvehicledata' in p])==2
    count=len(c.calls)
    c.stamp=datetime(2026,10,1,18,59,tzinfo=timezone.utc)
    assert run(c.fetch(300))['1'].polling_status=='outside_window'
    assert len(c.calls)==count


@pytest.mark.parametrize('stamp,pickup_date,dropoff_date', [
    ('2026-10-01T11:00:00+00:00','2026-10-01','2026-10-01'),
    ('2026-10-01T14:00:00+00:00','2026-10-02','2026-10-01'),
    ('2026-10-02T20:00:00+00:00','2026-10-05','2026-10-05'),
    ('2026-10-03T16:00:00+00:00','2026-10-05','2026-10-05'),
    ('2026-10-01T11:53:00+00:00','2026-10-02','2026-10-01')])
def test_next_occurrences_match_example_and_exact_time(stamp,pickup_date,dropoff_date):
    raw={'Routes':[{'Direction':'I','PickupTime':'07:53','Days':'MTWHF'},
                   {'Direction':'O','DropoffTime':'15:36','Days':'MTWHF'}]}
    p=plan(raw,datetime.fromisoformat(stamp),TZ)
    assert p.next_morning.date().isoformat()==pickup_date
    assert p.next_afternoon.date().isoformat()==dropoff_date
    assert p.next_morning.hour==7 and p.next_morning.minute==53
    assert p.next_afternoon.hour==15 and p.next_afternoon.minute==36
    assert p.next_morning.tzinfo is not None


def test_multiple_stop_times_partial_week_and_date_shift():
    raw={'Routes':[{'Direction':'I','PickupTime':'07:00','Days':'MWF'},
                   {'Direction':'I','PickupTime':'08:00','Days':'MWF'}]}
    p=plan(raw,datetime(2026,10,2,11,30,tzinfo=timezone.utc),TZ)
    assert p.next_morning.isoformat()=='2026-10-02T08:00:00-04:00'
    p=plan(raw,datetime(2026,10,6,11,30,tzinfo=timezone.utc),TZ)
    assert p.next_morning.isoformat()=='2026-10-07T07:00:00-04:00'
    # Crossing a DST change adjusts the offset, retaining district clock time.
    p=plan(raw,datetime(2026,10,30,20,tzinfo=timezone.utc),TZ)
    assert p.next_morning.isoformat()=='2026-11-02T07:00:00-05:00'


def test_configurable_margins_do_not_change_next_stop():
    p=plan(student(),at(19,10),TZ,before=30,after=60)
    assert p.active
    assert not plan(student(),at(19,10),TZ).active
    assert plan(student(),at(20,36),TZ,before=30,after=60).active
    assert not plan(student(),at(20,37),TZ,before=30,after=60).active
    assert p.next_afternoon==plan(student(),at(19,10),TZ).next_afternoon


@pytest.mark.parametrize('value,days',[
    ('Mon-Thu',{0,1,2,3}),('Monday - Friday',{0,1,2,3,4}),('M-TH',{0,1,2,3}),
    ('Mon thru Fri',{0,1,2,3,4}),('Tuesday to Thursday',{1,2,3}),
    ('Mon-Wed, Fri',{0,1,2,4}),('Fri-Mon',{4,5,6,0}),('Sat-Sun',{5,6})])
def test_service_day_ranges(value,days):
    assert service_days(value)==(frozenset(days),'available')


@pytest.mark.parametrize('value',['Mon-','Mon-Xyz','1-5','Mon-Wed-Fri'])
def test_invalid_service_day_ranges(value):
    assert service_days(value)==(frozenset(),'unsupported_service_days')


@pytest.mark.parametrize('value,days',[
    ('MTHF',{0,1,3,4}),('MTH',{0,1,3}),('MTWHF',{0,1,2,3,4}),('MTWTHF',{0,1,2,3,4}),
    ('MWTHF',{0,2,3,4}),('TTH',{1,3}),('MHF',{0,3,4}),('TH',{3})])
def test_single_letter_codes_use_h_for_thursday(value,days):
    assert service_days(value)==(frozenset(days),'available')


def test_mthf_morning_pickup_is_next_tuesday():
    # Monday 9:08 PM in Detroit; MTHF includes Tuesday.
    now=datetime(2026,10,27,1,8,tzinfo=timezone.utc)
    p=plan({'Routes':[{'Direction':'I','BusStopTime':'07:16:00','Days':'MTHF'}]},now,'America/New_York')
    assert p.next_morning.isoformat()=='2026-10-27T07:16:00-04:00'
