"""Account-scoped district API client."""
import asyncio
import logging
import socket
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode
from zoneinfo import ZoneInfo
import aiohttp
from .model import Student, freshness, get, latest, password_encode
from .schedule import plan
from .const import DEFAULT_BEFORE, DEFAULT_AFTER, VERSION
from .cache import account_snapshot

LOGGER = logging.getLogger(__name__)


class ApiError(Exception):
    """Communication or response failure. Never includes server response text."""

    def __init__(self, message="District API request failed", code="invalid_response"):
        super().__init__(message)
        self.code = code


class AuthError(ApiError):
    """Credentials rejected or session could not be renewed."""


class SessionExpired(ApiError):
    pass


class DistrictApiClient:
    def __init__(self, session, config, ssl_context=None):
        self.http = session
        self.config = config
        self.ssl_context = ssl_context
        self.sid = None
        self.account = None
        self.authenticated_at = None
        self.previous = {}
        self.schedule_day = None
        self.active_students = set()
        self.options = {}
        self.schedule_cache = {}
        self.cached_preferences = {}
        self.last_check = None
        self.failed_vehicles = set()

    def now(self):
        return datetime.now(timezone.utc)

    def plans(self):
        plans = {}
        for student in (get(self.account, "Students", []) or []):
            record, cached = self.schedule_record(student)
            p = plan(record, self.now(), self.config["timezone"], self.schedule_preferences(),
                self.options.get("poll_before", DEFAULT_BEFORE), self.options.get("poll_after", DEFAULT_AFTER))
            if cached:
                p = replace(p, status="cached_schedule")
            plans[str(get(student, "RecordID"))] = p
        mode = self.options.get("polling_mode", "scheduled")
        if mode in ("manual", "paused"):
            plans = {sid: replace(p, active=mode == "manual") for sid, p in plans.items()}
        return plans

    def remember_schedules(self, account):
        """Keep valid rules without retaining old bus or session assignments."""
        snapshot = account_snapshot(account, self.config["timezone"])
        if snapshot:
            for student in snapshot["Students"]:
                self.schedule_cache[str(student["RecordID"])] = {"Routes": student["Routes"]}
        preferences = get(account, "Preferences")
        if isinstance(preferences, dict):
            self.cached_preferences = {key: get(preferences, key) for key in (
                "MSRouteShiftMinutes", "MSRouteShiftStart", "MSRouteShiftEnd")}

    def schedule_record(self, student):
        """Use last-known rules only when this returned student has no routes."""
        sid = str(get(student, "RecordID"))
        cached = self.schedule_cache.get(sid)
        if not get(student, "Routes") and cached:
            record = {key: value for key, value in student.items() if key.lower() != "routes"}
            record["Routes"] = cached["Routes"]
            return record, True
        return student, False

    def schedule_preferences(self):
        preferences = get(self.account, "Preferences")
        return preferences if isinstance(preferences, dict) else self.cached_preferences

    def schedule_account(self):
        """Effective schedules for currently returned students, for persistence."""
        return {"Students": [self.schedule_record(s)[0] for s in (get(self.account, "Students", []) or [])],
                "Preferences": self.schedule_preferences()}

    async def request(self, path, body=None, *, login=False):
        headers = {"Accept": "application/json", "User-Agent": f"Home Assistant Bus Tracking/{VERSION}"}
        if self.sid and not login:
            headers["X-SID"] = self.sid
        kwargs = {"json": body} if body is not None else {"data": b""}
        if self.ssl_context is not None:
            kwargs["ssl"] = self.ssl_context
        if body is None:
            headers["Content-Type"] = "text/plain; charset=utf-8"
        try:
            async with self.http.post(self.config["url"].rstrip("/") + "/" + path,
                                      headers=headers, allow_redirects=False,
                                      timeout=aiohttp.ClientTimeout(total=30), **kwargs) as response:
                if response.status in (401, 403):
                    raise AuthError("Login rejected") if login else SessionExpired("Session expired")
                if response.status != 200:
                    code = "endpoint_not_found" if response.status == 404 else "rate_limited" if response.status == 429 else "redirect_error" if 300 <= response.status < 400 else "server_error" if response.status >= 500 else "http_error"
                    raise ApiError(f"District API returned HTTP {response.status}", code)
                record = await response.json(content_type=None)
        except aiohttp.ClientConnectorCertificateError as exc:
            code = getattr(exc.certificate_error, "verify_code", "unknown")
            reason = getattr(exc.certificate_error, "verify_message", "certificate validation failed")
            raise ApiError(f"TLS certificate validation failed (code {code}): {reason}", "tls_error") from None
        except aiohttp.ClientConnectorSSLError as exc:
            reason = getattr(exc.os_error, "reason", "unknown")
            raise ApiError(f"TLS handshake failed: {reason}", "tls_error") from None
        except asyncio.TimeoutError:
            raise ApiError("District API timed out after 30 seconds", "timeout") from None
        except aiohttp.ClientConnectorError as exc:
            code = "dns_error" if isinstance(exc.os_error, socket.gaierror) else "connection_error"
            raise ApiError("District hostname could not be resolved" if code == "dns_error" else "Unable to open a connection to the district", code) from None
        except aiohttp.ClientError:
            raise ApiError("Connection failed while reading district response", "connection_error") from None
        except ValueError:
            raise ApiError("District returned invalid JSON rather than API data", "invalid_response") from None
        if not isinstance(record, dict):
            raise ApiError("Unexpected district API response format")
        if get(record, "Error", False):
            raise AuthError("Login rejected") if login else ApiError("District API reported an error")
        return record

    async def authenticate(self):
        cfg = self.config
        record = await self.request("api/admin/loginuserEncrypted", {
            "UserName": cfg["username"], "Password": password_encode(cfg["password"]),
            "DeviceId": cfg["device_id"], "DeviceType": "Android",
            "DeviceName": "Home Assistant Bus Tracking", "SchoolGUID": cfg["school_guid"],
            "GetRouteDataOnly": False}, login=True)
        if not get(record, "SessionID"):
            # An incomplete HTTP 200 response does not prove bad credentials.
            # Treat it as retryable so HA does not stop its refresh timer.
            raise ApiError("District API did not return a session", "invalid_response")
        students = get(record, "Students")
        if not isinstance(students, list) or not all(isinstance(s, dict) and get(s, "RecordID") is not None for s in students):
            raise ApiError("Unexpected student response")
        self.remember_schedules(self.account)
        self.remember_schedules(record)
        current = {str(get(s, "RecordID")) for s in students}
        self.schedule_cache = {sid: rules for sid, rules in self.schedule_cache.items() if sid in current}
        self.sid, self.account = get(record, "SessionID"), record
        self.authenticated_at = self.now()
        self.schedule_day = (self.now().astimezone(ZoneInfo(cfg["timezone"])) - timedelta(hours=4)).date()

    async def fetch(self, stale_after):
        # Local coordinator ticks age the GPS data; they do not necessarily call the API.
        self.last_check = self.now()
        self.remember_schedules(self.account)
        day = (self.now().astimezone(ZoneInfo(self.config["timezone"])) - timedelta(hours=4)).date()
        refreshed = False
        if self.account is None or self.schedule_day != day:
            await self.authenticate()
            refreshed = True
        plans = self.plans()
        active = {sid for sid, p in plans.items() if p.active}
        if active and not refreshed and (active - self.active_students or self.now() - self.authenticated_at >= timedelta(minutes=5)):
            # Resolve current/substitute vehicle assignments at the start of a window.
            await self.authenticate()
            plans = self.plans()
            active = {sid for sid, p in plans.items() if p.active}
        # Record the window-start login before vehicle requests, so a failed
        # vehicle request does not cause a fresh login on every refresh tick.
        self.active_students = active
        try:
            result = await self._fetch(stale_after, plans)
        except SessionExpired:
            await self.authenticate()  # One renewal, never an unbounded login loop.
            try:
                plans = self.plans()
                active = {sid for sid, p in plans.items() if p.active}
                result = await self._fetch(stale_after, plans)
            except SessionExpired:
                raise AuthError("Renewed session rejected") from None
        self.active_students = active
        return result

    async def _fetch(self, stale_after, plans):
        cfg = self.config
        students = get(self.account, "Students", []) or []
        if not all(isinstance(s, dict) and get(s, "RecordID") is not None for s in students):
            raise ApiError("Unexpected student records")
        # A failing bus affects only the students assigned to it. The refresh
        # fails as a whole only when every requested bus fails.
        prepared, vehicles, updates = [], set(), {}
        requested, failed, failed_ids, errors = set(), set(), set(), []
        for student in students:
            bus, route = get(student, "MatchedBusData") or {}, get(student, "MatchedRoute") or {}
            vehicle = get(bus, "RPVehicleId")
            sid = str(get(student, "RecordID"))
            if vehicle:
                vehicle = str(vehicle)
            if vehicle and plans[sid].active and self.options.get("polling_mode") != "paused":
                requested.add(sid)
                if vehicle not in updates:
                    try:
                        updates[vehicle] = await self.request("api/student/vehicledataEncrypted", {
                        "VehicleId": vehicle,
                        "RequestTime": self.now().astimezone(ZoneInfo(cfg["timezone"])).replace(tzinfo=None).isoformat(),
                        "UserName": cfg["username"], "UserGuid": get(self.account, "LoginGUID", ""),
                        "Password": password_encode(cfg["password"]), "NeedAuthentication": False,
                        "DeviceId": cfg["device_id"], "SchoolGuid": cfg["school_guid"],
                        "NeedRouteData": True, "CurrentVehicleNumber": get(bus, "OrigBusBeforeOverRide_Vehicle") or get(route, "OrigBusBeforeOverRide_Route") or vehicle,
                        "RouteNumber": get(route, "Route", "")})
                    except (SessionExpired, AuthError):
                        raise
                    except ApiError as exc:
                        updates[vehicle] = None
                        errors.append(exc)
                        failed_ids.add(vehicle)
                result = updates[vehicle]
                if result is None:
                    failed.add(sid)
                else:
                    bus = get(result, "StuBusData") or bus
                    vehicle = str(get(bus, "RPVehicleId") or vehicle)
                    vehicles.add(vehicle)
            prepared.append((student, bus, route, vehicle))
        recent = {}
        for vehicle in sorted(vehicles):
            if self.options.get("polling_mode") == "paused":
                break
            try:
                result = await self.request("api/student/recentvehicledata?" + urlencode({"rpVehicleId": vehicle}))
                rows = get(result, "BusData", []) or []
                if not isinstance(rows, list):
                    raise ApiError("Unexpected position response")
                recent[vehicle] = rows
            except (SessionExpired, AuthError):
                raise
            except ApiError as exc:
                errors.append(exc)
                failed_ids.add(vehicle)
        failed |= {sid for raw, _, _, vehicle in prepared
                   if (sid := str(get(raw, "RecordID"))) in requested and vehicle in failed_ids}
        if requested and requested <= failed:
            raise errors[0]
        for vehicle in sorted(failed_ids - self.failed_vehicles):
            LOGGER.warning("Bus %s request failed; other buses continue: %s", vehicle, errors[0])
        for vehicle in sorted(self.failed_vehicles - failed_ids):
            LOGGER.info("Bus %s requests recovered", vehicle)
        self.failed_vehicles = failed_ids
        now = self.now()
        data = {}
        for raw, bus, route, vehicle in prepared:
            sid = str(get(raw, "RecordID"))
            pos = latest([bus, *recent.get(vehicle, [])], cfg["timezone"]) if vehicle else None
            old = self.previous.get(sid)
            if old and old.vehicle == vehicle and old.position and old.position.timestamp <= now + timedelta(seconds=60) and (pos is None or old.position.timestamp > pos.timestamp):
                pos = old.position
            name = " ".join(str(get(raw, key, "") or "").strip() for key in ("FirstName", "LastName")).strip() or f"Student {sid}"
            active = get(bus, "IsActive")
            status = freshness(pos, now, stale_after) if vehicle else "no_assigned_vehicle"
            mode, p = self.options.get("polling_mode"), plans[sid]
            if mode == "paused":
                polling = "paused"
            elif sid in failed:
                polling = "request_failed"
            elif mode == "manual" and vehicle:
                polling = "manual_polling"
            elif p.active:
                polling = "polling" if vehicle else "no_matched_vehicle"
            else:
                polling = "outside_window" if p.next_start else "schedule_unavailable"
            live = isinstance(active, bool) and p.active and mode != "paused" and sid not in failed
            data[sid] = Student(sid, name, vehicle,
                get(route, "ScheduledBusStopTimeWithRouteShift") or get(route, "ScheduledBusStopTime"),
                active if live else None, pos, status, p.morning, p.afternoon,
                polling, p.next_start, p.status)
        self.previous = data
        return data
