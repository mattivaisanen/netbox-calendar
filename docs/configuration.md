# Configuration

Settings live under `PLUGINS_CONFIG['netbox_calendar']` in NetBox's
`configuration.py`. All are optional. Superusers can also change every
setting except `reminder_grace_minutes` on **Calendar → Settings**; see
[below](#changing-settings-in-netbox).

| Key | Default | Description |
|---|---|---|
| `notifications` | `True` | NetBox notifications (the bell) about events. `False` sends none; Event Rules still run. |
| `direct_email` | `False` | E-mail notices through NetBox's `EMAIL` settings, with a calendar invite attached, for event types with **Send e-mail** on. See [notifications.md](notifications.md#direct-e-mail). |
| `ical_feeds` | `True` | Personal calendar feed addresses. `False` makes every feed address return "not found". |
| `restrict_editing` | `True` | Only the owner, people in a role that can edit, superusers and users with the `manage` action may change an event. `False`: NetBox permissions only. See [usage.md](usage.md#who-can-edit-an-event). |
| `conflict_warnings` | `True` | Warn on the event page when another event overlaps on the same object. |
| `freeze_banner` | `True` | The **Change freeze** button in the top bar while a change freeze event runs. |
| `scope_rollup` | `True` | Events on a site, location, rack or cluster also show on the devices and VMs in it. |
| `change_requests` | `True` | Show netbox-change-approval's scheduled change requests on the month view and timeline, when that plugin is installed. |
| `auto_status` | `True` | Single events move to *In progress* at their start and *Done* at their end. |
| `first_weekday` | `0` | First column of the month view: `0` Monday ... `6` Sunday. |
| `netbox_url` | `''` | NetBox's address as users reach it, such as `https://netbox.corp.internal`. Gives full links in notices, e-mails and the iCal feed. Empty: links are paths only, and feed addresses use the address of the browser that opened the feed page. |
| `message` | see [notifications.md](notifications.md#the-message) | Jinja2 template of the notice text: `{{ data.message }}` for webhooks and the body of direct e-mail. |
| `reminder_grace_minutes` | `60` | How late a reminder, start or end notice may still be sent, for example after the worker was down. Older ones are dropped. Only in `configuration.py`. |

Example:

```python
PLUGINS_CONFIG = {
    'netbox_calendar': {
        'netbox_url': 'https://netbox.corp.internal',
        'direct_email': True,
        'first_weekday': 6,
    },
}
```

## Changing settings in NetBox

**Calendar → Settings** is shown to superusers only.

- A value saved on the page overrides `configuration.py`. Each setting shows
  where its value comes from: *Saved here*, *From configuration.py* or
  *Default*.
- **Use configuration.py** next to a saved value removes it, so the setting
  follows `configuration.py` again. Saving the same value as
  `configuration.py` removes the saved value too.
- Changes take effect at once in every NetBox process and the background
  worker, without a restart.

## Set in NetBox, not here

- **Event types** (Calendar → Event Types): color, icon, default length,
  change freeze, reminders and who is notified, per type.
- **Event roles** (Calendar → Event Roles): who may edit and who is
  notified.
- **Permissions** (Admin → Permissions): see
  [installation.md](installation.md#first-steps).
- **E-mail server**: NetBox's own `EMAIL` setting in `configuration.py`.
- **Webhooks**: Operations → Event Rules. See
  [notifications.md](notifications.md).

## With netbox-change-approval

netbox-change-approval gates changes to every NetBox model, including
events, event types and event roles. To let calendar changes through
without approval, either:

- add approval rules with the outcome **Skip approval** for the object types
  **Calendar > Event**, **Calendar > Event Type** and **Calendar > Event
  Role** (Change Approval → Approval Rules), or
- list them in its `exempt_models`:

  ```python
  PLUGINS_CONFIG = {
      'netbox_change_approval': {
          'exempt_models': ['netbox_calendar.event', 'netbox_calendar.eventtype', 'netbox_calendar.eventrole'],
      },
  }
  ```

The calendar's background job updates event statuses directly in the
database, so it is not held up by approval either way.
