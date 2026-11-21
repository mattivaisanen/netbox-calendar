"""
The background jobs. The first: reminders, start and end notices, and the automatic
status of single events. Runs every five minutes in NetBox's background
worker (the one that sends webhooks and runs scripts).

Each notice is sent once (SentNotice). One the job missed, because the
worker was stopped say, is still sent up to reminder_grace_minutes late,
never later, so a restarted worker doesn't flood anyone with old news.

The second, hourly: a full push to every connected outside calendar
(sync/engine.py), which catches what the push after each save missed and
moves the window of rules sent as single events.
"""
import logging
import uuid
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.translation import gettext as _

from core.choices import ObjectChangeActionChoices
from core.events import OBJECT_UPDATED
from extras.events import enqueue_event, flush_events
from netbox.jobs import JobRunner, system_job
from utilities.request import NetBoxFakeRequest

from . import integrations, notifications
from .choices import EventStatusChoices, NoticeChoices
from .models import Event, EventType, SentNotice
from .plugin_settings import get_setting, on

logger = logging.getLogger('netbox_calendar.jobs')

# Sent notices are forgotten after this long.
KEEP_SENT = timedelta(days=35)


def _grace():
    try:
        return timedelta(minutes=int(get_setting('reminder_grace_minutes', 60)))
    except (TypeError, ValueError):
        return timedelta(minutes=60)


#: The name the change log shows for status changes this job makes.
SYSTEM_NAME = 'netbox-calendar'


def update_statuses(now):
    """
    Single events move to in progress at their start and done at their end.
    Each event is saved on its own, so the change shows in its change log,
    Event Rules and webhooks see it, and connected calendars get it.
    """
    if not on('auto_status'):
        return 0
    single = Event.objects.filter(rrule='').exclude(pk__in=integrations.all_awaiting_approval())
    started = single.filter(
        status__in=(EventStatusChoices.PLANNED, EventStatusChoices.CONFIRMED), start__lte=now, end__gt=now,
    )
    ended = single.filter(status__in=EventStatusChoices.ACTIVE, end__lte=now)
    changes = [(event, EventStatusChoices.IN_PROGRESS) for event in started]
    changes += [(event, EventStatusChoices.DONE) for event in ended]
    if not changes:
        return 0

    request = NetBoxFakeRequest({
        'id': uuid.uuid4(), 'user': None, 'path': '', 'path_info': '', 'method': '', 'GET': {}, 'META': {},
    })
    queue = {}
    count = 0
    for event, status in changes:
        try:
            with transaction.atomic():
                _set_status(event, status, request, queue)
            count += 1
        except Exception:
            logger.exception('Automatic status of event #%s failed', event.pk)
    try:
        flush_events(list(queue.values()))
    except Exception:
        logger.exception('Event rules for automatic status changes failed')
    return count


def _set_status(event, status, request, queue):
    """Save `status` on `event` with a change log entry, and queue its "updated" event for Event Rules."""
    event.snapshot()
    event.status = status
    event._changelog_message = _('Status set automatically')
    event.save()
    # Without a signed-in user NetBox writes no change log itself: write it here, under SYSTEM_NAME.
    change = event.to_objectchange(ObjectChangeActionChoices.ACTION_UPDATE)
    change.user_name = SYSTEM_NAME
    change.request_id = request.id
    change.save()
    enqueue_event(queue, event, request, OBJECT_UPDATED)


def _longest_reminder():
    """The longest reminder of any event type or event, in minutes."""
    longest = 0
    for values in EventType.objects.values_list('reminders', flat=True):
        longest = max([longest, *(values or ())])
    for values in Event.objects.exclude(reminders=None).values_list('reminders', flat=True):
        longest = max([longest, *(values or ())])
    return longest


def _send_once(occurrence, kind, offset=0):
    """Send a notice unless it was sent before. Returns True when sent now."""
    try:
        with transaction.atomic():
            SentNotice.objects.create(
                event=occurrence.event, occurrence=occurrence.start, kind=kind, offset=offset,
            )
    except IntegrityError:
        return False
    notifications.notify(occurrence.event, kind, occurrence.start, occurrence.end, offset=offset or None)
    return True


def due_notices(occurrence, now, grace):
    """[(kind, offset)] due for `occurrence` right now."""
    due = []
    for offset in occurrence.event.effective_reminders():
        moment = occurrence.start - timedelta(minutes=offset)
        if moment <= now < moment + grace and now < occurrence.start:
            due.append((NoticeChoices.REMINDER, offset))
    if occurrence.start <= now < occurrence.start + grace and now < occurrence.end:
        due.append((NoticeChoices.STARTED, 0))
    if occurrence.end <= now < occurrence.end + grace:
        due.append((NoticeChoices.ENDED, 0))
    return due


def send_notices(now=None):
    """Send every reminder, start and end notice that is due. Returns how many were sent."""
    now = now or timezone.now()
    grace = _grace()
    update_statuses(now)
    ahead = timedelta(minutes=_longest_reminder())
    # Events waiting for approval in netbox-change-approval send nothing yet.
    events = Event.objects.exclude(status=EventStatusChoices.CANCELLED).exclude(
        pk__in=integrations.all_awaiting_approval(),
    ).filter(
        Q(rrule__gt='') | Q(start__lte=now + ahead, end__gte=now - grace),
    ).select_related('event_type', 'event_owner').prefetch_related('occurrence_changes')
    sent = 0
    for event in events:
        for occurrence in event.occurrences(now - grace, now + ahead + timedelta(minutes=1)):
            for kind, offset in due_notices(occurrence, now, grace):
                try:
                    sent += _send_once(occurrence, kind, offset)
                except Exception:
                    logger.exception('Notice %s about event #%s failed', kind, event.pk)
    SentNotice.objects.filter(sent__lt=now - KEEP_SENT).delete()
    return sent


@system_job(interval=5)
class CalendarNoticesJob(JobRunner):
    class Meta:
        name = 'Calendar reminders and notices'

    def run(self, *args, **kwargs):
        count = send_notices()
        self.logger.info(f'Sent {count} calendar notice(s).')


@system_job(interval=60)
class CalendarSyncJob(JobRunner):
    class Meta:
        name = 'Calendar sync'

    def run(self, *args, **kwargs):
        from .sync import any_enabled, engine

        if not any_enabled():
            return
        count = engine.sync_all()
        self.logger.info(f'Synced {count} calendar connection(s).')
