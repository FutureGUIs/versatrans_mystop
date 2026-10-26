# Versatrans My Stop for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![Validate](https://github.com/FutureGUIs/versatrans_mystop/actions/workflows/validate.yml/badge.svg)](https://github.com/FutureGUIs/versatrans_mystop/actions/workflows/validate.yml)

Unofficial custom integration for Home Assistant 2026.9 or newer. Version 0.4.3.
Connects to a school district's Versatrans My Stop API to provide student devices,
assigned-bus locations, scheduled stop timestamps, and polling controls. Only Lake
Orion has been account-tested. Offline tests use synthetic records and minimal HA
test doubles; they do not replace validation on the HA host.

This project is not affiliated with or endorsed by Tyler Technologies or any
school district.

## Installation

### HACS (recommended)

1. In Home Assistant, open **HACS**, then the three-dot menu > **Custom repositories**.
2. Add `https://github.com/FutureGUIs/versatrans_mystop` with type **Integration**.
3. Search HACS for **Versatrans My Stop**, open it, and select **Download**.
4. Restart Home Assistant.

[![Open your Home Assistant instance and open this repository in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=FutureGUIs&repository=versatrans_mystop&category=integration)

### Manual

1. Download the latest release or this repository.
2. Copy `custom_components/versatrans_mystop` to `/config/custom_components/versatrans_mystop`, replacing existing files when upgrading.
3. Confirm `manifest.json`, `districts.json`, `certificates/`, and `brand/` are directly inside that folder.
4. Restart Home Assistant.

### Setup

1. Add **Versatrans My Stop** under **Settings > Devices & services**.
2. Choose the district, enter district account credentials, and confirm the district timezone. Lake Orion uses America/Detroit.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=versatrans_mystop)

Upgrades preserve configured accounts, student devices, and entity IDs. There
is no need to remove and re-add the integration. Press **Resume scheduled
polling** if you want automatic windows after previously using Start or Stop.
No configuration.yaml changes or MQTT broker are required.

## Student devices and map

Every returned student gets a device with these entities:

| Entity | Section | Purpose |
|---|---|---|
| Bus location | Main | Assigned-bus GPS tracker for an HA Map card |
| Assigned bus | Main | Current matched vehicle identifier |
| Last GPS update | Main | Timestamp of the newest valid position |
| Morning pickup time | Main | Next inbound stop timestamp on a service day |
| Afternoon drop-off time | Main | Next outbound stop timestamp on a service day |
| Polling status | Diagnostic | polling, manual_polling, paused, request_failed, outside_window, no_matched_vehicle, or schedule_unavailable |
| Next polling window | Diagnostic | Next upcoming automatic window start |
| Schedule status | Diagnostic | available, cached_schedule, weekdays_assumed, missing_schedule, or unsupported field warning(s) |
| Route active | Diagnostic | Independent server activity flag; unknown outside polling windows |

Diagnostic entities appear under **Diagnostic** on the device page and are left
out of auto-generated dashboards. Entity IDs are unchanged, and they can still be
added to any card or used in automations. Entity names come from the integration's translation files. Frequently changing attributes
(last_local_check, last_login, schedule_cache, schedule_preferences) are excluded
from recorder history.

Students sharing a bus have separate trackers at the same coordinates, with
vehicle requests deduplicated. Students without a matched bus still get devices;
their trackers require an assigned bus and valid position. The tracker indicates
bus location, not confirmation that a student is on board.

Add a **Map** card and select each student's Bus location entity. For example,
replace these entity IDs with those on your student device pages:

```yaml
type: map
entities:
  - entity: device_tracker.student_one_bus_location
  - entity: device_tracker.student_two_bus_location
hours_to_show: 0
default_zoom: 12
```

## Schedules and polling

The default polling window is 15 minutes before through 30 minutes after each
stop, with a 60-second refresh interval. Morning uses inbound pickup and afternoon
uses outbound drop-off. A supplied BusStopTime takes precedence. The next local
refresh tick observes each window boundary. A 3:36 PM stop therefore polls from
3:21 through 4:06 PM, subject to that refresh interval.

Named service days, day ranges, and compact weekday codes are supported: MTWHF
and MTWRF mean Monday-Friday; MWF means Monday, Wednesday, and Friday. Ranges
such as Mon-Thu, Monday - Friday, M-TH, Mon thru Fri, and Mon-Wed, Fri are read
forward and may wrap past Sunday (Fri-Mon). Empty Days assumes
weekdays and reports weekdays_assumed. Unsupported directions, missing times,
numeric masks, and ambiguous single-letter weekend codes do not create windows.
Explicitly named weekend days are honored. Holidays are not inferred.

Pickup and drop-off are independent timestamps. Before today's stop they use
today; at or after the stop they advance to the next service day. Multiple stops
select the earliest upcoming occurrence. Supported dated inbound delays and
outbound early-release shifts adjust timestamps and polling windows. HA displays
timestamps using the viewing user's preferences; cards may show relative times.
The scheduled_times attribute contains normalized base clock times for templates.

Schedule timestamps remain available during API or GPS failures and advance
locally every minute from cached rules. The cache is restored after restart;
a valid schedule must first have been obtained. These values are recurring
schedules, not live arrival estimates or confirmation of school service.

Startup and the first local refresh at or after 04:00 obtain student schedules
and assignments. Login also occurs on entering a window and every five minutes
while a window is active. A failed vehicle request does not trigger another
window-start login on the next refresh. Local refreshes continue outside windows without
vehicle requests. Assignment changes announced after the daily login may not be
discovered until an existing window opens. New students are discovered
automatically; removed students' existing entities, including their pickup and
drop-off times, become unavailable.

If a successful login returns a student with missing or empty Routes, polling
uses their last-known normalized rules and reports cached_schedule. New valid
rules replace the cache. Nonempty unsupported routes continue to report warnings.
Cached rules never restore an old vehicle assignment or session; vehicle requests
require a current matched bus. Only currently returned students use the fallback.
An incomplete login response without SessionID is retryable; explicit rejected
credentials or failed session renewal trigger HA reauthentication.

## Account controls

The district polling controls device has two editable Number entities under
Configuration, applied to all students:

- **Polling minutes before stop:** default 15.
- **Polling minutes after stop:** default 30.

Both accept whole minutes from 0 through 240. They are also available in the
integration options and apply without a reload. Refresh interval is separately
configurable from 30 through 900 seconds.

Three buttons control the whole account:

- **Start polling:** force assigned-bus polling outside or inside scheduled windows
  for 3 hours, then return to scheduled polling automatically. Pressing it again
  restarts the 3 hours. The end time is in the manual_until attribute.
- **Stop polling:** pause vehicle requests. An in-flight request may finish; subsequent requests are skipped.
- **Resume scheduled polling:** return to automatic service-day windows.

Paused mode persists across days and restarts until another button is pressed.
Manual mode also survives restarts but ends at its manual_until time. Schedule timestamps keep advancing, and daily schedule logins can
still occur while vehicle polling is paused. Controls stay available during an
API outage.

Polling status has polling_mode, manual_until, last_local_check, last_login,
poll_before, and poll_after attributes. Account controls also expose polling_mode
and manual_until. last_local_check
means a local coordinator check, not proof of a vehicle request. Schedule status
exposes api_service_days for diagnosing unsupported day notation.

## GPS handling

The default stale threshold is 300 seconds. Last-known stale coordinates remain
visible by default; disable **Keep stale positions visible on the map** to make
stale trackers unavailable. HA's Map card does not distinguish stale coordinates,
so display Last GPS update alongside it. For location-triggered automations,
require the tracker's gps_status attribute to equal fresh.

Zero coordinates, invalid coordinates, missing dates, and year-0001 timestamps
are rejected. Timestamps more than 60 seconds ahead of HA's clock report
clock_mismatch and are not mapped. Naive timestamps use the district timezone;
the repeated DST fall-back hour uses its first occurrence. Freshness compares
GPS timestamps with HA's clock. Route active is independent of GPS validity.

Each bus is requested independently. If one bus's request fails, students on
other buses keep updating; affected students report request_failed and keep their
last-known position, which ages to stale. The refresh fails as a whole only when
every requested bus fails. Failures and recoveries are logged once per bus.

In-memory positions are retained only for the same student and bus. A changed
bus or restart clears that association. API failures make live bus entities
unavailable while schedule timestamps continue. GPS age, GPS fresh, GPS status,
and Scheduled stop time are not separate entities; retired registry entries are
removed during successful setup. Update cards or automations using those IDs.

## District setup and TLS

Setup loads the public district directory from
https://mystopclientlistapi.azurewebsites.net/api/ClientList/getall and uses the
bundled September 30, 2026 snapshot if unavailable. It contains 316 districts:
313 HTTPS entries and three HTTP-only entries. HTTP entries cannot submit
credentials. Manual setup requires a district-confirmed HTTPS URL and GUID.
The directory is fetched only during new setup and has no timezone field.

For Lake Orion the API base is https://mystop.lok12.org/MyStopWebAPI/.
Setup distinguishes DNS, TLS, timeout, connection, HTTP, and malformed-response
errors. Logs include fixed descriptions and status codes without response bodies.
Requests validate HTTPS certificates and hostnames and do not follow redirects.

For mystop.lok12.org HTTPS port 443, the integration supplies the public
GoDaddy R1-to-G2 cross-certificate needed to complete the observed chain. Full
certificate validation remains enabled. Certificate source:
https://certs.godaddy.com/repository/gd_tls_root-r1-cross-g2.crt.pem
DER SHA-256: 7bcb0f2f2d1031a6af8d61baa835d2835a3b8bcc26d94a3b048b1655fb81298c

## Storage and artwork

Credentials are stored in the HA configuration entry and are not separately
encrypted by this integration. Student names, location history, and configuration
can appear in HA storage, its recorder, and backups. The schedule cache contains
student IDs/names, normalized times/service days, and dated shift preferences;
it excludes passwords, sessions, GPS positions, addresses, and vehicle IDs.
Removing the configuration entry removes this cache. Non-ASCII passwords are
currently unsupported.

Brand assets are orange tiles with a white front-facing bus and STOP badge at
256 and 512 pixels. Bus trackers use mdi:bus-school. License notices for the
included historical SVG asset remain in ICON-LICENSE.txt and APACHE-2.0.txt.

## Developer checks

Run `pip install -r requirements_test.txt` and then `python -m pytest tests`.
Tests cover schedules, timestamp rollover, stale positions, account controls,
assignment isolation, shared-bus deduplication, cache restore, retryable login
responses, TLS, and setup behavior. API requests still require validation on
the actual district server. GitHub Actions runs hassfest, HACS validation, and
the tests on every push.

## License

The integration code is released under the [MIT License](LICENSE). The bundled
bus icon artwork retains its original license notices in ICON-LICENSE.txt and
APACHE-2.0.txt.
