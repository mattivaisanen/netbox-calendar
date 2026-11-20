from datetime import date, datetime

from django.test import SimpleTestCase
from django.utils import timezone

from netbox_calendar import agenda


def entry(title, first, last):
    """An all-day entry from `first` to the end of `last` (dates in November 2026)."""
    start = timezone.make_aware(datetime(2026, 11, first))
    end = timezone.make_aware(datetime(2026, 11, last + 1))
    return agenda.Entry(title=title, start=start, end=end, url='', color='aaaaaa', all_day=True)


class MonthGridTests(SimpleTestCase):
    def test_multi_day_entry_is_one_bar_per_week(self):
        # November 2026 shown from Monday 26 October; weeks start 2, 9, 16 ... November.
        long = entry('Long', 6, 11)    # Friday 6 to Wednesday 11
        short = entry('Short', 7, 7)   # Saturday 7, under the long bar
        weeks = agenda.month_grid(2026, 11, [long, short])
        week = next(w for w in weeks if w.days[0].date == date(2026, 11, 2))
        following = next(w for w in weeks if w.days[0].date == date(2026, 11, 9))

        bars = {bar.entry.title: bar for bar in week.bars}
        self.assertEqual((bars['Long'].column, bars['Long'].span, bars['Long'].lane), (5, 3, 1))
        self.assertTrue(bars['Long'].continues_after)
        self.assertEqual((bars['Short'].column, bars['Short'].lane), (6, 2))

        [bar] = following.bars
        self.assertEqual((bar.column, bar.span, bar.lane), (1, 3, 1))
        self.assertTrue(bar.continues_before and not bar.starts_here)
