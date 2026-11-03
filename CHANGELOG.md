# Changelog

## Unreleased

- Event form: after the start, give the end as a **Length** (such as
  `1h 30m`) or an **End date**, picked with the **End by** buttons. Repeat
  is part of the same **When** section and follows the start: weekly must
  include the start's weekday, and the repeat **Ends**: **No end** (the
  default), **Until a day** (not before the start) or after a **Number of
  times**.
- Fixed: date pickers started the week on Sunday whatever the **First day
  of the week** setting. They now follow it, and shade weekends.
- **All day** sits under the start. Ticking it switches the start to a
  date and hides End by; an optional **Last day** makes it run over several
  days.
- Beside **Length**, a read-only box shows the end it gives, such as
  *ends Tue 10.11.2026 12:00*.
- A repeating event lists its **Next** three times under the repeat fields,
  in NetBox's date format, updated as the form is filled in.
- People are added on the event page only. The event form's *Add people*
  section is gone; **Add People** on the People card adds users, groups
  and contacts in one role, and **Add and pick another role** goes on to
  the next. The owner stays in the event form.
- The event page shows the event's journal where the empty Comments panel
  was: the five newest entries and a form to add one without leaving the
  page. The Journal tab still lists them all. Comments (set in the edit
  form) show only when there are some. The add form fills the card.
- Fixed: tick boxes (reminder times, *Notify on*, repeat weekdays) showed
  as empty text fields stacked above their labels. They now sit side by
  side as NetBox checkboxes.
- Direct e-mail has its own **E-mail subject** and **E-mail body**
  templates (Calendar → Settings, `email_subject` and `email_body`),
  separate from the webhook message. **The e-mail body no longer uses the
  Notification message**; copy it to E-mail body to keep the old text.
- Docs and help texts say plainly that each event type needs **Send
  e-mail** on, as well as Direct e-mail.
- Fixed: skipping, moving or bringing back an occurrence failed with
  `NameError: name '_' is not defined`.
- **Edit** on an occurrence replaces **Skip**: skip it, move it to another
  time, or bring it back. Each needs a reason, shown on the event page,
  in the event's journal and change log, and in the *Changed* notice
  (`occurrence` in the Event Rule data; `occurrence` and `reason` in the
  message template).
- Calendar feeds and `.ics` files show moved occurrences at their new time
  (`RECURRENCE-ID`).
- REST API: `occurrence-changes/` endpoint. **The event's `skipped` field
  is replaced by `occurrence_changes`**; existing skipped occurrences are
  kept with the reason "Skipped before reasons were recorded."
- Fixed: the Event Rules list failed to load (TypeError) when a rule
  used a calendar notice as a trigger.
- The calendar's notices (Event scheduled, Event reminder, ...) are now
  listed as Event Rule triggers. They were registered too late for
  NetBox's form to see them.
- Event Rule data has `display` and `detail`, so webhook body templates
  written for netbox-change-approval also work for calendar notices.
- Docs: Event Rules should use the calendar's notice types, not Object
  created/updated/deleted.
- Objects on an event combine as a rule: different kinds AND together
  (a device role and a location: that role's devices in that location),
  several of one kind OR together. Devices and VMs picked one by one are
  always included. **Existing events with several kinds linked now cover
  fewer devices**; check their Affected Devices.
- Objects can be excluded: an excluded location, role or device leaves
  those devices out (`exclude` on event objects in the API).
- The event form's objects section shows only the kinds in use, with an
  **Add objects...** menu to match, include or exclude another kind.
- Affected devices, object pages, conflict warnings, notifications
  (subscribers, contacts, `objects_text`, `excluded`, `devices`) and the
  iCal feed follow the rule.
- Upcoming Events widget: the start time now sits on the same line as the event title.
- The event form has one picker per kind of object (devices, sites, device
  roles, ...) and shows what is already linked when editing. Removing an
  object from a picker unlinks it.
- Linking a device role, device type, manufacturer, platform or tenant
  covers its devices, like sites and locations already did.
- The event page lists every affected device.
- Unlinking an object from the event page asks to confirm first.
- Reminders are set with tick boxes for common times (15 minutes to
  1 week before) plus a box for any other time. On an event, pick whether
  it uses the event type's reminders, its own, or none. Event and event
  type pages show reminder times in words.
- User groups can be added to an event as people. Each member counts as
  on the event, for notices and for editing.
- Users are shown by full name (username when no name is set) on event
  pages, the event list, user pickers and the iCal feed.
- Removing a person from an event asks for confirmation.

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
