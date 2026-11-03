# Notifications, webhooks and e-mail

The calendar tells people about events in three ways:

- **NetBox notifications** (the bell), on by default.
- **NetBox Event Rules**: webhooks, scripts and anything else NetBox can
  run, with the message and recipients in the event data.
- **Direct e-mail** through NetBox's own e-mail settings, with the event
  attached as a calendar invite. Off by default.

## What is sent

| Notice | Sent when | NetBox event type |
|---|---|---|
| Scheduled | an event is created | Event scheduled |
| Changed | its time, title, type, status, impact, recurrence, objects or people change, or one occurrence is skipped, moved or brought back | Event changed |
| Cancelled | its status becomes *Cancelled* | Event cancelled |
| Reminder | at each reminder time before each occurrence | Event reminder |
| Started | an occurrence starts | Event started |
| Ended | an occurrence ends | Event ended |

Saving an event together with its objects sends one notice, not one per
change, and so does adding several people at once. People added after an
event is created get the *Changed* notice, not *Scheduled*. Moving to *In progress* or *Done* sends no *Changed*
notice; that is what *Started* and *Ended* are for. Deleting an event
sends nothing, so cancel it instead.

Reminders, *Started* and *Ended* come from NetBox's background worker,
which checks every five minutes. A notice more than an hour late (for
example after the worker was down) is dropped; `reminder_grace_minutes`
changes the hour. Each is sent once per occurrence.

## Who gets them

Each event type (Calendar → Event Types) decides:

| Field | |
|---|---|
| Notify on | Which notices are sent at all. |
| Reminders | Default reminder times: tick common ones, type others such as `3d, 45m`. None: no reminders. An event can use these, pick its own, or have none. |
| Notify owner | The event's owner. |
| Notify people | People on the event whose role has **Notify** on. A group on the event means each of its members. |
| Notify group | Every member of a NetBox group, for every event of the type. |
| Notify subscribers | Users subscribed to a linked object (the **Subscribe** button on NetBox object pages). |
| Notify contacts | Contacts assigned to a linked object (Organization → Contacts), by e-mail only. |
| Send e-mail | Also e-mail them, with direct e-mail turned on. |

The user who made a change is not told about it. Inactive users are
skipped, and each person is told once.

Contacts, and people on the event added as contacts, have no NetBox
account. They get direct e-mail, and they are in the Event Rule data for
webhooks to reach them.

## Event Rules and webhooks

Use NetBox's Event Rules to send notices anywhere: a chat channel, a
ticketing system, an SMS gateway, a script.

1. Set the plugin's `netbox_url` (Calendar → Settings), so links in the
   data are complete.
2. **Operations → Webhooks → Add**: the address of the receiving service,
   method `POST`, content type `application/json`, and a body template
   such as:

   ```jinja
   {
     "text": {{ data.message | tojson }},
     "event": {{ data.title | tojson }},
     "starts": {{ data.start | tojson }},
     "impact": {{ data.impact | tojson }},
     "to": {{ data.recipient_emails | tojson }}
   }
   ```

3. **Operations → Event Rules → Add**: object type **Calendar > Event**,
   the event types from the table above (*Event reminder*, *Event
   cancelled*, ...), action type **Webhook**, and the webhook.

Event Rules run even when nobody is notified directly, so a rule can post
every event to a team channel. They run only for notices the event's type
sends (its **Notify on** list).

Pick the calendar's own event types, not NetBox's *Object created*,
*Object updated* and *Object deleted*. Those carry the raw API object
(no `message`, `link` or recipients), fire once per saved row (an event,
each of its objects and people), and a body template that reads a missing
field with `tojson` fails in the worker, so nothing arrives.

Add **conditions** to the rule to narrow it, for example only outages:

```json
{"attr": "impact", "value": "outage"}
```

or only events of one type:

```json
{"attr": "event_type", "value": "Planned downtime"}
```

### The data

Available in body templates as `{{ data.<field> }}` and in conditions:

| Field | Example |
|---|---|
| `message` | the ready message text, see [below](#the-message) |
| `notice` | `reminder` (`scheduled`, `changed`, `cancelled`, `reminder`, `started`, `ended`) |
| `event` | `Event reminder` |
| `id`, `title` | `42`, `Replace line cards in agg-3` |
| `display` | the event's name, same as `title` |
| `detail` | one short line: `when`, plus `, by <name>` for a change someone made |
| `event_type` | `Planned maintenance` |
| `status`, `impact` | `confirmed`, `degraded` |
| `start`, `end` | the occurrence, ISO 8601: `2026-12-02T21:00:00+02:00` |
| `when` | the occurrence as text: `Dec. 2, 2026, 9 p.m. - 11 p.m.` |
| `all_day` | `false` |
| `recurrence` | `Every month on the 1st Wednesday`, or empty |
| `reminder_minutes` | `120` for a reminder two hours ahead, else `null` |
| `occurrence` | for one occurrence skipped, moved or brought back: `{"action": "moved", "original_start": "...", "start": "...", "end": "...", "reason": "...", "text": "..."}` (`action` is `skipped`, `moved` or `restored`), else `null` |
| `link`, `url` | the event's full address (with `netbox_url`), and its path |
| `objects_text` | what the event concerns, in words: `Device role: Access, and Location: Floor 2. Except: Location: Lab` |
| `objects` | included objects: `[{"type": "dcim.device", "id": 7, "name": "agg-3", "link": "..."}]` |
| `excluded` | excluded objects, each like `objects` |
| `devices` | affected devices (at most 50), each like `objects` |
| `vms` | affected virtual machines (at most 50), each like `objects` |
| `owner`, `by` | `{"id": 3, "username": "...", "name": "...", "email": "..."}`; `by` is who made the change, `null` for notices from the worker |
| `people` | `[{"role": "Technical contact", "name": "...", "email": "..."}]` |
| `recipients` | users to tell, each like `owner` |
| `contacts` | contacts to tell: `[{"name": "...", "email": "..."}]` |
| `recipient_emails` | every recipient's e-mail address, separated by `;` |

### The message

`message` is made from a Jinja2 template, changed on Calendar → Settings
(**Notification message**) or with the `message` key in `configuration.py`.
Direct e-mail has its own templates, see
[below](#e-mail-subject-and-body). The default:

```jinja
{{ notice }}: {{ title }}

{{ event_type }}, {{ when }}

{% if occurrence %}{{ occurrence }}

{% endif %}{% if reason %}Reason: {{ reason }}

{% endif %}{% if objects %}Concerns: {{ objects }}

{% endif %}{% if impact %}Impact: {{ impact }}

{% endif %}{% if by %}By: {{ by }}

{% endif %}{{ link }}
```

Variables: `notice` (*Event reminder*), `title`, `event_type`, `status`,
`impact` (empty for none), `start`, `end`, `when`, `objects` (the
objects in words, as `objects_text`), `owner`, `by`, `link`, `comment` (the description), and for one changed occurrence
`occurrence` (what happened) and `reason`. The
settings page refuses a template that does not render; one that fails
later falls back to the default.

## Direct e-mail

E-mail goes out through NetBox's own `EMAIL` setting in
`configuration.py`, from its `SERVER_EMAIL` address. To use it:

1. Make sure NetBox can send e-mail: `python manage.py sendtestemail
   you@corp.internal` sends a test message.
2. Turn on **Direct e-mail** (Calendar → Settings, or `direct_email` in
   `configuration.py`).
3. Turn on **Send e-mail** on each event type that should e-mail
   (Calendar → Event types → edit). It is off by default, and without it
   no e-mail is sent for that type, even with direct e-mail on.

Recipients are the same people as for the bell, plus contacts. A user or
contact without an e-mail address is skipped. A failed send is logged as
an error by the `netbox_calendar.notifications` logger; reminders, starts
and ends are sent by the background worker, so check its log too.

Each notice is one e-mail to all its recipients, with the event attached
as `event.ics`. Outlook and most mail
programs show it as a meeting invite that can be added to the calendar;
a cancelled event arrives as a cancellation that removes it. The event's
owner is the organizer.

### E-mail subject and body

Subject and body are Jinja2 templates of their own, separate from the
webhook message: **E-mail subject** and **E-mail body** on Calendar →
Settings, or `email_subject` and `email_body` in `configuration.py`. They
get the same variables as [the message](#the-message). The subject is
joined to one line. Defaults:

```jinja
{{ notice }}: {{ title }} ({{ when }})
```

```jinja
{{ notice }}: {{ title }}

When: {{ when }}
Type: {{ event_type }}
Status: {{ status }}
{% if impact %}Impact: {{ impact }}
{% endif %}{% if objects %}Concerns: {{ objects }}
{% endif %}{% if owner %}Owner: {{ owner }}
{% endif %}{% if by %}By: {{ by }}
{% endif %}{% if occurrence %}
{{ occurrence }}
{% endif %}{% if reason %}Reason: {{ reason }}
{% endif %}{% if comment %}
{{ comment }}
{% endif %}
{{ link }}
```
