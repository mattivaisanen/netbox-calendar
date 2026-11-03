"""
iCal (RFC 5545) output: the subscription feed, one event's .ics download,
and the invite attached to direct e-mail. Written by hand, no extra
package: only the parts calendars need.

Times are written in NetBox's TIME_ZONE with a VTIMEZONE block, so a
repeating event at 09:00 stays at 09:00 in Outlook across daylight saving
changes. All-day events are dates.
"""
from calendar import monthrange
from datetime import datetime, timedelta, timezone as dt_timezone
from functools import lru_cache
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from .plugin_settings import get_setting

PRODID = '-//netbox-calendar//EN'

STATUS = {
    'planned': 'TENTATIVE',
    'confirmed': 'CONFIRMED',
    'in_progress': 'CONFIRMED',
    'done': 'CONFIRMED',
    'cancelled': 'CANCELLED',
}


def escape(text):
    """Text as an iCal value: backslash, semicolon, comma and line breaks escaped."""
    return (
        str(text or '').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,')
        .replace('\r\n', '\n').replace('\n', '\\n')
    )


def param(text):
    """Text as a parameter value (such as CN=): quoted when it holds , ; or :."""
    text = str(text or '').replace('"', "'").replace('\r', ' ').replace('\n', ' ')
    return f'"{text}"' if any(char in text for char in ',;:') else text


def fold(line):
    """Split a line into 75-octet pieces, as iCal requires."""
    data = line.encode('utf-8')
    if len(data) <= 75:
        return line
    pieces, current = [], b''
    for char in line:
        encoded = char.encode('utf-8')
        if len(current) + len(encoded) > (75 if not pieces else 74):
            pieces.append(current.decode('utf-8'))
            current = b''
        current += encoded
    pieces.append(current.decode('utf-8'))
    return '\r\n '.join(pieces)


def _zone_name():
    return settings.TIME_ZONE or 'UTC'


def _utc(value):
    return value.astimezone(dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def _local_text(value):
    return timezone.localtime(value).strftime('%Y%m%dT%H%M%S')


def _is_utc():
    return _zone_name().upper() in ('UTC', 'ETC/UTC', 'GMT')


def _time_line(name, value):
    """DTSTART/DTEND/EXDATE line of a timed value, local with TZID unless NetBox runs in UTC."""
    if _is_utc():
        return f'{name}:{_utc(value)}'
    return f'{name};TZID={_zone_name()}:{_local_text(value)}'


def _offset_text(delta):
    minutes = int(delta.total_seconds() // 60)
    sign = '+' if minutes >= 0 else '-'
    minutes = abs(minutes)
    return f'{sign}{minutes // 60:02d}{minutes % 60:02d}'


def _first_match_1970(month, nth, weekday, at):
    """
    The day in 1970 that a yearly rule such as "last Sunday of March" falls
    on, at time `at`. Rules start in 1970 so they cover events of any year:
    Outlook drops a feed whose time zone starts after its events.
    """
    length = monthrange(1970, month)[1]
    days = [day for day in range(1, length + 1) if datetime(1970, month, day).weekday() == weekday]
    day = days[-1] if nth == -1 else days[nth - 1]
    return datetime(1970, month, day, at.hour, at.minute)


@lru_cache(maxsize=8)
def vtimezone(zone_name, year):
    """
    VTIMEZONE lines for `zone_name`, from its daylight saving changes in
    `year` (found by stepping through the year hour by hour). Each change
    becomes a yearly rule such as "last Sunday of March", starting in 1970.
    """
    zone = ZoneInfo(zone_name)
    lines = ['BEGIN:VTIMEZONE', f'TZID:{zone_name}']
    moment = datetime(year, 1, 1, tzinfo=dt_timezone.utc)
    previous = moment.astimezone(zone).utcoffset()
    changes = []
    for _hour in range(366 * 24):
        moment += timedelta(hours=1)
        offset = moment.astimezone(zone).utcoffset()
        if offset != previous:
            changes.append((moment, previous, offset))
            previous = offset
    if not changes:
        offset = _offset_text(previous)
        lines += [
            'BEGIN:STANDARD', 'DTSTART:19700101T000000', f'TZOFFSETFROM:{offset}', f'TZOFFSETTO:{offset}',
            'END:STANDARD',
        ]
    for moment, before, after in changes:
        # The wall-clock time the change happens at, in the old offset.
        local = (moment + before).replace(tzinfo=None)
        nth = (local.day - 1) // 7 + 1
        last = (local + timedelta(days=7)).month != local.month
        day = ('MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU')[local.weekday()]
        kind = 'DAYLIGHT' if after > before else 'STANDARD'
        first = _first_match_1970(local.month, -1 if last else nth, local.weekday(), local)
        lines += [
            f'BEGIN:{kind}',
            f'DTSTART:{first.strftime("%Y%m%dT%H%M%S")}',
            f'RRULE:FREQ=YEARLY;BYMONTH={local.month};BYDAY={-1 if last else nth}{day}',
            f'TZOFFSETFROM:{_offset_text(before)}',
            f'TZOFFSETTO:{_offset_text(after)}',
            f'END:{kind}',
        ]
    lines.append('END:VTIMEZONE')
    return tuple(lines)


def _uid(event):
    host = urlparse(_base_url()).hostname or 'netbox'
    return f'netbox-calendar-{event.pk}@{host}'


def _base_url():
    return (get_setting('netbox_url') or '').rstrip('/')


def absolute_url(path):
    """`path` with the NetBox address in front, when the netbox_url setting gives one."""
    base = _base_url()
    return f'{base}{path}' if base else path


def _sequence(event):
    """Grows with every save, so calendars take an updated invite as newer."""
    if not event.created or not event.last_updated:
        return 0
    return max(int((event.last_updated - event.created).total_seconds()), 0) % 2147483647


def event_lines(event, objects_text='', attendees=(), organizer=None):
    """The VEVENT lines of one event (a whole series for a repeating one)."""
    lines = [
        'BEGIN:VEVENT',
        f'UID:{_uid(event)}',
        f'DTSTAMP:{_utc(timezone.now())}',
        f'SEQUENCE:{_sequence(event)}',
    ]
    if event.all_day:
        lines.append(f'DTSTART;VALUE=DATE:{timezone.localtime(event.start).strftime("%Y%m%d")}')
        lines.append(f'DTEND;VALUE=DATE:{timezone.localtime(event.end).strftime("%Y%m%d")}')
    else:
        lines.append(_time_line('DTSTART', event.start))
        lines.append(_time_line('DTEND', event.end))
    if event.rrule:
        lines.append(f'RRULE:{event.rrule}')
        for skipped in event.skipped or ():
            if event.all_day:
                lines.append(f'EXDATE;VALUE=DATE:{timezone.localtime(skipped).strftime("%Y%m%d")}')
            else:
                lines.append(_time_line('EXDATE', skipped))
    link = absolute_url(event.get_absolute_url())
    description = '\n\n'.join(part for part in (
        event.description,
        f'Concerns: {objects_text}' if objects_text else '',
        event.comments,
        link,
    ) if part)
    lines += [
        f'SUMMARY:{escape(event.title)}',
        f'DESCRIPTION:{escape(description)}',
        f'CATEGORIES:{escape(event.event_type.name)}',
        f'STATUS:{STATUS.get(event.status, "CONFIRMED")}',
        # Outage and degraded time shows as busy.
        f'TRANSP:{"TRANSPARENT" if event.impact == "none" else "OPAQUE"}',
    ]
    if objects_text:
        lines.append(f'LOCATION:{escape(objects_text)}')
    if link.startswith('http'):
        lines.append(f'URL:{link}')
    if organizer:
        lines.append(f'ORGANIZER;CN={param(organizer[0])}:mailto:{organizer[1]}')
    for name, email in attendees:
        lines.append(f'ATTENDEE;CN={param(name)};ROLE=REQ-PARTICIPANT;RSVP=FALSE:mailto:{email}')
    lines.append('END:VEVENT')
    return lines


def calendar(events, name='NetBox', method='PUBLISH', describe=None, attendees=(), organizer=None, refresh=None):
    """
    A whole .ics file. `describe(event)` gives the "Concerns:" text of an
    event; `attendees` and `organizer` are (name, e-mail) for invites;
    `refresh` is an iCal duration such as PT1H, for subscription feeds.
    """
    lines = [
        'BEGIN:VCALENDAR',
        'VERSION:2.0',
        f'PRODID:{PRODID}',
        'CALSCALE:GREGORIAN',
        f'METHOD:{method}',
        # Calendar name: NAME is the standard (RFC 7986), X-WR-CALNAME is what Outlook reads.
        f'NAME:{escape(name)}',
        f'X-WR-CALNAME:{escape(name)}',
    ]
    if refresh:
        # How often subscribers should fetch the feed again.
        lines += [f'REFRESH-INTERVAL;VALUE=DURATION:{refresh}', f'X-PUBLISHED-TTL:{refresh}']
    if not _is_utc():
        lines.append(f'X-WR-TIMEZONE:{_zone_name()}')
        lines += vtimezone(_zone_name(), timezone.localtime().year)
    for event in events:
        objects_text = describe(event) if describe else ''
        lines += event_lines(event, objects_text, attendees, organizer)
    lines.append('END:VCALENDAR')
    return '\r\n'.join(fold(line) for line in lines) + '\r\n'
