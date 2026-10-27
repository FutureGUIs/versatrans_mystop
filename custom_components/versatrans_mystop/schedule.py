"""Student stop windows in the district timezone; no network operations."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import re
from zoneinfo import ZoneInfo
from .model import get
from .const import DEFAULT_BEFORE, DEFAULT_AFTER

WEEKDAYS = frozenset(range(5))
DAY_NAMES = {name: i for i, names in enumerate((('monday','mon'), ('tuesday','tue','tues'),
    ('wednesday','wed'), ('thursday','thu','thur','thurs'), ('friday','fri'),
    ('saturday','sat'), ('sunday','sun'))) for name in names}
WEEKDAY_CODES = {'m': 0, 't': 1, 'w': 2, 'h': 3, 'r': 3, 'th': 3, 'f': 4}


def service_days(value):
    """Read named days, day ranges and compact weekday codes (H = Thursday).

    Numeric masks and ambiguous single-letter weekend codes remain unsupported.
    Ranges run forward and may wrap past Sunday ("Fri-Mon").
    """
    text = str(value or '').strip().lower()
    if not text:
        return WEEKDAYS, 'weekdays_assumed'
    if text in ('weekdays','m-f','mon-fri','monday-friday','mtwrf'):
        return WEEKDAYS, 'available'
    if text in ('daily','every day','all days'):
        return frozenset(range(7)), 'available'
    names = {**DAY_NAMES, **WEEKDAY_CODES}
    # Ranges such as "Mon-Thu", "Monday - Friday", "M-TH" or "Mon thru Fri".
    ranged = re.sub(r'\s*(?:-|\u2013|\bthru\b|\bthrough\b|\bto\b)\s*', '-', text)
    words = [w for w in re.split(r'[\s,;/|]+', ranged) if w]
    if words and any('-' in w for w in words):
        days = set()
        for word in words:
            parts = word.split('-')
            if len(parts) == 1 and parts[0] in names:
                days.add(names[parts[0]])
            elif len(parts) == 2 and all(p in names for p in parts):
                first, last = names[parts[0]], names[parts[1]]
                days.update((first + i) % 7 for i in range((last - first) % 7 + 1))
            else:
                return frozenset(), 'unsupported_service_days'
        return frozenset(days), 'available'
    if words and all(w in names for w in words):
        return frozenset(names[w] for w in words), 'available'
    # Compact codes. Versatrans uses one letter per day with H for Thursday
    # (MTWHF), so MTHF is Mon, Tue, Thu, Fri. Read each letter alone first and
    # treat TH as Thursday only when single letters would repeat a day or run
    # out of order (MTWTHF, TTH).
    compact = ''.join(words)
    if compact and all(c in WEEKDAY_CODES for c in compact):
        letters = [WEEKDAY_CODES[c] for c in compact]
        if letters == sorted(set(letters)):
            return frozenset(letters), 'available'
    compact = compact.replace('th', 'h')
    if compact and all(c in WEEKDAY_CODES for c in compact):
        return frozenset(WEEKDAY_CODES[c] for c in compact), 'available'
    return frozenset(), 'unsupported_service_days'


def clock(value, tz):
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if stamp.tzinfo:
            stamp = stamp.astimezone(tz)
        return stamp.time().replace(tzinfo=None)
    except ValueError:
        for fmt in ('%I:%M %p','%I:%M:%S %p','%H:%M','%H:%M:%S'):
            try:
                return datetime.strptime(str(value).strip(), fmt).time()
            except ValueError:
                pass
    return None


INBOUND = ('I', 'INBOUND', 'TO SCHOOL')
OUTBOUND = ('O', 'OUTBOUND', 'FROM SCHOOL')


def stop_kind(direction):
    """Map an API direction to 'morning', 'afternoon', or None."""
    return 'morning' if direction in INBOUND else 'afternoon' if direction in OUTBOUND else None


@dataclass
class Plan:
    morning: str | None
    afternoon: str | None
    active: bool
    next_start: datetime | None
    status: str
    next_morning: datetime | None = None
    next_afternoon: datetime | None = None


def plan(student, now, timezone_name, preferences=None, before=DEFAULT_BEFORE, after=DEFAULT_AFTER):
    tz = ZoneInfo(timezone_name)
    now = now.astimezone(tz)
    routes = get(student, 'Routes', []) or []
    if not isinstance(routes, list):
        routes = []
    times = {'morning': set(), 'afternoon': set()}
    upcoming = {'morning': [], 'afternoon': []}
    windows, issues = [], set()
    for route in routes:
        direction = str(get(route, 'Direction', '')).strip().upper()
        kind = stop_kind(direction)
        if kind is None:
            issues.add('unsupported_direction')
            continue
        stop = clock(get(route, 'BusStopTime'), tz) or clock(get(route, 'PickupTime' if kind == 'morning' else 'DropoffTime'), tz)
        if stop is None:
            issues.add('missing_stop_time')
            continue
        days, status = service_days(get(route, 'Days'))
        if status != 'available':
            issues.add(status)
        times[kind].add(stop.strftime('%H:%M'))
        # Include yesterday for windows that cross midnight and a full following week.
        for offset in range(-1, 9):
            date = now.date() + timedelta(days=offset)
            if date.weekday() not in days:
                continue
            center = datetime.combine(date, stop, tz)
            center += timedelta(minutes=shift_minutes(preferences, direction, date))
            if center.astimezone(timezone.utc) > now.astimezone(timezone.utc):
                upcoming[kind].append(center)
            windows.append((center-timedelta(minutes=before), center+timedelta(minutes=after)))
    active = any(start <= now <= end for start,end in windows)
    future = [start for start,end in windows if start > now]
    status = ', '.join(sorted(issues)) if issues else ('available' if windows else 'missing_schedule')
    return Plan(', '.join(sorted(times['morning'])) or None,
                ', '.join(sorted(times['afternoon'])) or None, active,
                min(future) if future else None, status,
                min(upcoming['morning']) if upcoming['morning'] else None,
                min(upcoming['afternoon']) if upcoming['afternoon'] else None)


def schedule_snapshot(student, timezone_name):
    """Keep only normalized clock times, direction and named service days."""
    tz = ZoneInfo(timezone_name)
    rows = get(student, 'Routes', []) or []
    if not isinstance(rows, list):
        return None
    result = []
    for row in rows:
        direction = str(get(row, 'Direction', '')).strip().upper()
        kind = stop_kind(direction)
        if kind is None:
            continue
        stop = clock(get(row, 'BusStopTime'), tz) or clock(get(row, 'PickupTime' if kind == 'morning' else 'DropoffTime'), tz)
        days, _ = service_days(get(row, 'Days'))
        if stop is None or not days:
            continue
        names = ('Mon','Tue','Wed','Thu','Fri','Sat','Sun')
        result.append({'Direction': 'I' if kind == 'morning' else 'O',
                       'BusStopTime': stop.isoformat(),
                       'Days': ' '.join(names[d] for d in sorted(days))})
    return {'Routes': result} if result else None


def shift_minutes(preferences, direction, date):
    """Honor dated positive inbound delays and negative outbound early release."""
    try:
        minutes = int(get(preferences, 'MSRouteShiftMinutes', 0) or 0)
        start = datetime.fromisoformat(get(preferences, 'MSRouteShiftStart', '').replace('Z','+00:00')).date()
        end = datetime.fromisoformat(get(preferences, 'MSRouteShiftEnd', '').replace('Z','+00:00')).date()
        if start <= date <= end and ((direction in INBOUND and minutes > 0) or
                                    (direction in OUTBOUND and minutes < 0)):
            return minutes
    except (ValueError, TypeError, AttributeError):
        pass
    return 0
