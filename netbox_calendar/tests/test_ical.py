from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings

from .. import ical
from ..models import Event, EventType, OccurrenceChange

ZONE = ZoneInfo('Europe/Helsinki')


def unfold(text):
    return text.replace('\r\n ', '')


@override_settings(TIME_ZONE='Europe/Helsinki')
class ICalTests(TestCase):
    def setUp(self):
        start = datetime(2026, 4, 7, 9, 0, tzinfo=ZONE)
        self.event = Event.objects.create(
            title='Core switch upgrade, stage 1', event_type=EventType.objects.get(slug='upgrade'),
            start=start, end=start + timedelta(hours=2), rrule='FREQ=WEEKLY;COUNT=4',
            description='Line one\nLine two',
        )
        OccurrenceChange.objects.create(event=self.event, original_start=start + timedelta(days=7), reason='Holiday')

    def test_escape_and_fold(self):
        self.assertEqual(ical.escape('a,b;c\\d\ne'), 'a\\,b\\;c\\\\d\\ne')
        self.assertEqual(ical.param('Doe, Jane'), '"Doe, Jane"')
        line = 'DESCRIPTION:' + 'ä' * 100
        folded = ical.fold(line)
        self.assertTrue(all(len(part.encode()) <= 75 for part in folded.split('\r\n')))
        self.assertEqual(unfold(folded), line)

    def test_repeating_event(self):
        text = ical.calendar([self.event])
        self.assertTrue(text.endswith('END:VCALENDAR\r\n'))
        lines = unfold(text).split('\r\n')
        for expected in (
            'NAME:NetBox', 'X-WR-CALNAME:NetBox',
            'BEGIN:VTIMEZONE', 'TZID:Europe/Helsinki', 'DTSTART:19700329T030000',
            'DTSTART;TZID=Europe/Helsinki:20260407T090000',
            'DTEND;TZID=Europe/Helsinki:20260407T110000',
            'RRULE:FREQ=WEEKLY;COUNT=4',
            'EXDATE;TZID=Europe/Helsinki:20260414T090000',
            'SUMMARY:Core switch upgrade\\, stage 1',
            'STATUS:TENTATIVE',
        ):
            self.assertIn(expected, lines)
        self.assertTrue(any(line.startswith('DESCRIPTION:Line one\\nLine two') for line in lines))

    def test_moved_occurrence_has_its_own_vevent(self):
        original = self.event.start + timedelta(days=14)
        OccurrenceChange.objects.create(
            event=self.event, original_start=original, start=original + timedelta(days=1),
            end=original + timedelta(days=1, hours=3), reason='Spare parts late',
        )
        lines = unfold(ical.calendar([self.event])).split('\r\n')
        self.assertEqual(lines.count('BEGIN:VEVENT'), 2)
        # Only the skipped one is excluded; the moved one is replaced.
        self.assertEqual([line for line in lines if line.startswith('EXDATE')],
                         ['EXDATE;TZID=Europe/Helsinki:20260414T090000'])
        override = lines[lines.index('RECURRENCE-ID;TZID=Europe/Helsinki:20260421T090000'):]
        self.assertIn('DTSTART;TZID=Europe/Helsinki:20260422T090000', override)
        self.assertIn('DTEND;TZID=Europe/Helsinki:20260422T120000', override)
        self.assertTrue(any('Reason: Spare parts late' in line for line in override))
        uids = {line for line in lines if line.startswith('UID:')}
        self.assertEqual(len(uids), 1)

    def test_all_day_event_is_dates(self):
        self.event.all_day = True
        self.event.rrule = ''
        self.event.full_clean()
        lines = unfold(ical.calendar([self.event])).split('\r\n')
        self.assertIn('DTSTART;VALUE=DATE:20260407', lines)
        self.assertIn('DTEND;VALUE=DATE:20260408', lines)

    @override_settings(TIME_ZONE='UTC')
    def test_utc_without_timezone_block(self):
        text = ical.calendar([self.event])
        self.assertNotIn('VTIMEZONE', text)
        self.assertIn('DTSTART:20260407T060000Z', text)

    def test_invite(self):
        text = ical.calendar(
            [self.event], method='REQUEST', organizer=('Owner', 'owner@example.com'),
            attendees=[('Doe, Jane', 'jane@example.com')],
        )
        lines = unfold(text).split('\r\n')
        self.assertIn('METHOD:REQUEST', lines)
        self.assertIn('ORGANIZER;CN=Owner:mailto:owner@example.com', lines)
        self.assertIn('ATTENDEE;CN="Doe, Jane";ROLE=REQ-PARTICIPANT;RSVP=FALSE:mailto:jane@example.com', lines)
