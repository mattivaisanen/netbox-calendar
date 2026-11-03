# Installation

The plugin is not on PyPI. Install it from GitHub into NetBox's own Python
environment (on a standard install, `source /opt/netbox/venv/bin/activate`
first):

```bash
pip install git+https://github.com/mattivaisanen/netbox-calendar.git
```

To keep it installed across NetBox upgrades, add the same line to
`/opt/netbox/local_requirements.txt`:

```text
git+https://github.com/mattivaisanen/netbox-calendar.git
```

Add it to `configuration.py`:

```python
PLUGINS = [
    'netbox_calendar',
]

# Optional. Every key and its default is in configuration.md.
PLUGINS_CONFIG = {
    'netbox_calendar': {
        'netbox_url': 'https://netbox.corp.internal',
    },
}
```

Create the plugin's tables, then restart NetBox and its background worker:

```bash
cd /opt/netbox/netbox
python manage.py migrate netbox_calendar
sudo systemctl restart netbox netbox-rq
```

The worker sends reminders and start/end notices every five minutes, and
moves single events to *In progress* and *Done*. Without it, the calendar
works but nothing is sent on time.

## First steps

1. **Event types** (Calendar → Event Types): the plugin starts with
   *Upgrade*, *Planned downtime*, *Planned maintenance* and *Change freeze*.
   Each type sets its color, default length, reminders and who is notified.
   Rename, change or delete them, or add your own.
2. **Event roles** (Calendar → Event Roles): *Technical contact* (may edit,
   notified), *Participant* and *Observer* (notified). Add roles such as
   *Vendor* or *Approver* as needed.
3. **Permissions** (Admin → Permissions): give users permissions on the
   object types under *Calendar*:

   | To | Give |
   |---|---|
   | See the calendar and events | `view` on Event (and Event Type, Event Role) |
   | Schedule events | `add` on Event |
   | Edit and delete their own events | `change`, `delete` on Event |
   | Edit and delete every event | `change`, `delete` and the additional action `manage` on Event |
   | Manage types and roles | `add`, `change`, `delete` on Event Type and Event Role |

   Superusers can do everything. The owner of an event and people whose
   role may edit can change it; see
   [usage.md](usage.md#who-can-edit-an-event).
4. **Settings** (Calendar → Settings, superusers only): turn features on
   or off without a restart. See [configuration.md](configuration.md).

## Upgrading

`--force-reinstall --no-deps` makes pip fetch the latest code even if the
version number did not change:

```bash
pip install --upgrade --force-reinstall --no-deps git+https://github.com/mattivaisanen/netbox-calendar.git
cd /opt/netbox/netbox
python manage.py migrate
sudo systemctl restart netbox netbox-rq
```

The [changelog](../CHANGELOG.md) lists what each version needs.

## Uninstalling

Taking the plugin out of `PLUGINS` does not break NetBox by itself, but
leftovers do. NetBox's Notifications, Bookmarks, Subscriptions, Changelog
and Journal Entries pages (and their API lists) give a server error once
they hold a row about an event, event type or role, as does the page of a
tag or custom field used on them. A dashboard with the Upcoming Events
widget falls back to the default one.

`prepare_calendar_uninstall` removes those leftovers while the plugin is
still installed:

- deletes NetBox notifications, bookmarks, subscriptions, changelog and
  journal entries, tag assignments, image attachments, saved table
  configurations and search entries about calendar objects
- takes the calendar's object types off custom fields, custom links, export
  templates, saved filters, tags, event rules and permissions, and deletes
  those that were only about the calendar
- deletes custom fields whose values are calendar objects
- takes the calendar's notices ("Event reminder", ...) off event rules
- removes the Upcoming Events widget from dashboards
- cancels the calendar's queued background jobs

It shows what it will remove and asks first. `--dry-run` only shows.

```bash
cd /opt/netbox/netbox
python manage.py prepare_calendar_uninstall --dry-run
python manage.py prepare_calendar_uninstall
```

Events, types and roles are kept. To delete them too, remove the plugin's
tables:

```bash
python manage.py migrate netbox_calendar zero
```

Then take `netbox_calendar` out of `PLUGINS` and `PLUGINS_CONFIG`, restart
NetBox and its worker, and uninstall the package:

```bash
pip uninstall netbox-calendar
```

Calendar apps subscribed to a feed will show an error or keep the last
events they downloaded. Ask users to remove the subscription.
