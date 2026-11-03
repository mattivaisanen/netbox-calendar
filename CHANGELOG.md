# Changelog

## Unreleased

- User groups can be added to an event as people. Each member counts as
  on the event, for notices and for editing.
- Users are shown by full name (username when no name is set) on event
  pages, the event list, user pickers and the iCal feed.
- Removing a person from an event asks for confirmation.

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
