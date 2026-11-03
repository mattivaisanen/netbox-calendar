"""
Repeating events, stored as an iCal RRULE (RFC 5545) and expanded with
python-dateutil when shown. The rule runs in NetBox's local time zone, so
an event at 09:00 stays at 09:00 across daylight saving changes.

Single occurrences can be skipped or moved (models.OccurrenceChange);
occurrences() applies those changes.

The event form builds simple rules from a few fields (build() and
parse_simple()); anything else can be typed as a rule. describe() puts a
rule in words.
"""
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta, timezone as dt_timezone

from dateutil import rrule as du
from django.utils import formats, timezone
from django.utils.translation import gettext as _, ngettext

# Rules repeating more often than hourly would flood the calendar.
ALLOWED_FREQS = ('HOURLY', 'DAILY', 'WEEKLY', 'MONTHLY', 'YEARLY')
# The most occurrences ever expanded in one go, as a safety net.
MAX_OCCURRENCES = 5000

WEEKDAYS = ('MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU')

# The simple repeat choices of the event form.
NONE, DAILY, WEEKLY, MONTHLY_DATE, MONTHLY_WEEKDAY, YEARLY, CUSTOM = (
    '', 'daily', 'weekly', 'monthly_date', 'monthly_weekday', 'yearly', 'custom',
)


@dataclass(frozen=True)
class Occurrence:
    """
    One time an event happens. A moved occurrence keeps the time the rule
    gave it in `original_start`, and its OccurrenceChange in `change`.
    """
    event: object
    start: datetime
    end: datetime
    original_start: datetime = None
    change: object = field(default=None, compare=False)

    def __post_init__(self):
        if self.original_start is None:
            object.__setattr__(self, 'original_start', self.start)

    @property
    def is_first(self):
        return self.original_start == self.event.start

    @property
    def is_moved(self):
        return self.change is not None

    def is_running(self, now=None):
        now = now or timezone.now()
        return self.start <= now < self.end


def _local(value):
    return timezone.localtime(value)


def _rule(text, start):
    return du.rrulestr(f'RRULE:{text}', dtstart=_local(start))


def _parts(text):
    """{'FREQ': 'WEEKLY', ...} of a rule."""
    parts = {}
    for item in text.split(';'):
        if '=' in item:
            key, value = item.split('=', 1)
            parts[key.strip().upper()] = value.strip().upper()
    return parts


def _join(parts):
    order = ('FREQ', 'INTERVAL', 'BYDAY', 'BYMONTHDAY', 'BYMONTH', 'COUNT', 'UNTIL')
    keys = [key for key in order if key in parts] + sorted(key for key in parts if key not in order)
    return ';'.join(f'{key}={parts[key]}' for key in keys)


def normalize(text, start):
    """
    The rule as stored: no "RRULE:" prefix, upper case, a local UNTIL turned
    into UTC (as RFC 5545 requires with a time zone). Raises ValueError
    with a readable reason when the rule is wrong or never happens.
    """
    text = text.strip()
    if text.upper().startswith('RRULE:'):
        text = text[6:]
    parts = _parts(text)
    if not parts:
        raise ValueError(_('Not a recurrence rule.'))
    if parts.get('FREQ') not in ALLOWED_FREQS:
        raise ValueError(_('FREQ must be one of {choices}.').format(choices=', '.join(ALLOWED_FREQS)))
    if 'UNTIL' in parts and 'COUNT' in parts:
        raise ValueError(_('Give UNTIL or COUNT, not both.'))
    if 'UNTIL' in parts and not parts['UNTIL'].endswith('Z'):
        parts['UNTIL'] = _until_utc(parts['UNTIL'])
    text = _join(parts)
    if start is None:
        return text
    try:
        rule = _rule(text, start)
    except (ValueError, TypeError) as error:
        raise ValueError(_('Not a valid recurrence rule: {error}').format(error=error))
    if rule.after(_local(start), inc=True) is None:
        raise ValueError(_('This rule never happens after the start.'))
    return text


def _until_utc(value):
    """A local UNTIL (YYYYMMDD or YYYYMMDDTHHMMSS) as UTC, end of day for a date."""
    try:
        if 'T' in value:
            local = datetime.strptime(value, '%Y%m%dT%H%M%S')
        else:
            local = datetime.combine(datetime.strptime(value, '%Y%m%d').date(), time(23, 59, 59))
    except ValueError:
        raise ValueError(_('UNTIL must be a date (YYYYMMDD) or date and time (YYYYMMDDTHHMMSS).'))
    aware = timezone.make_aware(local, timezone.get_current_timezone())
    return aware.astimezone(dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def changes(event):
    """{original start: OccurrenceChange} of `event` (uses a prefetch when there is one)."""
    if event.pk is None:
        return {}
    return {change.original_start: change for change in event.occurrence_changes.all()}


def is_occurrence(event, moment):
    """Whether the event's rule puts an occurrence at `moment`."""
    if not event.rrule or moment is None:
        return moment == event.start
    try:
        rule = _rule(event.rrule, event.start)
    except (ValueError, TypeError):
        return False
    return rule.after(_local(moment), inc=True) == moment


def occurrences(event, after, before, limit=None):
    """
    The occurrences of `event` that overlap [after, before), oldest first.
    Skipped ones are left out and moved ones are at their new time. At most
    `limit` (and never more than MAX_OCCURRENCES).
    """
    duration = event.end - event.start
    if not event.rrule:
        if event.start < before and event.end > after:
            return [Occurrence(event, event.start, event.end)]
        return []
    try:
        rule = _rule(event.rrule, event.start)
    except (ValueError, TypeError):
        return []
    changed = changes(event)
    found = []
    limit = min(limit or MAX_OCCURRENCES, MAX_OCCURRENCES)
    # An occurrence overlaps when it starts before `before` and ends after `after`.
    for count, start in enumerate(rule.xafter(_local(after - duration), inc=False)):
        if start >= before or len(found) >= limit or count >= MAX_OCCURRENCES:
            break
        if start in changed:
            # Skipped, or moved: a moved one is added below at its new time.
            continue
        found.append(Occurrence(event, start, start + duration))
    for change in changed.values():
        if change.is_moved and change.start < before and change.end > after \
                and is_occurrence(event, change.original_start):
            found.append(Occurrence(event, change.start, change.end, change.original_start, change))
    found.sort(key=lambda occurrence: occurrence.start)
    return found[:limit]


def build(kind, start, interval=1, weekdays=(), until=None, count=None):
    """
    A rule from the event form's simple fields. `kind` is one of the
    constants above; `until` a date (inclusive); `count` a number of times.
    """
    if not kind or kind == CUSTOM:
        return ''
    local = _local(start)
    parts = {}
    if kind == DAILY:
        parts['FREQ'] = 'DAILY'
    elif kind == WEEKLY:
        parts['FREQ'] = 'WEEKLY'
        days = [day for day in WEEKDAYS if day in (weekdays or ())]
        parts['BYDAY'] = ','.join(days or [WEEKDAYS[local.weekday()]])
    elif kind == MONTHLY_DATE:
        parts['FREQ'] = 'MONTHLY'
        parts['BYMONTHDAY'] = str(local.day)
    elif kind == MONTHLY_WEEKDAY:
        parts['FREQ'] = 'MONTHLY'
        nth = (local.day - 1) // 7 + 1
        # The 5th weekday of a month is its last one.
        parts['BYDAY'] = f'{-1 if nth == 5 else nth}{WEEKDAYS[local.weekday()]}'
    elif kind == YEARLY:
        parts['FREQ'] = 'YEARLY'
    if interval and int(interval) > 1:
        parts['INTERVAL'] = str(int(interval))
    if count:
        parts['COUNT'] = str(int(count))
    elif until:
        parts['UNTIL'] = _until_utc(until.strftime('%Y%m%d'))
    return _join(parts)


def parse_simple(text, start):
    """
    The event form's simple fields for a rule, as a dict (kind, interval,
    weekdays, until, count), or {'kind': CUSTOM} when the rule is not one
    build() makes.
    """
    if not text:
        return {'kind': NONE}
    parts = _parts(text)
    local = _local(start)
    fields = {'interval': int(parts.pop('INTERVAL', 1) or 1), 'weekdays': [], 'until': None, 'count': None}
    if 'COUNT' in parts:
        fields['count'] = int(parts.pop('COUNT'))
    if 'UNTIL' in parts:
        try:
            fields['until'] = timezone.localtime(
                datetime.strptime(parts.pop('UNTIL'), '%Y%m%dT%H%M%SZ').replace(tzinfo=dt_timezone.utc),
            ).date()
        except ValueError:
            return {'kind': CUSTOM}
    freq = parts.pop('FREQ', '')
    if freq == 'DAILY' and not parts:
        return {**fields, 'kind': DAILY}
    if freq == 'YEARLY' and not parts:
        return {**fields, 'kind': YEARLY}
    if freq == 'WEEKLY' and set(parts) <= {'BYDAY'}:
        days = parts.get('BYDAY', WEEKDAYS[local.weekday()]).split(',')
        if all(day in WEEKDAYS for day in days):
            return {**fields, 'kind': WEEKLY, 'weekdays': days}
    if freq == 'MONTHLY' and parts == {'BYMONTHDAY': str(local.day)}:
        return {**fields, 'kind': MONTHLY_DATE}
    if freq == 'MONTHLY' and parts == {'BYDAY': _parts(build(MONTHLY_WEEKDAY, start))['BYDAY']}:
        return {**fields, 'kind': MONTHLY_WEEKDAY}
    return {'kind': CUSTOM}


def _day_names():
    return {
        'MO': _('Monday'), 'TU': _('Tuesday'), 'WE': _('Wednesday'), 'TH': _('Thursday'),
        'FR': _('Friday'), 'SA': _('Saturday'), 'SU': _('Sunday'),
    }


def _ordinal(number):
    if number == -1:
        return _('last')
    return {1: _('1st'), 2: _('2nd'), 3: _('3rd'), 4: _('4th')}.get(number, f'{number}.')


def describe(text, start):
    """A rule in words: "Every 2 weeks on Monday, Thursday, until 31 Dec 2027"."""
    parts = _parts(text)
    simple = parse_simple(text, start)
    kind, interval = simple['kind'], simple.get('interval', 1)
    local = _local(start)
    names = _day_names()
    if kind == DAILY:
        words = ngettext('Every day', 'Every %(n)s days', interval) % {'n': interval}
    elif kind == WEEKLY:
        days = ', '.join(str(names[day]) for day in simple['weekdays'])
        words = ngettext('Every week on %(days)s', 'Every %(n)s weeks on %(days)s', interval) % {
            'n': interval, 'days': days,
        }
    elif kind == MONTHLY_DATE:
        words = ngettext('Every month on day %(day)s', 'Every %(n)s months on day %(day)s', interval) % {
            'n': interval, 'day': local.day,
        }
    elif kind == MONTHLY_WEEKDAY:
        byday = parts['BYDAY']
        number, day = int(byday[:-2]), byday[-2:]
        words = ngettext(
            'Every month on the %(nth)s %(day)s', 'Every %(n)s months on the %(nth)s %(day)s', interval,
        ) % {'n': interval, 'nth': _ordinal(number), 'day': names[day]}
    elif kind == YEARLY:
        words = ngettext('Every year on %(date)s', 'Every %(n)s years on %(date)s', interval) % {
            'n': interval, 'date': formats.date_format(local, 'j F'),
        }
    else:
        return text
    if simple.get('count'):
        words += ', ' + ngettext('once', '%(n)s times', simple['count']) % {'n': simple['count']}
    elif simple.get('until'):
        words += ', ' + _('until {date}').format(date=formats.date_format(simple['until']))
    return words


def latest_end(event):
    """When the last occurrence ends, or None for a series without an end."""
    if not event.rrule:
        return event.end
    parts = _parts(event.rrule)
    if 'COUNT' not in parts and 'UNTIL' not in parts:
        return None
    try:
        starts = list(_rule(event.rrule, event.start))[-1:]
    except (ValueError, TypeError):
        return event.end
    return (starts[0] + (event.end - event.start)) if starts else event.end


def window(days_before=0, days_after=0, now=None):
    """(after, before) around now, in whole local days."""
    now = timezone.localtime(now or timezone.now())
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight - timedelta(days=days_before), midnight + timedelta(days=days_after + 1)
