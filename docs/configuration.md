# Configuration

Settings live under `PLUGINS_CONFIG['netbox_calendar']` in NetBox's
`configuration.py`. All are optional. Superusers can also change every
setting except `reminder_grace_minutes`, `outlook_client_secret`,
`google_client_secret`, `outlook_login_url` and `outlook_graph_url` on
**Calendar → Settings**; see
[below](#changing-settings-in-netbox).

| Key | Default | Description |
|---|---|---|
| `notifications` | `True` | NetBox notifications (the bell) about events. `False` sends none; Event Rules still run. |
| `direct_email` | `False` | E-mail notices through NetBox's `EMAIL` settings, with a calendar invite attached. **Each event type also needs Send e-mail on.** See [notifications.md](notifications.md#direct-e-mail). |
| `ical_feeds` | `True` | Personal calendar feed addresses. `False` makes every feed address return "not found". |
| `restrict_editing` | `True` | Only the owner, people in a role that can edit, superusers and users with the `manage` action may change an event. `False`: NetBox permissions only. See [usage.md](usage.md#who-can-edit-an-event). |
| `conflict_warnings` | `True` | Warn on the event page when another event overlaps on the same object. |
| `freeze_banner` | `True` | The **Change freeze** button in the top bar while a change freeze event runs. |
| `scope_rollup` | `True` | Events on a site, region, location, rack, cluster, device role, device type, platform or tenant also concern its devices and VMs. |
| `change_requests` | `True` | Show netbox-change-approval's scheduled change requests on the month view and timeline, when that plugin is installed. |
| `auto_status` | `True` | Single events move to *In progress* at their start and *Done* at their end. |
| `first_weekday` | `0` | First column of the month view and first day in date pickers: `0` Monday ... `6` Sunday. |
| `netbox_url` | `''` | NetBox's address as users reach it, such as `https://netbox.corp.internal`. Gives full links in notices, e-mails and the iCal feed. Empty: links are paths only, and feed addresses use the address of the browser that opened the feed page. |
| `message` | see [notifications.md](notifications.md#the-message) | Jinja2 template of the notice text: `{{ data.message }}` for webhooks. |
| `email_subject` | see [notifications.md](notifications.md#e-mail-subject-and-body) | Jinja2 template of the direct e-mail subject. |
| `email_body` | see [notifications.md](notifications.md#e-mail-subject-and-body) | Jinja2 template of the direct e-mail body (plain text). |
| `outlook_sync` | `False` | Users can connect their Outlook calendar, and their events are pushed to it. See [calendar-sync.md](calendar-sync.md). |
| `outlook_client_id` | `''` | Application (client) ID of the Entra app registration. |
| `outlook_client_secret` | `''` | Its client secret. Only in `configuration.py`. |
| `outlook_tenant` | `'organizations'` | Who may connect: your tenant ID or domain (your organization only), `'organizations'` (any work or school account) or `'common'` (personal Microsoft accounts too). |
| `google_sync` | `False` | Users can connect their Google Calendar. See [calendar-sync.md](calendar-sync.md#google-calendar-setup). |
| `google_client_id` | `''` | Client ID of the Google OAuth client. |
| `google_client_secret` | `''` | Its client secret. Only in `configuration.py`. |
| `caldav_sync` | `False` | Users can connect a CalDAV calendar (Nextcloud, Fastmail, iCloud, ...) with an app password. See [calendar-sync.md](calendar-sync.md#caldav-setup). |
| `caldav_server` | `''` | The one CalDAV server users connect to. Empty: users type an address. |
| `sync_calendar_name` | `'NetBox'` | Name of the calendar NetBox creates in each connected account. |
| `outlook_login_url`, `outlook_graph_url` | Microsoft's global cloud | For national clouds only. Only in `configuration.py`. |
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

**Calendar → Settings** is shown to superusers only. Its tabs group the
settings: **General**, **Notifications**, **iCal Feed**, **Outlook**,
**Google** and **CalDAV**. **Save** stores only the settings of the open
tab. A number on a tab counts its settings saved on the page.

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
