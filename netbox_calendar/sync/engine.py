"""
Which events go to whose calendar, and sending only what changed.

For each connection: the events its user may view (only those they own or
are on, unless they chose all), not cancelled, and not over for more than
PAST. Each one's fingerprint is compared with the one stored when it was
last pushed (SyncedEvent), so an unchanged event costs nothing. Events
that dropped out (deleted, cancelled, permission gone) are removed.

A sign-in that stopped working marks the connection "Sign-in expired" and
tells the user once, by bell notification. Other errors are kept on the
connection (shown to the user and on the admin page) and retried on the
next sync.
"""
import logging
import time
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone

from core.models import ObjectType
from extras.models import Notification

from .. import recurrence
from ..choices import EventStatusChoices
from ..event_types import SYNC_FAILED
from ..models import CalendarConnection, Event, SyncedEvent, involving
from . import get_provider
from .base import AuthExpired, SyncError

logger = logging.getLogger('netbox_calendar.sync')

#: Events that ended longer ago than this are left out (and removed).
PAST = timedelta(days=30)

#: One sync per connection at a time; a second one waits this long, then gives up.
LOCK_SECONDS = 600
LOCK_WAIT = 60


def events_for(connection, now=None):
    """The events that belong in the connection's calendar."""
    user = connection.user
    since = (now or timezone.now()) - PAST
    queryset = Event.objects.restrict(user, 'view').exclude(status=EventStatusChoices.CANCELLED)
    if connection.scope == CalendarConnection.SCOPE_MINE:
        queryset = queryset.filter(involving(user))
    queryset = queryset.filter(Q(end__gte=since) | ~Q(rrule='')).distinct().select_related(
        'event_type', 'event_owner',
    ).prefetch_related('occurrence_changes')
    found = []
    for event in queryset:
        if event.rrule:
            last = recurrence.latest_end(event)
            if last is not None and last < since:
                continue
        found.append(event)
    return found


class _Lock:
    """A lock in NetBox's cache (Redis), so two workers don't push one connection at once."""

    def __init__(self, connection):
        self.key = f'netbox_calendar_sync_{connection.pk}'
        self.held = False

    def __enter__(self):
        deadline = time.monotonic() + LOCK_WAIT
        while not cache.add(self.key, 1, LOCK_SECONDS):
            if time.monotonic() > deadline:
                return False
            time.sleep(1)
        self.held = True
        return True

    def __exit__(self, *exc):
        if self.held:
            cache.delete(self.key)


def sync_connection(connection, event_ids=None):
    """
    Push the connection's events, or only `event_ids` when given (a missing
    one is removed). Returns (pushed, removed), or None when skipped.
    """
    provider = get_provider(connection.provider)
    if provider is None or not provider.is_ready() or not connection.is_active:
        return None
    with _Lock(connection) as locked:
        if not locked:
            logger.warning('Sync of connection #%s skipped: another one is running', connection.pk)
            return None
        return _sync(provider, connection, event_ids)


def _sync(provider, connection, event_ids):
    errors, pushed, removed = [], 0, 0
    try:
        if provider.prepare(connection):
            # A new calendar: whatever was pushed before is gone with the old one.
            connection.synced.all().delete()
        wanted = {event.pk: event for event in events_for(connection)}
        rows = connection.synced.all()
        if event_ids is not None:
            rows = rows.filter(event_id__in=event_ids)
            wanted = {pk: event for pk, event in wanted.items() if pk in event_ids}
        rows = {row.event_id: row for row in rows}

        for pk, event in wanted.items():
            row = rows.get(pk) or SyncedEvent(connection=connection, event_id=pk)
            fingerprint = provider.fingerprint(event)
            if row.pk and row.fingerprint == fingerprint:
                continue
            try:
                provider.push(connection, event, row)
            except AuthExpired:
                raise
            except SyncError as error:
                errors.append(f'{event}: {error}')
                continue
            row.fingerprint = fingerprint
            row.save()
            pushed += 1

        for pk, row in rows.items():
            if pk in wanted:
                continue
            try:
                provider.remove(connection, row)
            except AuthExpired:
                raise
            except SyncError as error:
                errors.append(f'event #{pk}: {error}')
                continue
            row.delete()
            removed += 1
    except AuthExpired as error:
        expire(connection, str(error))
        return pushed, removed
    except SyncError as error:
        errors.append(str(error))
    except Exception as error:  # A bug or an unexpected answer: keep it visible, don't kill the worker.
        logger.exception('Sync of connection #%s failed', connection.pk)
        errors.append(f'{type(error).__name__}: {error}')

    connection.last_error = '\n'.join(errors)[:2000]
    if event_ids is None or not errors:
        # A partial sync (one event) doesn't count as a full one.
        connection.status = CalendarConnection.STATUS_ERROR if errors else CalendarConnection.STATUS_ACTIVE
    if event_ids is None:
        connection.last_sync = timezone.now()
    connection.save(update_fields=('status', 'last_error', 'last_sync'))
    return pushed, removed


def expire(connection, reason):
    """Mark the sign-in as expired and tell the user once (bell)."""
    first_time = connection.status != CalendarConnection.STATUS_EXPIRED
    connection.status = CalendarConnection.STATUS_EXPIRED
    connection.last_error = reason[:2000]
    connection.save(update_fields=('status', 'last_error'))
    if first_time:
        object_type = ObjectType.objects.get_for_model(connection)
        Notification.objects.filter(object_type=object_type, object_id=connection.pk).delete()
        Notification.objects.create(
            user=connection.user, object_type=object_type, object_id=connection.pk, event_type=SYNC_FAILED,
        )


def active_connections():
    return CalendarConnection.objects.filter(
        status__in=(CalendarConnection.STATUS_ACTIVE, CalendarConnection.STATUS_ERROR),
    ).select_related('user')


def sync_events(event_ids):
    """Background job after a save: push these events to every connection."""
    for connection in active_connections():
        sync_connection(connection, set(event_ids))


def sync_one(connection_pk):
    """Background job: a full sync of one connection (after connecting, or Sync now)."""
    connection = CalendarConnection.objects.filter(pk=connection_pk).select_related('user').first()
    if connection is not None:
        return sync_connection(connection)


def sync_all():
    """The hourly job: a full sync of every connection. Returns how many were synced."""
    count = 0
    for connection in active_connections():
        if sync_connection(connection) is not None:
            count += 1
    return count


def disconnect(connection, remove_calendar=True):
    """Delete the connection, and NetBox's calendar in the outside account when asked."""
    provider = get_provider(connection.provider)
    if provider is not None and remove_calendar and connection.status != CalendarConnection.STATUS_EXPIRED:
        try:
            provider.disconnect(connection, remove_calendar=True)
        except SyncError as error:
            logger.warning('Removing the calendar of connection #%s failed: %s', connection.pk, error)
    Notification.objects.filter(
        object_type=ObjectType.objects.get_for_model(connection), object_id=connection.pk,
    ).delete()
    connection.delete()


#
# Queueing background jobs
#

def enqueue(function, *args):
    """Run `function` (a name in this module) in NetBox's background worker."""
    import django_rq

    django_rq.get_queue('default').enqueue(f'{__name__}.{function}', *args, job_timeout=1800)
