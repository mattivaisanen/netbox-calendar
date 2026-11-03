"""
Telling people about events: NetBox's own notifications (the bell), a
NetBox event that Event Rules can turn into an e-mail, Teams message or any
other webhook, and optionally a direct e-mail with a calendar invite.

What is sent about is set per event type (EventType.notify_on): an event
scheduled, changed or cancelled (signals.py, after the database commit),
and reminders, starts and ends (jobs.py, the background job). Skipping,
moving or bringing back one occurrence is a change; the notice names the
occurrence and the reason.

Who hears is also set per event type: the owner, people on the event whose
role is notified, a NetBox group, users subscribed to an object the event
concerns, and contacts assigned to one (e-mail only). Those objects are the
included links and the affected devices and VMs; excluded ones are left out. Whoever made a change
is not told about their own change.
"""
import logging
from datetime import datetime, timedelta

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.mail import EmailMultiAlternatives
from django.utils import formats, timezone
from django.utils.translation import gettext as _

from core.models import ObjectType
from extras.events import EventContext, flush_events
from extras.models import Notification, Subscription
from tenancy.models import ContactAssignment

from . import ical, message_template
from .event_types import EVENT_TYPES
from .lookup import Targets, affected_devices, affected_vms, describe, linked_objects
from .models import EventObject, display_name
from .plugin_settings import get_setting, on

logger = logging.getLogger('netbox_calendar.notifications')

def person(user):
    """A user as webhook data."""
    if user is None:
        return None
    return {
        'id': user.pk,
        'username': user.username,
        'name': display_name(user),
        'email': user.email or '',
    }


#: At most this many affected devices (and as many VMs) are listed in a notice, or looked up for subscribers and
#: contacts.
DEVICES_LISTED = 50
DEVICES_LOOKED_UP = 500


def _linked_objects(event):
    # Included links; a link to a deleted object comes back as its EventObject: left out.
    objects, _total = linked_objects(event, limit=50)
    return [obj for obj in objects if not isinstance(obj, EventObject)]


def _concerned_objects(event):
    """The included objects and the affected devices and VMs, for subscribers and contacts."""
    objects = _linked_objects(event)
    seen = {(type(obj), obj.pk) for obj in objects}
    for found in (affected_devices(event), affected_vms(event)):
        for obj in found[:DEVICES_LOOKED_UP]:
            if (type(obj), obj.pk) not in seen:
                objects.append(obj)
    return objects


def _object(obj):
    """An object as webhook data."""
    return {
        'type': obj._meta.label_lower, 'id': obj.pk, 'name': str(obj),
        'link': ical.absolute_url(obj.get_absolute_url()) if hasattr(obj, 'get_absolute_url') else '',
    }


def recipients(event, actor=None):
    """
    (users, contacts) to tell about `event`: active users, and
    (name, e-mail) of contacts and people without a NetBox account.
    `actor` (who made the change) is left out.
    """
    event_type = event.event_type
    users, contacts = [], []
    if event_type.notify_owner and event.event_owner is not None:
        users.append(event.event_owner)
    if event_type.notify_people:
        for row in event.people.select_related('role', 'user', 'group', 'contact'):
            if not row.role.notify:
                continue
            if row.user_id or row.group_id:
                # A group stands for all its members.
                users += row.users
            elif row.contact.email:
                contacts.append((str(row.contact), row.contact.email))
    if event_type.notify_group_id:
        users += list(event_type.notify_group.users.all())
    if event_type.notify_subscribers or event_type.notify_contacts:
        objects = _concerned_objects(event)
        keys = [(ContentType.objects.get_for_model(obj), obj.pk) for obj in objects]
        if event_type.notify_subscribers:
            for content_type, pk in keys:
                users += [row.user for row in Subscription.objects.filter(
                    object_type=content_type, object_id=pk,
                ).select_related('user')]
        if event_type.notify_contacts:
            for content_type, pk in keys:
                for row in ContactAssignment.objects.filter(
                    object_type=content_type, object_id=pk,
                ).select_related('contact'):
                    if row.contact.email:
                        contacts.append((str(row.contact), row.contact.email))

    unique_users, seen = [], set()
    for user in users:
        if user.pk in seen or not user.is_active or (actor is not None and user.pk == actor.pk):
            continue
        seen.add(user.pk)
        unique_users.append(user)
    taken = {user.email.lower() for user in unique_users if user.email}
    unique_contacts = []
    for name, email in contacts:
        if email.lower() not in taken:
            taken.add(email.lower())
            unique_contacts.append((name, email))
    return unique_users, unique_contacts


def when_text(event, start, end):
    """"4 Nov 2026 22:00 - 23:30", or dates only for all-day events."""
    start, end = timezone.localtime(start), timezone.localtime(end)
    if event.all_day:
        last = timezone.localtime(end - timedelta(seconds=1)).date()
        first = start.date()
        if first == last:
            return formats.date_format(first)
        return f'{formats.date_format(first)} - {formats.date_format(last)}'
    if start.date() == end.date():
        return f'{formats.date_format(start, "DATETIME_FORMAT")} - {formats.time_format(end)}'
    return f'{formats.date_format(start, "DATETIME_FORMAT")} - {formats.date_format(end, "DATETIME_FORMAT")}'


#: What can happen to one occurrence (occurrence_info()).
SKIPPED, MOVED, RESTORED = 'skipped', 'moved', 'restored'


def _moment_text(event, value):
    value = timezone.localtime(value)
    return formats.date_format(value, 'DATE_FORMAT' if event.all_day else 'DATETIME_FORMAT')


def occurrence_info(change, action, reason=None):
    """
    What happened to one occurrence, as notice data: `action` is SKIPPED,
    MOVED or RESTORED; `reason` defaults to the change's own reason.
    """
    event = change.event
    original = _moment_text(event, change.original_start)
    if action == MOVED:
        text = _('Occurrence of {original} moved to {when}').format(
            original=original, when=when_text(event, change.start, change.end),
        )
    elif action == RESTORED:
        text = _('Occurrence of {original} brought back').format(original=original)
    else:
        text = _('Occurrence of {original} skipped').format(original=original)
    return {
        'action': action,
        'original_start': change.original_start.isoformat(),
        'start': change.start.isoformat() if action == MOVED else None,
        'end': change.end.isoformat() if action == MOVED else None,
        'reason': change.reason if reason is None else reason,
        'text': str(text),
    }


def occurrence_times(event, info):
    """(start, end) a notice about one occurrence is about: the new times of a moved one."""
    if info['action'] == MOVED:
        return datetime.fromisoformat(info['start']), datetime.fromisoformat(info['end'])
    start = datetime.fromisoformat(info['original_start'])
    return start, start + event.duration


def event_data(event, kind, users, contacts, start, end, actor=None, offset=None, occurrence=None):
    """
    Everything a message about the notice needs, for Event Rules
    (conditions) and webhook body templates ({{ data.<key> }}).
    `occurrence` is occurrence_info() for a notice about one occurrence.
    """
    data, _context = _data_and_context(event, kind, users, contacts, start, end, actor, offset, occurrence)
    return data


def _data_and_context(event, kind, users, contacts, start, end, actor, offset, occurrence):
    """event_data(), and the variables the message templates get (message_template.py)."""
    targets = Targets(event)
    objects = _linked_objects(event)
    objects_text = describe(event, targets)
    devices = list(affected_devices(event, targets).order_by('name')[:DEVICES_LISTED])
    vms = list(affected_vms(event, targets).order_by('name')[:DEVICES_LISTED])
    link = ical.absolute_url(event.get_absolute_url())
    notice = str(EVENT_TYPES[kind][1])
    context = {
        'notice': notice,
        'title': event.title,
        'event_type': event.event_type.name,
        'status': str(event.get_status_display()),
        'impact': str(event.get_impact_display()) if event.impact != 'none' else '',
        'start': formats.date_format(timezone.localtime(start), 'DATETIME_FORMAT'),
        'end': formats.date_format(timezone.localtime(end), 'DATETIME_FORMAT'),
        'when': when_text(event, start, end),
        'objects': objects_text,
        'owner': (person(event.event_owner) or {}).get('name', ''),
        'by': (person(actor) or {}).get('name', ''),
        'link': link,
        'comment': event.description,
        'occurrence': occurrence['text'] if occurrence else '',
        'reason': occurrence['reason'] if occurrence else '',
    }
    emails = [user.email for user in users if user.email] + [email for _name, email in contacts]
    # One short line on top of the title, like netbox-change-approval's
    # `detail`, so one body template can serve both plugins.
    detail = f"{context['when']}, by {context['by']}" if context['by'] else context['when']
    data = {
        'message': message_template.render(get_setting('message'), context),
        'notice': kind,
        'event': notice,
        'id': event.pk,
        'display': str(event),
        'detail': detail,
        'title': event.title,
        'event_type': event.event_type.name,
        'status': event.status,
        'impact': event.impact,
        'start': start.isoformat(),
        'end': end.isoformat(),
        'when': context['when'],
        'all_day': event.all_day,
        'recurrence': event.recurrence_text(),
        'reminder_minutes': offset,
        # A skipped, moved or brought back occurrence, with the reason; None otherwise.
        'occurrence': occurrence,
        'link': link,
        'url': event.get_absolute_url(),
        # What the event concerns: in words, the included objects, the excluded ones and the affected devices and VMs.
        'objects_text': objects_text,
        'objects': [_object(obj) for obj in objects],
        'excluded': [_object(obj) for obj in targets.excluded],
        'devices': [_object(device) for device in devices],
        'vms': [_object(vm) for vm in vms],
        'owner': person(event.event_owner),
        'people': [
            {'role': row.role.name, 'name': row.person_name, 'email': row.email}
            for row in event.people.select_related('role', 'user', 'group', 'contact')
        ],
        'by': person(actor),
        'recipients': [person(user) for user in users],
        'contacts': [{'name': name, 'email': email} for name, email in contacts],
        'recipient_emails': ';'.join(emails),
    }
    return data, context


def notify(event, kind, start=None, end=None, actor=None, offset=None, occurrence=None):
    """
    Send notice `kind` (a NoticeChoices value) about `event`, or about one
    occurrence of it (`start`, `end`), if its event type asks for it.
    `occurrence` is occurrence_info() when one occurrence was skipped,
    moved or brought back. Returns the number of people told.
    """
    if kind not in (event.event_type.notify_on or ()):
        return 0
    if occurrence and start is None:
        start, end = occurrence_times(event, occurrence)
    start = start or event.start
    end = end or event.end
    users, contacts = recipients(event, actor)
    data, context = _data_and_context(event, kind, users, contacts, start, end, actor, offset, occurrence)
    if users and on('notifications'):
        _bell(event, kind, users)
    # Event Rules run even when nobody is told directly, such as for a
    # webhook that posts every event to a team channel.
    _fire_event(event, kind, data, actor)
    if (users or contacts) and on('direct_email') and event.event_type.send_email:
        send_email(event, data, context, users, contacts)
    return len(users) + len(contacts)


def _bell(event, kind, users):
    """A NetBox notification for each user, replacing their earlier one about the event."""
    content_type = ContentType.objects.get_for_model(event)
    for user in users:
        Notification.objects.filter(object_type=content_type, object_id=event.pk, user=user).delete()
        Notification.objects.create(
            user=user, object_type=content_type, object_id=event.pk, event_type=EVENT_TYPES[kind][0],
        )


def _fire_event(event, kind, data, actor):
    """Run NetBox Event Rules for this notice (webhooks, scripts, ...)."""
    try:
        context = EventContext(
            object_type=ObjectType.objects.get_for_model(event),
            object_id=event.pk,
            object=event,
            event_type=EVENT_TYPES[kind][0],
            snapshots=None,
            user=actor or event.event_owner,
            data=data,
        )
        flush_events([context])
    except Exception:
        logger.exception('Event rules for event #%s failed', event.pk)


def send_email(event, data, context, users, contacts):
    """
    E-mail the notice through NetBox's EMAIL settings, with the event as a
    calendar invite (a cancelled event as a cancellation), so it lands in
    Outlook as a meeting. Subject and body come from their own templates
    (email_subject, email_body), not the webhook message.
    """
    addresses = [(display_name(user), user.email) for user in users if user.email]
    addresses += list(contacts)
    if not addresses:
        return
    method = 'CANCEL' if event.status == 'cancelled' else 'REQUEST'
    organizer = None
    if event.event_owner is not None and event.event_owner.email:
        organizer = (display_name(event.event_owner), event.event_owner.email)
    invite = ical.calendar(
        [event], name=event.title, method=method, attendees=addresses, organizer=organizer,
        describe=lambda _event: data['objects_text'],
    )
    subject = message_template.render(get_setting('email_subject'), context, message_template.EMAIL_SUBJECT)
    # A header must be one line.
    subject = ' '.join(subject.split())[:250]
    body = message_template.render(get_setting('email_body'), context, message_template.EMAIL_BODY)
    message = EmailMultiAlternatives(
        subject=subject, body=body, from_email=settings.SERVER_EMAIL,
        to=[email for _name, email in addresses],
    )
    message.attach('event.ics', invite, f'text/calendar; method={method}; charset=UTF-8')
    try:
        message.send()
    except Exception:
        logger.exception('E-mail about event #%s could not be sent', event.pk)
