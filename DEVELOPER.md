# Developer guide

How the plugin is put together, and why. Read this before changing how
events repeat, how notices are sent or who may edit.

## Ground rules

- **NetBox 4.7+ only**, through documented plugin extension points:
  generic views, `register_model_view`, `PluginTemplateExtension`,
  `DashboardWidget`, `system_job`, `netbox.events.EventType`. No
  monkey-patching.
- **No dependency on other plugins.** The netbox-change-approval
  integration reads its model through `apps.get_model()` when it is
  installed, and never imports it.
- **No extra packages** beyond `python-dateutil`, which NetBox already
  installs. The iCal output is written by hand (`ical.py`).

## Module map

| Module | Responsibility |
|---|---|
| `models.py` | `EventType`, `EventRole`, `Event` (NetBox models), `EventObject`, `EventPerson` and `OccurrenceChange` (change-logged parts of an event), plus plain `FeedToken`, `SentNotice` and `PluginSetting`. `all_day_bounds()`. |
| `recurrence.py` | Everything about repeating: validating and normalizing an RRULE, the occurrences in a window, the form's simple choices (`build()` / `parse_simple()`), and the rule in words (`describe()`). |
| `agenda.py` | Turns events (and change requests) into `Entry` rows for the month grid, timeline and widget. |
| `lookup.py` | The targeting rule. `Targets` sorts an event's links into criteria, picked and excluded objects; `concerns()` checks one object, `affected_devices()` builds the same rule as one device query, `events_for_object()` and `describe()` build on them. Also the scope roll-up (`scopes()`) and events by filter. |
| `permissions.py` | The edit rule: owner, editing role, superuser or the `manage` action. `editable()` narrows a queryset, `can_edit()` checks one event. |
| `signals.py` | Edit notices (scheduled, changed, cancelled), collected per transaction and sent on commit. |
| `jobs.py` | The five-minute system job: automatic status, reminders, start and end notices, each sent once (`SentNotice`). |
| `notifications.py` | Recipients, the event data, and the three channels: bell, Event Rules, direct e-mail. |
| `message_template.py` | The Jinja2 notice text, rendered in a sandbox. |
| `ical.py` | RFC 5545 output: feed, `.ics` download and e-mail invites, with a generated `VTIMEZONE`. |
| `plugin_settings.py` | Settings superusers change in NetBox, layered over `configuration.py`. |
| `integrations.py` | netbox-change-approval's scheduled change requests on the calendar. |
| `template_content.py` | The Events card and running banner on object pages, the change freeze badge, and **Schedule Event** above object lists. |
| `widgets.py` | The Upcoming Events dashboard widget. |
| `views.py`, `forms.py`, `filtersets.py`, `tables.py`, `urls.py`, `navigation.py`, `search.py`, `api/` | Standard NetBox UI and REST API. |
| `management/commands/prepare_calendar_uninstall.py` | Removes rows that would break NetBox pages once the plugin is gone. |

## Repeating events

An event stores one `start`/`end` (the first occurrence) and an iCal
`rrule`. Occurrences are computed, never stored:

- `dateutil.rrule` runs on the **local** wall-clock start (NetBox's
  `TIME_ZONE`), so an event at 22:00 stays at 22:00 across daylight saving
  changes. `UNTIL` is stored in UTC (`...Z`), as RFC 5545 requires with a
  zoned start; `normalize()` converts a local one.
- `occurrences(after, before)` starts the rule at `after - duration`, so an
  occurrence that began before the window and is still running is found.
- `OccurrenceChange` rows skip or move one occurrence, keyed by the start
  the rule gives it (`original_start`), always with a reason. Skipped ones
  are left out and moved ones added at their new time. A change whose
  original start the rule no longer produces is ignored
  (`is_occurrence()`). In iCal a skipped one is an `EXDATE`; a moved one is
  a second `VEVENT` with the same `UID` and a `RECURRENCE-ID`, with no
  `EXDATE` (that would hide the override in Outlook and Google).
- Saving or deleting an `OccurrenceChange` writes a journal entry on the
  event and queues a *Changed* notice with `occurrence` data
  (`signals.py`). The reason is also the change log message.
- Searches stop at 5000 occurrences (`MAX_OCCURRENCES`), so an hourly rule
  with no end can't run away.
- `rrule` is a plain string so any rule works; the form's *Repeat* choices
  are a front end that `build()` turns into a rule and `parse_simple()`
  reads back. A rule they can't express shows as *Custom*.

All-day events are stored as local midnight to the midnight after the
last day (`all_day_bounds()`), so overlap checks need no special case. The
form takes the last day and the iCal output writes `VALUE=DATE`.

## Edit notices on commit

Saving an event from the form is several saves in one transaction: the
event, then each link and person. `signals.py` queues one notice per event
and sends it from `transaction.on_commit`, so:

- a rolled-back save tells nobody;
- a new event is *Scheduled*, not *Scheduled* plus a *Changed* per link;
- the strongest notice wins (`STRENGTH`: scheduled, cancelled, changed).

Whether a callback is already waiting is read from
`connection.run_on_commit` (`_registered()`), not from a flag of our own:
a flag would survive a rollback and silence the next save. A `TestCase`
never commits; `tests/utils.commit()` runs the waiting callbacks the way a
commit would.

`_calendar_quiet = True` on an instance saves it without a notice.

## Reminders and status

`CalendarNoticesJob` runs every five minutes on NetBox's worker:

1. `update_statuses()` moves single events to *In progress* and *Done*
   with `QuerySet.update()`: no signals, so no *Changed* notice, and
   netbox-change-approval does not hold it up.
2. For each occurrence in the window, `due_notices()` lists the reminders,
   start and end that fall between their time and that time plus the grace
   (`reminder_grace_minutes`).
3. `_send_once()` inserts a `SentNotice` row first; the unique constraint
   makes a second run (or a second worker) skip it. Rows older than 35 days
   are deleted.

## Notices

`notify()` checks the type's `notify_on`, works out the recipients, builds
the data once and sends it to:

- the **bell**: one `extras.Notification` per user, replacing their earlier
  one about the same event so the list doesn't fill up;
- **Event Rules**: an `EventContext` with the plugin's own event type
  (`netbox_calendar.reminder`, ...) and the data, through `flush_events()`.
  Runs even with no recipients, for channel webhooks;
- **direct e-mail**: `EmailMultiAlternatives` with an `event.ics`
  attachment, `METHOD:REQUEST` or `CANCEL`. `SEQUENCE` is the seconds since
  the event was created at its last save, so calendars take each update as
  newer.

The six event types are registered in `ready()` with a `__str__` that
makes the lazy translation plain text; NetBox's notification menu fails
otherwise.

## The edit rule

NetBox's object permissions decide whether a user may change events at
all. `permissions.editable()` narrows that to events the user owns or is on
with an editing role, unless they are a superuser, have the `manage`
action, or *Restrict editing* is off. Every write path uses it: the edit and
delete views (through `get_queryset()`), bulk views, the event page's own
actions (`EventChangeMixin`), and the API viewsets.

`Event.event_owner` is not called `owner`: NetBox 4.7's form template
renders any `owner` field in its own *Ownership* section.

## The feed

`FeedView` needs no login: the 40-character token in the address stands
for its user, and the events are `restrict()`ed to what that user may
view. The event list's filterset runs on the query string, so any list
filter works on a feed. `ical_feeds` off makes every feed a 404.

## The targeting rule

Links of criteria types (`DEVICE_MATCHES`: site, location, role, ...)
AND across types and OR within one; other included links (devices, VMs,
circuits, ...) are added on top; excluded links (`EventObject.exclude`)
are taken out of both. A device or VM filter is one more criterion.

`concerns()` walks `scopes()` of one object; `affected_devices()` writes
the same rule as a `Q` with `DEVICE_MATCHES`. Change both together:
`tests/test_targets.py` checks they agree. `events_for_object()` picks
candidates by link in SQL, then checks each with `concerns()`.

The form's objects section (`TargetFieldsMixin`) has one field per kind
and mode, named `include__<app>__<model>` or `exclude__<app>__<model>`.
**Add objects...** is an `HTMXSelect` that re-renders the section; a
picker exists when it has values, the event links that kind, or it was
just chosen.

## Migrations

```bash
python manage.py makemigrations netbox_calendar --check --dry-run
```

0001 is the schema; 0002 creates the starting event types and roles with
`get_or_create`, so it is safe to run on a database that has them.

## Running tests

From a NetBox checkout with the plugin installed in editable mode:

```bash
python manage.py test netbox_calendar --keepdb
```

`tests/utils.py` has the helpers: `commit()` (see above), `make_event()`
(saves quietly), `make_device()`, `make_user()` and `grant()`.

## CI

`.github/workflows/test.yml` runs on pull requests and by hand: one job on
NetBox 4.7.0 that migrates, checks for migration drift and runs the tests.
