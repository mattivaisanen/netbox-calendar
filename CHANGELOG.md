# Changelog

## Unreleased

- Reminders are set with tick boxes for common times (15 minutes to
  1 week before) plus a box for any other time. On an event, pick whether
  it uses the event type's reminders, its own, or none. Event and event
  type pages show reminder times in words.

## 0.1.0 (2026-11-03)

First release.

- Events with a type, status, impact, owner and people in roles, linked to
  any NetBox objects or to every object matching a filter.
- Repeating events on any iCal rule, with simple choices on the form,
  daylight-saving-safe times and skipped occurrences.
- Month view, timeline, event list and an Upcoming Events dashboard widget.
- An Events card and running banner on linked objects' pages, with events
  on sites, locations, racks and clusters rolled up to what is in them.
- Schedule one event for many objects from any object list.
- Notices when an event is scheduled, changed, cancelled, before it starts,
  at its start and at its end: NetBox notifications, NetBox Event Rules
  with ready message text and recipients, and optional direct e-mail with
  a calendar invite.
- Personal iCal feed and per-event `.ics` download.
- Change freeze windows, overlap warnings, and automatic status.
- Scheduled change requests from netbox-change-approval on the calendar,
  when that plugin is installed.
- Settings superusers change in NetBox, the REST API, and
  `prepare_calendar_uninstall` for a clean removal.
