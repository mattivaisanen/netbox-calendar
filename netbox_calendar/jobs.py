"""
The background job: reminders, start and end notices, and the automatic
status of single events. Runs every five minutes in NetBox's background
worker (the one that sends webhooks and runs scripts).

Each notice is sent once (SentNotice). One the job missed, because the
worker was stopped say, is still sent up to reminder_grace_minutes late,
never later, so a restarted worker doesn't flood anyone with old news.
"""
import logging
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone

from . import notifications
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


def update_statuses(now):
    """Single events move to in progress at their start and done at their end."""
    if not on('auto_status'):
        return 0
    single = Event.objects.filter(rrule='')
    started = single.filter(
        status__in=(EventStatusChoices.PLANNED, EventStatusChoices.CONFIRMED), start__lte=now, end__gt=now,
    ).update(status=EventStatusChoices.IN_PROGRESS)
    ended = single.filter(status__in=EventStatusChoices.ACTIVE, end__lte=now).update(status=EventStatusChoices.DONE)
    return started + ended


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
    events = Event.objects.exclude(status=EventStatusChoices.CANCELLED).filter(
        Q(rrule__gt='') | Q(start__lte=now + ahead, end__gte=now - grace),
    ).select_related('event_type', 'event_owner')
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


try:
    from netbox.jobs import JobRunner, system_job
except ImportError:  # pragma: no cover
    JobRunner = system_job = None

if system_job is not None:
    @system_job(interval=5)
    class CalendarNoticesJob(JobRunner):
        class Meta:
            name = 'Calendar reminders and notices'

        def run(self, *args, **kwargs):
            count = send_notices()
            self.logger.info(f'Sent {count} calendar notice(s).')
