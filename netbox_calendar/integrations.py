"""
Optional integrations. Nothing here imports another plugin; this plugin
works the same without them.

netbox-change-approval:

- Its scheduled change requests (approved, waiting for their "Apply at"
  time) are shown on the month view and the timeline as read-only entries.
- Events, event types and roles are NetBox objects, so change-approval
  gates changes to them like any other object; see docs/configuration.md
  for letting them through without approval.
- A new event that waits for approval is already in the database (marked
  pending), but nobody hears about it and it reaches no outside calendar
  until its create request is approved (awaiting_approval(),
  watch_change_requests()). A rejected one is deleted without a word.
"""
import logging
from datetime import timedelta

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.db.models.signals import post_save, pre_save

CHANGE_APPROVAL = 'netbox_change_approval'

# How long a change request is drawn, from its "Apply at" time.
CHANGE_REQUEST_LENGTH = timedelta(minutes=30)

# change-approval's values for a create request that is still open, and one that went through.
ACTION_CREATE = 'create'
STATUS_PENDING = 'pending'
STATUS_APPLIED = 'applied'

logger = logging.getLogger('netbox_calendar.integrations')


def has_change_approval():
    return apps.is_installed(CHANGE_APPROVAL)


def _change_request_model():
    return apps.get_model(CHANGE_APPROVAL, 'ChangeRequest')


def _event_content_type():
    from .models import Event

    return ContentType.objects.get_for_model(Event)


def awaiting_approval(event_ids):
    """
    The ids among `event_ids` of events added through netbox-change-approval
    whose create request is still open. Always empty without that plugin.
    """
    event_ids = [pk for pk in event_ids if pk]
    if not event_ids or not has_change_approval():
        return set()
    try:
        return set(_change_request_model().objects.filter(
            content_type=_event_content_type(), object_id__in=event_ids,
            action=ACTION_CREATE, status=STATUS_PENDING,
        ).values_list('object_id', flat=True))
    except Exception:
        # A change-approval without these fields: hold nothing back, but say so.
        logger.exception('Could not read the open change requests of events')
        return set()


def all_awaiting_approval():
    """A queryset of the ids of every event whose create request is still open (for exclude(pk__in=...))."""
    from .models import Event

    if not has_change_approval():
        return Event.objects.none().values('pk')
    try:
        return _change_request_model().objects.filter(
            content_type=_event_content_type(), action=ACTION_CREATE, status=STATUS_PENDING,
        ).values('object_id')
    except Exception:
        logger.exception('Could not read the open change requests of events')
        return Event.objects.none().values('pk')


#
# Telling people once a new event is approved
#

def _remember_status(sender, instance, **kwargs):
    """Keep a change request's stored status, to see after the save whether it was just approved."""
    instance._calendar_status_before = None
    if instance.pk and getattr(instance, 'action', None) == ACTION_CREATE:
        instance._calendar_status_before = sender.objects.filter(pk=instance.pk).values_list(
            'status', flat=True,
        ).first()


def _approved(sender, instance, created, raw=False, **kwargs):
    """A create request of an event went from open to applied: the event is now "scheduled"."""
    if raw or created or getattr(instance, '_calendar_status_before', None) != STATUS_PENDING:
        return
    if instance.status != STATUS_APPLIED or instance.content_type_id != _event_content_type().pk:
        return
    from . import signals
    from .choices import NoticeChoices

    # As if the event had been added without approval: whoever asked for it is not told about it.
    signals.queue(instance.object_id, NoticeChoices.SCHEDULED, actor=getattr(instance, 'submitted_by', None))
    signals.queue_sync(instance.object_id)


def watch_change_requests():
    """Connect the receivers above to change-approval's ChangeRequest. Called from ready()."""
    if not has_change_approval():
        return
    try:
        model = _change_request_model()
    except LookupError:
        logger.warning('netbox-change-approval is installed but has no ChangeRequest model')
        return
    pre_save.connect(_remember_status, sender=model, dispatch_uid='netbox_calendar:change_request_status')
    post_save.connect(_approved, sender=model, dispatch_uid='netbox_calendar:change_request_approved')


#
# Scheduled change requests on the calendar
#

def change_request_entries(user, after, before):
    """Calendar entries for change requests scheduled in [after, before) that `user` may view."""
    if not has_change_approval():
        return []
    from .agenda import Entry

    try:
        queryset = _change_request_model().objects.filter(
            status=STATUS_PENDING, scheduled_for__gte=after - CHANGE_REQUEST_LENGTH, scheduled_for__lt=before,
        )
        if hasattr(queryset, 'restrict'):
            queryset = queryset.restrict(user, 'view')
        found = []
        for change_request in queryset[:500]:
            start = change_request.scheduled_for
            found.append(Entry(
                title=str(change_request),
                start=start,
                end=start + CHANGE_REQUEST_LENGTH,
                url=change_request.get_absolute_url(),
                color='9e9e9e',
                kind='change_request',
                icon='check-decagram-outline',
                type_name='Change request',
            ))
        return found
    except Exception:
        # A newer or older change-approval without these fields: the calendar still loads, and the log says why.
        logger.exception('Could not read scheduled change requests from netbox-change-approval')
        return []
