"""
What the month view, the timeline, the widget and the object card show:
occurrences of events in a time range as Entry rows, laid out as a month
grid (month_grid()) or grouped by day (by_day()).

Scheduled change requests of netbox-change-approval are shown too, when
that plugin is installed (integrations.py), as read-only entries.
"""
import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone
from django.utils.dates import WEEKDAYS_ABBR

from . import integrations
from .choices import EventStatusChoices
from .plugin_settings import get_setting


@dataclass
class Entry:
    """One thing on the calendar: an event occurrence or a scheduled change request."""
    title: str
    start: datetime
    end: datetime
    url: str
    color: str
    all_day: bool = False
    event: object = None
    occurrence: object = None
    kind: str = 'event'
    icon: str = ''
    status: str = ''
    status_label: str = ''
    impact: str = ''
    type_name: str = ''

    @property
    def is_running(self):
        return self.start <= timezone.now() < self.end

    @property
    def is_past(self):
        return self.end <= timezone.now()

    @property
    def is_cancelled(self):
        return self.status == EventStatusChoices.CANCELLED

    @property
    def last_day(self):
        return timezone.localtime(self.end - timedelta(seconds=1)).date()

    def days(self):
        """The local dates this entry covers."""
        first = timezone.localtime(self.start).date()
        last = self.last_day if self.end > self.start else first
        return [first + timedelta(days=n) for n in range((last - first).days + 1)]


@dataclass
class Day:
    date: date
    in_month: bool
    is_today: bool
    entries: list = field(default_factory=list)


def event_entries(queryset, after, before):
    """Entries for every occurrence of the events in `queryset` that overlaps [after, before)."""

    events = queryset.filter(Q(start__lt=before), Q(end__gt=after) | ~Q(rrule='')).select_related(
        'event_type',
    ).prefetch_related('occurrence_changes')
    found = []
    for event in events:
        event_type = event.event_type
        for occurrence in event.occurrences(after, before):
            found.append(Entry(
                title=event.title,
                start=occurrence.start,
                end=occurrence.end,
                url=event.get_absolute_url(),
                color=event_type.color,
                all_day=event.all_day,
                event=event,
                occurrence=occurrence,
                icon=event_type.icon,
                status=event.status,
                status_label=str(event.get_status_display()),
                impact=event.impact,
                type_name=event_type.name,
            ))
    return found


def entries(queryset, after, before, user=None):
    """Events and (when shown) scheduled change requests in [after, before), oldest first."""
    found = event_entries(queryset, after, before)
    if user is not None and get_setting('change_requests', True) is not False:
        found += integrations.change_request_entries(user, after, before)
    found.sort(key=lambda entry: (entry.start, not entry.all_day, entry.title))
    return found


def month_bounds(year, month, first_weekday=0):
    """(first, last) dates shown for a month: whole weeks around it."""
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    first -= timedelta(days=(first.weekday() - first_weekday) % 7)
    last += timedelta(days=(first_weekday - 1 - last.weekday()) % 7)
    return first, last


def local_midnight(day):
    return timezone.make_aware(datetime.combine(day, time(0)), timezone.get_current_timezone())


def month_grid(year, month, all_entries, first_weekday=0):
    """Weeks (lists of 7 Days) of the month, each Day with the entries on it."""
    first, last = month_bounds(year, month, first_weekday)
    today = timezone.localdate()
    days = {}
    day = first
    while day <= last:
        days[day] = Day(day, day.month == month, day == today)
        day += timedelta(days=1)
    for entry in all_entries:
        for covered in entry.days():
            if covered in days:
                days[covered].entries.append(entry)
    ordered = list(days.values())
    return [ordered[i:i + 7] for i in range(0, len(ordered), 7)]


def by_day(all_entries, first_day=None):
    """[(date, entries)] for the timeline: an entry lasting days is listed on its first shown day."""
    groups = {}
    for entry in all_entries:
        start = timezone.localtime(entry.start).date()
        if first_day is not None and start < first_day:
            start = first_day
        groups.setdefault(start, []).append(entry)
    return sorted(groups.items())


def weekday_names(first_weekday=0):
    """Short weekday names in column order."""

    return [WEEKDAYS_ABBR[(first_weekday + n) % 7] for n in range(7)]
