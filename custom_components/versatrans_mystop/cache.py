"""Persist only student identity and recurring schedules, never live/API secrets."""
from .model import get
from .schedule import schedule_snapshot


def account_snapshot(account, timezone_name):
    students = get(account, "Students", []) or []
    if not isinstance(students, list):
        return None
    result = []
    for student in students:
        sid = get(student, "RecordID")
        if isinstance(sid, bool) or not isinstance(sid, (str, int)):
            continue
        snapshot = schedule_snapshot(student, timezone_name)
        if snapshot:
            result.append({"RecordID": sid,
                "FirstName": str(get(student, "FirstName", "") or ""),
                "LastName": str(get(student, "LastName", "") or ""), **snapshot})
    if not result:
        return None
    prefs = get(account, "Preferences") or {}
    return {"Students": result, "Preferences": {key: get(prefs, key) for key in (
        "MSRouteShiftMinutes", "MSRouteShiftStart", "MSRouteShiftEnd")}}
