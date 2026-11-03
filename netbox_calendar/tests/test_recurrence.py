from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings

from .. import recurrence
from ..models import Event, EventType, all_day_bounds

ZONE = ZoneInfo('Europe/Helsinki')


def local(*args):
    return datetime(*args, tzinfo=ZONE)


@override_settings(TIME_ZONE='Europe/Helsinki')
class RuleTests(TestCase):
    def test_simple_rules_round_trip(self):
        start = local(2026, 3, 10, 10, 0)  # The 2nd Tuesday of March
        cases = [
            (recurrence.DAILY, {'interval': 2, 'until': date(2026, 12, 31)}),
            (recurrence.WEEKLY, {'weekdays': ['MO', 'WE']}),
            (recurrence.MONTHLY_DATE, {}),
            (recurrence.MONTHLY_WEEKDAY, {}),
            (recurrence.YEARLY, {'count': 5}),
        ]
        for kind, fields in cases:
            with self.subTest(kind=kind):
                text = recurrence.build(kind, start, **fields)
                parsed = recurrence.parse_simple(text, start)
                self.assertEqual(parsed['kind'], kind)
                for name, value in fields.items():
                    self.assertEqual(parsed[name], value)

    def test_monthly_weekday(self):
        self.assertEqual(
            recurrence.build(recurrence.MONTHLY_WEEKDAY, local(2026, 3, 10, 10, 0)), 'FREQ=MONTHLY;BYDAY=2TU',
        )
        # The 5th Tuesday of a month is its last one.
        last = local(2026, 3, 31, 10, 0)
        self.assertEqual(recurrence.build(recurrence.MONTHLY_WEEKDAY, last), 'FREQ=MONTHLY;BYDAY=-1TU')
        self.assertEqual(recurrence.describe('FREQ=MONTHLY;BYDAY=-1TU', last), 'Every month on the last Tuesday')

    def test_other_rules_are_custom(self):
        start = local(2026, 3, 10, 10, 0)
        self.assertEqual(recurrence.parse_simple('FREQ=MONTHLY;BYMONTHDAY=1,15', start), {'kind': recurrence.CUSTOM})
        self.assertEqual(recurrence.describe('FREQ=MONTHLY;BYMONTHDAY=1,15', start), 'FREQ=MONTHLY;BYMONTHDAY=1,15')

    def test_normalize(self):
        start = local(2026, 3, 10, 10, 0)
        self.assertEqual(recurrence.normalize('RRULE:freq=weekly', start), 'FREQ=WEEKLY')
        # A local end date becomes the end of that day in UTC.
        self.assertEqual(
            recurrence.normalize('FREQ=DAILY;UNTIL=20260320', start), 'FREQ=DAILY;UNTIL=20260320T215959Z',
        )
        for bad in ('FREQ=SECONDLY', 'FREQ=DAILY;COUNT=2;UNTIL=20260320', 'FREQ=DAILY;UNTIL=20260101', 'nonsense'):
            with self.subTest(rule=bad), self.assertRaises(ValueError):
                recurrence.normalize(bad, start)


@override_settings(TIME_ZONE='Europe/Helsinki')
class OccurrenceTests(TestCase):
    def _event(self, start, rrule, **fields):
        return Event(
            title='Patch', event_type=EventType.objects.get(slug='upgrade'),
            start=start, end=start + timedelta(hours=2), rrule=rrule, **fields,
        )

    def test_wall_clock_kept_over_daylight_saving(self):
        event = self._event(local(2026, 10, 20, 9, 0), 'FREQ=WEEKLY')
        found = event.occurrences(local(2026, 10, 1), local(2026, 11, 5))
        self.assertEqual([item.start.astimezone(ZONE).hour for item in found], [9, 9, 9])
        self.assertEqual([item.start.date() for item in found], [date(2026, 10, 20), date(2026, 10, 27),
                                                                 date(2026, 11, 3)])

    def test_running_occurrence_overlapping_the_window_is_included(self):
        event = self._event(local(2026, 1, 5, 9, 0), 'FREQ=DAILY;COUNT=3')
        found = event.occurrences(local(2026, 1, 6, 10, 0), local(2026, 1, 6, 12, 0))
        self.assertEqual([item.start for item in found], [local(2026, 1, 6, 9, 0)])

    def test_skipped_occurrences_are_left_out(self):
        event = self._event(local(2026, 1, 5, 9, 0), 'FREQ=DAILY;COUNT=3', skipped=[local(2026, 1, 6, 9, 0)])
        found = event.occurrences(local(2026, 1, 1), local(2026, 2, 1))
        self.assertEqual([item.start.day for item in found], [5, 7])

    def test_single_event(self):
        event = self._event(local(2026, 1, 5, 9, 0), '')
        self.assertEqual(len(event.occurrences(local(2026, 1, 5, 10, 0), local(2026, 1, 6))), 1)
        self.assertEqual(event.occurrences(local(2026, 1, 6), local(2026, 1, 7)), [])

    def test_all_day_bounds(self):
        start, end = all_day_bounds(local(2026, 3, 10, 14, 0), local(2026, 3, 11, 12, 0))
        self.assertEqual((start, end), (local(2026, 3, 10), local(2026, 3, 12)))
        # Bounds already on midnights stay as they are.
        self.assertEqual(all_day_bounds(start, end), (start, end))

    def test_clean_checks_rule_and_times(self):
        event = self._event(local(2026, 1, 5, 9, 0), 'FREQ=YEARLY;COUNT=2;UNTIL=20270101')
        with self.assertRaises(ValidationError) as caught:
            event.clean()
        self.assertIn('rrule', caught.exception.message_dict)
        event = self._event(local(2026, 1, 5, 9, 0), '')
        event.end = event.start
        with self.assertRaises(ValidationError):
            event.clean()
