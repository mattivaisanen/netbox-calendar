# netbox-calendar

A NetBox plugin for planning work on your network. It keeps a calendar of
upgrades, planned downtime, maintenance and any other scheduled event, ties
each event to the NetBox objects it affects, and tells the right people
before it happens.

With it, you can see on a device, site or circuit page what is planned for
it, see in one view what is happening this month, and have the events in
Outlook or any other calendar app.

## How it works

- **Events** have a type (Upgrade, Planned downtime, Planned maintenance,
  Change freeze, or your own), a time, an impact and a status. They can
  repeat on any iCal rule: every month on the 2nd Tuesday, every year on
  1 June, every other week on Monday and Thursday, and so on.
- **Objects:** link an event to any NetBox objects (devices, sites, circuits,
  VMs, other plugins' models). Kinds combine as a rule: "role *core* at site
  *dc-2*, except rack *R4*" covers exactly those devices, including ones
  added later.
- **People:** each event has an owner and people in roles (Technical
  contact, Participant, Observer, or your own). Roles decide who may edit
  the event and who is notified. A whole user group, or a contact without a
  NetBox account, can be added too. Names are shown in full.
- **Notifications** go out when an event is scheduled, changed or cancelled,
  before it starts (reminders), when it starts and when it ends: NetBox's
  bell, NetBox Event Rules (webhooks, scripts) and, if you want, e-mail with
  a calendar invite attached.

## Features

- Month view, a timeline of all events, and an Upcoming Events dashboard
  widget.
- An Events card and a "running now" banner on the page of every linked
  object. Events on a site, location, rack or cluster also show on the
  devices and VMs in it.
- Schedule one event for many objects at once from any object list.
- Personal iCal feed for Outlook, Google Calendar and phones, and an `.ics`
  download per event.
- Change freeze windows, shown in the top bar to everyone while they run.
- A warning when another event overlaps on the same object.
- Skip or move single occurrences of a repeating event, always with a
  reason that shows on the event, in its journal and in notifications.
- Scheduled change requests from netbox-change-approval on the calendar, if
  that plugin is installed. It is not required.
- REST API for everything, and settings superusers change in NetBox.

## Requirements

- NetBox 4.7 or later. NetBox refuses to load the plugin on older versions.
- No other plugins.
- NetBox's background worker (`rqworker`) running, for reminders and
  start/end notices.

## Installation

Install into NetBox's Python environment:

```bash
pip install git+https://github.com/mattivaisanen/netbox-calendar.git
```

Enable it in `configuration.py`:

```python
PLUGINS = ['netbox_calendar']
```

Apply the database changes and restart NetBox and its worker:

```bash
cd /opt/netbox/netbox && python manage.py migrate netbox_calendar
```

The plugin starts with four event types and three roles. Change them under
**Calendar → Event Types** and **Calendar → Event Roles**. Give users the
`netbox_calendar` permissions they need under **Admin → Permissions**.

## Upgrading

```bash
pip install --upgrade --force-reinstall --no-deps git+https://github.com/mattivaisanen/netbox-calendar.git
cd /opt/netbox/netbox && python manage.py migrate
```

Then restart NetBox and its worker. The [changelog](CHANGELOG.md) lists what
each version needs.

## Uninstalling

1. While the plugin is still installed, remove what would break NetBox
   pages once it is gone (notifications, bookmarks, changelog entries and
   the like about events), then optionally its tables:

   ```bash
   cd /opt/netbox/netbox
   python manage.py prepare_calendar_uninstall
   python manage.py migrate netbox_calendar zero
   ```

2. Remove `netbox_calendar` from `PLUGINS` (and `PLUGINS_CONFIG`), restart
   NetBox and its worker, then `pip uninstall netbox-calendar`.

See [docs/installation.md](docs/installation.md#uninstalling) for details.

## Documentation

- [Installation, upgrading and uninstalling](docs/installation.md)
- [Using the calendar](docs/usage.md)
- [Configuration](docs/configuration.md)
- [Notifications, webhooks and e-mail](docs/notifications.md)
- [Developer notes](DEVELOPER.md)
- [Changelog](CHANGELOG.md)

## License

Apache License 2.0, see [LICENSE](LICENSE).
