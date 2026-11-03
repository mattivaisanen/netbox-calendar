from datetime import timedelta

from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import ObjectChange
from extras.models import JournalEntry

from .. import plugin_settings
from ..forms import when_text
from ..models import Event, EventPerson, EventRole, EventType, FeedToken, OccurrenceChange
from ..widgets import UpcomingEventsWidget
from .utils import grant, make_device, make_event, make_user


def url(name, *args):
    return reverse(f'plugins:netbox_calendar:{name}', args=args)


class PageTests(TestCase):
    def setUp(self):
        self.admin = make_user('admin', is_superuser=True)
        self.client.force_login(self.admin)
        self.device = make_device()
        self.event = make_event(owner=self.admin, hours_from_now=2, rrule='FREQ=WEEKLY')
        self.event.links.create(object=self.device)

    def test_pages_load(self):
        pages = [
            url('month'), url('month') + '?year=2026&month=2', url('timeline'), url('timeline') + '?past=true',
            url('my_feed'), url('settings'),
            url('event_list'), url('event', self.event.pk), url('event_add'), url('event_edit', self.event.pk),
            url('event_link', self.event.pk), url('event_person_add', self.event.pk),
            url('eventtype_list'), url('eventrole_list'),
            reverse('dcim:device', args=[self.device.pk]),
        ]
        for page in pages:
            with self.subTest(page=page):
                self.assertEqual(self.client.get(page).status_code, 200)

    def test_device_page_shows_its_events(self):
        response = self.client.get(reverse('dcim:device', args=[self.device.pk]))
        self.assertContains(response, self.event.title)

    def test_widget(self):
        request = RequestFactory().get('/')
        request.user = self.admin
        html = UpcomingEventsWidget(config={'days': 3}).render(request)
        self.assertIn(self.event.title, html)

    def test_add_event_with_links(self):
        start = (timezone.localtime() + timedelta(days=3)).replace(hour=10, minute=0, second=0, microsecond=0)
        response = self.client.post(url('event_add'), {
            'title': 'Replace power supply',
            'event_type': EventType.objects.get(slug='planned-maintenance').pk,
            'status': 'planned',
            'impact': 'degraded',
            'start': start.strftime('%Y-%m-%d %H:%M:%S'),
            'repeat': 'monthly_weekday',
            'repeat_interval': 1,
            'include__dcim__device': [self.device.pk],
        })
        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(title='Replace power supply')
        self.assertEqual(event.event_owner, self.admin)
        self.assertEqual(event.end - event.start, timedelta(minutes=120))  # The type's default
        self.assertTrue(event.rrule.startswith('FREQ=MONTHLY;BYDAY='))
        self.assertEqual(event.links.get().object, self.device)

    def test_add_people_in_several_roles(self):
        # People are added on the event page, several per role.
        engineer, lead = make_user('engineer'), make_user('lead')
        technical = EventRole.objects.get(slug='technical-contact')
        response = self.client.post(url('event_person_add', self.event.pk), {
            'role': technical.pk, 'users': [engineer.pk, lead.pk], '_addanother': '',
        })
        self.assertRedirects(response, url('event_person_add', self.event.pk), fetch_redirect_response=False)
        other = EventRole.objects.exclude(pk=technical.pk).first()
        self.client.post(url('event_person_add', self.event.pk), {'role': other.pk, 'users': [lead.pk]})
        people = set(self.event.people.values_list('user__username', 'role__slug'))
        self.assertEqual(people, {('engineer', technical.slug), ('lead', technical.slug), ('lead', other.slug)})

        # A role with nobody picked is refused.
        response = self.client.post(url('event_person_add', self.event.pk), {'role': technical.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Pick at least one')

    def test_end_by_length_or_end_date_and_repeat_ends(self):
        start = (timezone.localtime() + timedelta(days=3)).replace(hour=10, minute=0, second=0, microsecond=0)
        data = {
            'title': 'Firmware upgrade', 'event_type': EventType.objects.get(slug='upgrade').pk,
            'status': 'planned', 'impact': 'none', 'start': start.strftime('%Y-%m-%d %H:%M:%S'),
            'length': '1h 30m', 'end': (start + timedelta(hours=5)).strftime('%Y-%m-%d %H:%M:%S'),
            'repeat': 'weekly', 'repeat_interval': 1, 'repeat_until': '2030-01-01', 'repeat_count': 4,
        }
        # End by and Ends decide: the length, and the number of times.
        response = self.client.post(url('event_add'), {**data, 'end_by': 'length', 'repeat_end_by': 'count'})
        self.assertEqual(response.status_code, 302)
        event = Event.objects.get(title='Firmware upgrade')
        self.assertEqual(event.end - event.start, timedelta(minutes=90))
        self.assertIn('COUNT=4', event.rrule)
        self.assertNotIn('UNTIL', event.rrule)
        # The edit form opens with the length filled in.
        self.assertEqual(self.client.get(url('event_edit', event.pk)).context['form'].initial['length'], '1h 30m')

        # The others: the end date, and the last day.
        response = self.client.post(url('event_edit', event.pk), {**data, 'end_by': 'end', 'repeat_end_by': 'until'})
        self.assertEqual(response.status_code, 302)
        event.refresh_from_db()
        self.assertEqual(event.end - event.start, timedelta(hours=5))
        self.assertIn('UNTIL=2030', event.rrule)
        self.assertNotIn('COUNT', event.rrule)

        # A weekly repeat must include the start's weekday, and cannot end before the start.
        other_day = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'][(start.weekday() + 1) % 7]
        response = self.client.post(url('event_edit', event.pk), {
            **data, 'repeat_weekdays': [other_day], 'repeat_until': '2020-01-01', 'repeat_end_by': 'until',
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(response.context['form'].errors), {'repeat_weekdays', 'repeat_until'})

        # The date pickers start the week on the configured day (flatpickr: 1 = Monday).
        self.assertContains(self.client.get(url('event_add')), 'data-first-weekday="1"')

    def test_when_section_end_by_ends_all_day_and_next_times(self):
        # End by: only the field in use is shown, Length to begin with.
        page = self.client.get(url('event_add'))
        self.assertIn('length', page.context['form'].fields)
        self.assertNotIn('end', page.context['form'].fields)
        self.assertNotContains(page, 'id="repeat_next"')
        page = self.client.get(url('event_add'), {'end_by': 'end'})
        self.assertNotIn('length', page.context['form'].fields)
        self.assertIn('end', page.context['form'].fields)

        # A repeat lists its next times (in NetBox's date format), as the form is filled in.
        start = (timezone.localtime() + timedelta(days=3)).replace(hour=10, minute=0, second=0, microsecond=0)
        query = {'start': start.strftime('%Y-%m-%d %H:%M:%S'), 'repeat': 'daily', 'repeat_interval': 2, 'end_by': ''}
        page = self.client.get(url('event_add'), {**query, 'length': '1h 30m'})
        self.assertContains(page, 'id="repeat_next"')
        for days in (0, 2, 4):
            self.assertContains(page, when_text(start + timedelta(days=days)))
        # The length shows the end it gives.
        self.assertContains(page, 'ends ' + when_text(start + timedelta(minutes=90)))
        # Ends: no end to begin with (no fields), until a day, or a number of times, which the next times follow.
        self.assertNotIn('repeat_until', page.context['form'].fields)
        self.assertNotIn('repeat_count', page.context['form'].fields)
        page = self.client.get(url('event_add'), {**query, 'repeat_end_by': 'until'})
        self.assertIn('repeat_until', page.context['form'].fields)
        self.assertNotIn('repeat_count', page.context['form'].fields)
        page = self.client.get(url('event_add'), {**query, 'repeat_end_by': 'count', 'repeat_count': 2})
        self.assertNotIn('repeat_until', page.context['form'].fields)
        self.assertNotContains(page, when_text(start + timedelta(days=4)))

        # All day: the start is picked as a date, End by and Length are gone, and the next times are days.
        # NetBox sends its hidden empty value along with the ticked box.
        page = self.client.get(url('event_add'), {**query, 'all_day': ['', 'on']})
        form = page.context['form']
        self.assertEqual(form.fields['start'].widget.__class__.__name__, 'CalendarDatePicker')
        self.assertEqual(form.initial['start'], start.strftime('%Y-%m-%d'))
        self.assertNotIn('end_by', form.fields)
        self.assertNotIn('length', form.fields)
        self.assertContains(page, when_text(start, all_day=True))
        self.assertNotContains(page, when_text(start))

        # Saved as all day without a last day: the start day only. With No end, the rule has no end.
        data = {
            'title': 'Freeze', 'event_type': EventType.objects.get(slug='upgrade').pk, 'status': 'planned',
            'impact': 'none', 'start': start.strftime('%Y-%m-%d'), 'all_day': 'on', 'repeat': 'weekly',
            'repeat_interval': 1, 'repeat_end_by': 'never', 'repeat_until': '2030-01-01',
        }
        self.assertEqual(self.client.post(url('event_add'), data).status_code, 302)
        event = Event.objects.get(title='Freeze')
        self.assertEqual(event.end - event.start, timedelta(days=1))
        self.assertNotIn('UNTIL', event.rrule)
        self.assertNotIn('COUNT', event.rrule)

        # Redrawn with All day unticked, an all-day event's form shows times again.
        self.event.all_day = True
        self.event.save()
        # NetBox draws a hidden empty "all_day" next to the tick box, so an unticked box sends "".
        page = self.client.get(url('event_edit', self.event.pk), {**query, 'all_day': ''})
        self.assertEqual(page.context['form'].fields['start'].widget.__class__.__name__, 'CalendarDateTimePicker')

    def test_edit_keeps_links_and_lists_role_devices(self):
        # The edit form shows the linked device, so saving it unchanged keeps the link.
        form = self.client.get(url('event_edit', self.event.pk)).context['form']
        self.assertEqual(list(form.initial['include__dcim__device']), [self.device.pk])

        # Swap the device for its role: the role's devices are then listed as affected.
        second = make_device('edge-2')
        response = self.client.post(url('event_edit', self.event.pk), {
            'title': self.event.title, 'event_type': self.event.event_type.pk, 'status': 'planned',
            'impact': 'none', 'start': timezone.localtime(self.event.start).strftime('%Y-%m-%d %H:%M:%S'),
            'repeat': 'weekly', 'repeat_interval': 1, 'objects_shown': '1',
            'include__dcim__devicerole': [self.device.role.pk],
        })
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.event.links.get().object, self.device.role)
        page = self.client.get(url('event', self.event.pk))
        self.assertEqual(page.context['devices_total'], 2)
        self.assertContains(page, second.name)

        # Unlinking asks first.
        link = self.event.links.get()
        self.assertEqual(self.client.get(url('event_unlink', self.event.pk, link.pk)).status_code, 200)
        self.assertTrue(self.event.links.exists())

    def test_reminders_from_tick_boxes_and_text(self):
        event_type = EventType.objects.get(slug='upgrade')
        response = self.client.post(url('eventtype_edit', event_type.pk), {
            'name': event_type.name, 'slug': event_type.slug, 'color': event_type.color,
            'default_duration': event_type.default_duration,
            'reminder_presets': ['60', '1440'], 'reminder_other': '3d, 45m',
        })
        self.assertEqual(response.status_code, 302)
        event_type.refresh_from_db()
        self.assertEqual(event_type.reminders, [4320, 1440, 60, 45])

        # The edit form ticks the common times and lists the rest as text.
        page = self.client.get(url('eventtype_edit', event_type.pk))
        form = page.context['form']
        self.assertEqual(form.initial['reminder_presets'], ['1440', '60'])
        self.assertEqual(form.initial['reminder_other'], '3d, 45m')
        # Drawn as Bootstrap checkboxes, not bare inputs.
        self.assertContains(page, 'class="form-check form-check-inline"')
        self.assertContains(page, 'class="form-check-input"')

        # An event with its own reminders opens with them picked.
        self.event.reminders = [15]
        self.event.save()
        form = self.client.get(url('event_edit', self.event.pk)).context['form']
        self.assertEqual(form.initial['reminder_source'], 'own')

    def test_skip_move_and_restore_an_occurrence(self):
        occurrence = self.event.next_occurrence()
        page = url('event_occurrence', self.event.pk)
        value = occurrence.start.isoformat()
        self.assertEqual(self.client.get(page, {'occurrence': value}).status_code, 200)
        # Not an occurrence of the series.
        self.assertEqual(self.client.get(page, {'occurrence': (occurrence.start + timedelta(hours=1)).isoformat()})
                         .status_code, 404)

        # A reason is always needed.
        response = self.client.post(page, {'occurrence': value, 'action': 'skip', 'reason': ''})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(OccurrenceChange.objects.exists())

        self.client.post(page, {'occurrence': value, 'action': 'skip', 'reason': 'Holiday'})
        change = OccurrenceChange.objects.get()
        self.assertTrue(change.is_skipped)
        self.assertEqual(change.reason, 'Holiday')
        self.assertNotEqual(self.event.next_occurrence().start, occurrence.start)

        # Moving it instead: the same change, with new times.
        new_start = timezone.localtime(occurrence.start + timedelta(days=1))
        self.client.post(page, {
            'occurrence': value, 'action': 'move', 'reason': 'Vendor late',
            'start': new_start.strftime('%Y-%m-%d %H:%M:%S'),
            'end': (new_start + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S'),
        })
        change = OccurrenceChange.objects.get()
        self.assertEqual((change.start, change.reason), (new_start, 'Vendor late'))
        self.assertEqual(self.event.next_occurrence().start, new_start)
        self.assertContains(self.client.get(url('event', self.event.pk)), 'Vendor late')

        self.client.post(page, {'occurrence': value, 'action': 'restore', 'reason': 'Back on'})
        self.assertFalse(OccurrenceChange.objects.exists())
        self.assertEqual(self.event.next_occurrence().start, occurrence.start)

        # Each step is in the event's journal with its reason, and in its change log.
        journal = list(JournalEntry.objects.filter(assigned_object_id=self.event.pk).values_list('comments', flat=True))
        self.assertEqual(len(journal), 3)
        self.assertTrue(all('Reason: ' in text for text in journal))
        self.assertTrue(any(text.endswith('Back on') for text in journal))
        self.assertEqual(
            set(ObjectChange.objects.filter(related_object_id=self.event.pk).exclude(message='')
                .values_list('message', flat=True)),
            {'Holiday', 'Vendor late', 'Back on'},
        )

    def test_journal_on_the_event_page(self):
        page = url('event', self.event.pk)
        self.assertContains(self.client.get(page), 'Add Entry')
        response = self.client.post(url('event_journal_add', self.event.pk), {'kind': 'info', 'comments': 'Vendor confirmed'})
        # Back on the event, not the Journal tab, with the new entry listed.
        self.assertRedirects(response, page)
        entry = JournalEntry.objects.get()
        self.assertEqual((entry.assigned_object, entry.created_by), (self.event, self.admin))
        self.assertContains(self.client.get(page), 'Vendor confirmed')

        # Text is required; nothing is saved without it.
        self.client.post(url('event_journal_add', self.event.pk), {'kind': 'info', 'comments': ''})
        self.assertEqual(JournalEntry.objects.count(), 1)


class EditRuleTests(TestCase):
    def setUp(self):
        self.owner = make_user('owner')
        self.other = make_user('other')
        for user in (self.owner, self.other):
            grant(user, ['view', 'change', 'delete'])
        self.event = make_event(owner=self.owner)

    def _edit(self, user):
        self.client.force_login(user)
        return self.client.get(url('event_edit', self.event.pk)).status_code

    def test_only_owner_and_editing_roles(self):
        self.assertEqual(self._edit(self.owner), 200)
        self.assertEqual(self._edit(self.other), 404)

        observer = EventRole.objects.get(slug='observer')
        person = EventPerson.objects.create(event=self.event, role=observer, user=self.other)
        self.assertEqual(self._edit(self.other), 404)
        person.role = EventRole.objects.get(slug='technical-contact')
        person.save()
        self.assertEqual(self._edit(self.other), 200)

    def test_manage_permission_and_setting(self):
        grant(self.other, ['manage'])
        self.assertEqual(self._edit(self.other), 200)

        third = make_user('third')
        grant(third, ['view', 'change'])
        plugin_settings.save('restrict_editing', False)
        self.assertEqual(self._edit(third), 200)


class FeedTests(TestCase):
    def setUp(self):
        self.user = make_user('reader')
        grant(self.user, ['view'])
        self.event = make_event(owner=self.user)
        self.token = FeedToken.objects.create(user=self.user, token='a' * 40)

    def test_feed_needs_no_login(self):
        response = self.client.get(url('feed', self.token.token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/calendar; charset=utf-8')
        self.assertContains(response, f'SUMMARY:{self.event.title}')

    def test_feed_holds_only_what_the_user_may_view(self):
        self.user.object_permissions.all().delete()
        self.assertNotContains(self.client.get(url('feed', self.token.token)), 'BEGIN:VEVENT')

    def test_bad_token_and_feeds_off(self):
        self.assertEqual(self.client.get(url('feed', 'b' * 40)).status_code, 404)
        plugin_settings.save('ical_feeds', False)
        self.assertEqual(self.client.get(url('feed', self.token.token)).status_code, 404)

    def test_reset_changes_the_address(self):
        self.client.force_login(self.user)
        self.client.post(url('my_feed'))
        self.assertNotEqual(FeedToken.objects.get(user=self.user).token, self.token.token)


class APITests(TestCase):
    def test_create_sets_the_owner(self):
        user = make_user('api')
        grant(user, ['view', 'add', 'change'])
        self.client.force_login(user)
        start = timezone.now() + timedelta(days=1)
        response = self.client.post(
            reverse('plugins-api:netbox_calendar-api:event-list'),
            {
                'title': 'Restart controller', 'event_type': EventType.objects.get(slug='upgrade').pk,
                'start': start.isoformat(), 'end': (start + timedelta(hours=1)).isoformat(),
                'rrule': 'FREQ=WEEKLY',
            },
            content_type='application/json', HTTP_ACCEPT='application/json',
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['event_owner']['id'], user.pk)
        self.assertTrue(response.json()['recurrence'].startswith('Every week on '))
