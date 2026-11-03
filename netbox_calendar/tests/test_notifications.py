from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase

from extras.models import Notification
from tenancy.models import Contact

from .. import jobs, notifications, plugin_settings
from ..choices import EventStatusChoices, NoticeChoices
from ..models import EventObject, EventPerson, EventRole, SentNotice
from .utils import commit, make_device, make_event, make_user


class EditNoticeTests(TestCase):
    def setUp(self):
        self.owner = make_user('owner')
        self.editor = make_user('editor')
        self.contact_role = EventRole.objects.get(slug='technical-contact')

    def test_one_notice_per_transaction(self):
        with mock.patch.object(notifications, 'notify') as notify:
            event = make_event(owner=self.owner)
            event.title = 'Renamed'
            event.save()
            EventObject.objects.create(event=event, object=make_device())
            EventPerson.objects.create(event=event, role=self.contact_role, user=self.editor)
            commit()
        self.assertEqual(notify.call_count, 1)
        self.assertEqual(notify.call_args.args[1], NoticeChoices.CHANGED)

    def test_scheduled_then_cancelled(self):
        with mock.patch.object(notifications, 'notify') as notify:
            event = make_event(owner=self.owner, status=EventStatusChoices.PLANNED)
            commit()
            self.assertEqual(notify.call_count, 0)  # Created quietly

            event.status = EventStatusChoices.CANCELLED
            event.save()
            commit()
            self.assertEqual(notify.call_args.args[1], NoticeChoices.CANCELLED)

            # Moving to in progress is for the start notice, not an edit notice.
            event.status = EventStatusChoices.IN_PROGRESS
            event.save()
            commit()
            self.assertEqual(notify.call_count, 1)

    def test_recipients_skip_the_actor_and_inactive_users(self):
        event = make_event(owner=self.owner)
        gone = make_user('gone', is_active=False)
        EventPerson.objects.create(event=event, role=self.contact_role, user=self.editor)
        EventPerson.objects.create(event=event, role=self.contact_role, user=gone)
        contact = Contact.objects.create(name='Vendor desk', email='desk@example.com')
        EventPerson.objects.create(event=event, role=self.contact_role, contact=contact)

        users, contacts = notifications.recipients(event, actor=self.editor)
        self.assertEqual(users, [self.owner])
        self.assertEqual(contacts, [('Vendor desk', 'desk@example.com')])

    def test_bell_notification_and_email(self):
        event = make_event(owner=self.owner)
        EventPerson.objects.create(event=event, role=self.contact_role, user=self.editor)
        event.event_type.send_email = True
        event.event_type.save()
        plugin_settings.save('direct_email', True)

        told = notifications.notify(event, NoticeChoices.SCHEDULED, actor=self.editor)
        self.assertEqual(told, 1)
        notice = Notification.objects.get(user=self.owner)
        self.assertEqual(notice.event_type, 'netbox_calendar.scheduled')
        self.assertFalse(Notification.objects.filter(user=self.editor).exists())

        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['owner@example.com'])
        name, content, mimetype = message.attachments[0]
        self.assertEqual(name, 'event.ics')
        self.assertIn('METHOD:REQUEST', content)
        self.assertTrue(mimetype.startswith('text/calendar'))

    def test_type_can_turn_a_notice_off(self):
        event = make_event(owner=self.owner)
        event.event_type.notify_on = [NoticeChoices.CANCELLED]
        self.assertEqual(notifications.notify(event, NoticeChoices.CHANGED), 0)
        self.assertFalse(Notification.objects.exists())


class ReminderJobTests(TestCase):
    def test_reminder_sent_once(self):
        event = make_event(owner=make_user('owner'), hours_from_now=1, reminders=[60])
        now = event.start - timedelta(minutes=59)
        with mock.patch.object(notifications, 'notify') as notify:
            self.assertEqual(jobs.send_notices(now), 1)
            self.assertEqual(jobs.send_notices(now + timedelta(minutes=5)), 0)
        self.assertEqual(notify.call_args.args[1], NoticeChoices.REMINDER)
        self.assertEqual(notify.call_args.kwargs['offset'], 60)
        self.assertEqual(SentNotice.objects.count(), 1)

    def test_started_and_status(self):
        event = make_event(owner=make_user('owner'), hours_from_now=1, reminders=[])
        with mock.patch.object(notifications, 'notify') as notify:
            jobs.send_notices(event.start + timedelta(minutes=1))
        self.assertEqual(notify.call_args.args[1], NoticeChoices.STARTED)
        event.refresh_from_db()
        self.assertEqual(event.status, EventStatusChoices.IN_PROGRESS)

    def test_reminder_missed_by_more_than_the_grace_is_dropped(self):
        event = make_event(owner=make_user('owner'), hours_from_now=5, reminders=[240])
        # Two hours after the reminder was due; the grace is one hour.
        late = event.start - timedelta(hours=2)
        with mock.patch.object(notifications, 'notify') as notify:
            jobs.send_notices(late)
        notify.assert_not_called()
