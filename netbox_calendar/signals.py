"""
Notices about edits: an event scheduled, changed or cancelled, sent once
the database transaction commits (so a rolled back save tells nobody).

Saving an event with its links and people is many saves in one
transaction; they become one notice per event. A new event is
"scheduled" even when its links are added after it; an event whose status
becomes cancelled is "cancelled"; any other change to its time, title,
type, status, recurrence, links or people is "changed".

Skipping, moving or bringing back one occurrence (OccurrenceChange) is
"changed" too: the notice names the occurrence and the reason, and the
event's journal gets an entry saying the same.

Deleting an event sends nothing: cancel it instead so people hear.
"""
import logging
import threading
from functools import partial

from django.db import transaction
from django.db.models.signals import post_delete, post_save, pre_save
from django.dispatch import receiver

from extras.choices import JournalEntryKindChoices
from extras.models import JournalEntry

from . import notifications
from .choices import EventStatusChoices, NoticeChoices
from .models import Event, EventObject, EventPerson, OccurrenceChange

# Changes to these fields are worth telling people about.
WATCHED_FIELDS = ('title', 'event_type_id', 'status', 'start', 'end', 'all_day', 'rrule', 'impact')

# When one transaction makes several notices for an event, the first one in
# this list wins.
STRENGTH = (NoticeChoices.SCHEDULED, NoticeChoices.CANCELLED, NoticeChoices.CHANGED)

logger = logging.getLogger('netbox_calendar.notifications')

_state = threading.local()


def _pending():
    if not hasattr(_state, 'pending'):
        _state.pending = {}
    return _state.pending


def _occurrences():
    """{event pk: occurrence_info()} of the occurrence changes waiting to be told."""
    if not hasattr(_state, 'occurrences'):
        _state.occurrences = {}
    return _state.occurrences


def _actor():
    from netbox.context import current_request

    request = current_request.get()
    user = getattr(request, 'user', None)
    return user if user is not None and user.is_authenticated else None


def _registered(pk):
    """Whether a commit callback for event `pk` is waiting in this transaction."""
    connection = transaction.get_connection()
    if not connection.in_atomic_block:
        return False
    return any(getattr(item[1], 'calendar_event', None) == pk for item in connection.run_on_commit)


def queue(pk, kind, occurrence=None):
    """
    Send notice `kind` about event `pk` when the transaction commits.
    `occurrence` (notifications.occurrence_info()) names one occurrence
    that changed; the last one in a transaction is told.
    """
    pending = _pending()
    registered = _registered(pk)
    # Without a waiting callback, anything left here is from a rolled back transaction.
    if not registered:
        _occurrences().pop(pk, None)
    if occurrence is not None:
        _occurrences()[pk] = occurrence
    current = pending.get(pk) if registered else None
    if current is not None and STRENGTH.index(current) <= STRENGTH.index(kind):
        return
    pending[pk] = kind
    if not registered:
        callback = partial(_send, pk, _actor())
        callback.calendar_event = pk
        transaction.on_commit(callback)


def _send(pk, actor):
    kind = _pending().pop(pk, None)
    occurrence = _occurrences().pop(pk, None)
    if kind is None:
        return
    event = Event.objects.filter(pk=pk).select_related('event_type', 'event_owner').first()
    if event is None:
        return
    try:
        notifications.notify(
            event, kind, actor=actor, occurrence=occurrence if kind == NoticeChoices.CHANGED else None,
        )
    except Exception:
        logger.exception('Notice about event #%s failed', pk)


@receiver(pre_save, sender=Event)
def remember_before(sender, instance, **kwargs):
    """Keep the stored values, to see after the save what changed."""
    if instance.pk:
        instance._calendar_before = Event.objects.filter(pk=instance.pk).values(*WATCHED_FIELDS).first()


@receiver(post_save, sender=Event)
def event_saved(sender, instance, created, raw=False, **kwargs):
    if raw or getattr(instance, '_calendar_quiet', False):
        return
    if created:
        queue(instance.pk, NoticeChoices.SCHEDULED)
        return
    before = getattr(instance, '_calendar_before', None) or {}
    changed = [name for name in WATCHED_FIELDS if before.get(name) != getattr(instance, name)]
    if not changed:
        return
    if 'status' in changed and instance.status == EventStatusChoices.CANCELLED:
        queue(instance.pk, NoticeChoices.CANCELLED)
    elif changed != ['status'] or instance.status not in (EventStatusChoices.IN_PROGRESS, EventStatusChoices.DONE):
        # Moving to in progress or done is what the start and end notices are for.
        queue(instance.pk, NoticeChoices.CHANGED)


@receiver(post_save, sender=EventObject)
@receiver(post_save, sender=EventPerson)
@receiver(post_delete, sender=EventObject)
@receiver(post_delete, sender=EventPerson)
def part_changed(sender, instance, raw=False, **kwargs):
    if raw:
        return
    # A link removed because its event is being deleted: nothing to say.
    if Event.objects.filter(pk=instance.event_id).exists():
        queue(instance.event_id, NoticeChoices.CHANGED)


def _journal(event, info, actor):
    """A journal entry on the event saying what happened to the occurrence, and why."""
    JournalEntry.objects.create(
        assigned_object=event, created_by=actor,
        kind=JournalEntryKindChoices.KIND_INFO if info['action'] == notifications.RESTORED
        else JournalEntryKindChoices.KIND_WARNING,
        comments=f"{info['text']}. Reason: {info['reason']}",
    )


@receiver(post_save, sender=OccurrenceChange)
def occurrence_saved(sender, instance, raw=False, **kwargs):
    if raw:
        return
    action = notifications.SKIPPED if instance.is_skipped else notifications.MOVED
    info = notifications.occurrence_info(instance, action)
    _journal(instance.event, info, _actor())
    queue(instance.event_id, NoticeChoices.CHANGED, info)


@receiver(post_delete, sender=OccurrenceChange)
def occurrence_restored(sender, instance, **kwargs):
    # Deleted along with its event: nothing to say.
    event = Event.objects.filter(pk=instance.event_id).first()
    if event is None:
        return
    instance.event = event
    # The reason for bringing it back is the change log message (set by the page and the API).
    info = notifications.occurrence_info(instance, notifications.RESTORED, instance._changelog_message or '')
    _journal(event, info, _actor())
    queue(event.pk, NoticeChoices.CHANGED, info)
