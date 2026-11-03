# Changelog

## Unreleased

- Upcoming Events widget: the start time now sits on the same line as the event title.
- The event form has one picker per kind of object (devices, sites, device
  roles, ...) and shows what is already linked when editing. Removing an
  object from a picker unlinks it.
- Linking a device role, device type, manufacturer, platform or tenant
  covers its devices, like sites and locations already did.
- The event page lists every affected device.
- Unlinking an object from the event page asks to confirm first.

## 0.1.1 (2026-11-03)

- iCal feed: time zone rules start in 1970 so they cover events of every
  year, a standard `NAME` next to `X-WR-CALNAME`, a one-hour refresh hint,
  and the feed is served inline and not cached by proxies.
- Calendar Feed page and usage docs explain why Outlook on the web can fail
  to subscribe to a NetBox that is only reachable internally.

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
