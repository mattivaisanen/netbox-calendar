# Using the calendar

Everything is under **Calendar** in the NetBox menu.

## Views

- **Month** (Calendar → Month): a month grid of colored events, past ones
  faded and cancelled ones struck through. Click a day's **+** to add an
  event on it.
- **Timeline** (Calendar → Timeline): running and upcoming events in date
  order, grouped by day, with impact and linked objects. **Show past**
  looks back instead; **Further ahead** and **Further back** widen the
  range.
- **Events** (Calendar → Events): the standard NetBox list, with filters,
  export, bulk edit, bulk import and bulk delete.
- **Upcoming Events** dashboard widget: the next events, set per widget to
  a number of days ahead, a maximum count, some event types or only your
  own events.

Month and Timeline share the filters above them: type, status, tag, and
*Owned by or including me*. Your filters stay when you switch between them.

## Scheduling an event

**Calendar → Events → Add**, **+** on the month view, **Schedule** on an
object's Events card, or **Schedule Event** above an object list.

| Field | Notes |
|---|---|
| Title, Type | The type sets the color, default length, reminders and who is notified. |
| Status | *Planned*, *Confirmed*, *In progress*, *Done* or *Cancelled*. |
| Impact | *None*, *Degraded* or *Outage*. Shown on the calendar; outage and degraded events show as busy in calendar apps. |
| Start, End | Empty end: the type's default length. |
| All day | Whole days in NetBox's time zone. End is then the last day. |
| Owner | Empty on a new event: you. Notified, and may always edit. |
| Repeat | See [Repeating events](#repeating-events). |
| Reminders | *Same as the event type*, *No reminders*, or *Pick for this event*: tick common times (15 minutes to 1 week before) and type any others in **Other times**, such as `3d, 45m`. |
| Link objects of type, Objects | Objects the event affects. More can be linked from the event page. |
| Filter object type, Filter | An event for every object matching a filter; see [Events by filter](#events-by-filter). |
| Role, Users, Contacts | People to add with one role. More from the event page. |

Times are in NetBox's `TIME_ZONE`.

## Repeating events

Pick how the event repeats:

| Repeat | Example |
|---|---|
| Daily | every 3 days |
| Weekly | every 2 weeks on Tuesday and Friday |
| Monthly on the same date | every month on the 15th |
| Monthly on the same weekday | every 3 months on the 1st Monday, or the last Sunday |
| Yearly | every year on 1 September |
| Custom | any iCal rule (RFC 5545 `RRULE`) |

Each can end on a date or after a number of times, or run without an end.
The monthly weekday follows the start: a start on the 4th Thursday repeats
on the 4th Thursday, a start on the 5th (last) Thursday on the last one.

A custom rule is written as in iCal, without `RRULE:`:

```text
FREQ=MONTHLY;BYDAY=WE;BYSETPOS=-1                  last Wednesday of each month
FREQ=YEARLY;BYMONTH=3,9;BYDAY=1SA                  first Saturday of March and September
FREQ=WEEKLY;INTERVAL=2;BYDAY=SA,SU;UNTIL=20271231  every other weekend until the end of 2027
```

Occurrences keep their local time across daylight saving changes: an event
at 22:00 stays at 22:00.

The event page lists the next occurrences. **Skip** calls off one
occurrence without touching the rest, and **Bring back** undoes it. Editing
the event changes every occurrence.

## Objects

Link any NetBox object: devices, interfaces, sites, racks, circuits,
virtual machines, IP prefixes, and other plugins' models.

- **On the event form:** pick a type, then the objects.
- **On the event page:** **Link Objects** adds objects of another type,
  **Unlink** removes one.
- **From an object list:** tick the rows, then **Schedule Event** above the
  table. The form opens with those objects linked.
- **From an object page:** **Schedule** on its Events card.

### On object pages

Every linked object's page has an **Events** card with its running and
upcoming events, and a banner while one runs. **All events linked here**
opens the event list for the object.

With *Scope roll-up* on (the default), events higher up also show:

| On the page of | Also shows events on |
|---|---|
| Device | its site, the site's region and site group (and their parents), its location (and parents), rack and cluster |
| Interface, port, other device component | its device, and everything the device shows |
| Virtual machine | its site (and region, site group), cluster and host device |
| Rack | its site (and region, site group) and location (and parents) |
| Location | its site (and region, site group) and parent locations |
| Site | its region and site group (and their parents) |
| Region, site group | their parents |

### Events by filter

An event can cover every object matching a filter instead of a fixed list,
for example every device with a certain platform. Pick the object type and
write the filter as on that type's list URL:

```text
platform=ios-xe&status=active
site_id=12&role=access-switch
tag=edge&has_primary_ip=true
```

The filter is checked each time the event is shown, so objects added later
are included. Unknown filter names are refused when saving. The event page
shows the matching objects and links to the full list.

## People

Each event has an **owner** and any number of people in **roles**. A person
is a NetBox user, or a contact (Organization → Contacts) for someone without
an account, such as a vendor engineer.

Roles (Calendar → Event Roles) decide:

- **Can edit:** people in the role may change the event.
- **Notify:** people in the role get the event's notices.

**Add Person** on the event page adds one; **Change role** and **Remove**
change them.

### Who can edit an event

NetBox's own permissions always apply first: changing an event needs
`change` on Event, deleting it `delete`. With *Restrict editing* on (the
default), a user also needs to be one of:

- the event's owner
- on the event in a role that can edit
- a superuser
- given the additional action `manage` on Event (Admin → Permissions)

Others can view the event but see no Edit or Delete button, and the API
refuses their changes. Turn *Restrict editing* off to use NetBox
permissions only.

## Change freezes

An event type with **Change freeze** marks a period when no changes should
be made. While such an event runs, every NetBox page shows a red **Change
freeze** button in the top bar, linking to it. The plugin does not block
changes itself.

## Overlapping events

The event page warns when another event overlaps its next occurrence on
the same linked object, so two teams don't take down the same device at
once.

## Status

Single events move to *In progress* at their start and *Done* at their end
(the *Automatic status* setting, on by default). The status of a repeating
event stays as set, because it covers the whole series. Cancel an event,
rather than deleting it, so people are told; deleting notifies nobody.

## In Outlook and other calendars

- **Your feed** (Calendar → Calendar Feed): a personal address to subscribe
  to in Outlook, Google Calendar, Apple Calendar or on a phone. It holds
  the events you may view and updates when they change. The address works
  without a NetBox login, so keep it private; **Reset Address** replaces it.
  Add filters to the address to narrow it, as on the event list:
  `?impact=outage`, `?mine=true`, `?event_type=upgrade&status=confirmed`.
- **One event:** **Add to Calendar** on the event page downloads it as an
  `.ics` file.
- **E-mail invites:** with direct e-mail on, notices arrive with the event
  attached as an invite (a cancellation for cancelled events). See
  [notifications.md](notifications.md#direct-e-mail).

Calendar apps refresh subscriptions on their own schedule, often every few
hours.

## Change requests from netbox-change-approval

If netbox-change-approval is installed, its approved change requests with an
**Apply at** time appear on the month view and timeline (the *Change
requests on the calendar* setting). The calendar reads them only; it does
not need the plugin.

netbox-change-approval gates changes to every NetBox model, the calendar's
included. To schedule events without approval, add approval rules there
with the outcome *Skip approval* for **Calendar > Event**, **Event Type**
and **Event Role**, or list them in its `exempt_models` (see
[configuration.md](configuration.md#with-netbox-change-approval)).

## REST API

Under `/api/plugins/calendar/`:

| Endpoint | |
|---|---|
| `events/` | Events. Filters as on the event list, plus `after`, `before` (ISO times) and `object_type` + `object_id` (`?object_type=dcim.device&object_id=7`). A new event's owner is the API user unless `event_owner` is given. |
| `event-objects/` | Links between events and objects (`event`, `object_type`, `object_id`). |
| `event-people/` | People on events (`event`, `role`, and `user` or `contact`). |
| `event-types/`, `event-roles/` | Types and roles. |

Event responses include the recurrence in words and the linked objects.
Changing an event, its links or its people follows the same edit rule as
the web pages. The links and people endpoints also need NetBox permissions
on Event Object and Event Person.
