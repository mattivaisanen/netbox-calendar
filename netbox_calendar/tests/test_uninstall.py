from io import StringIO

from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.test import TestCase

from extras.models import Dashboard, Notification

from ..models import Event
from ..widgets import UpcomingEventsWidget
from .utils import make_event, make_user


class PrepareUninstallTests(TestCase):
    def test_removes_rows_and_widget(self):
        user = make_user('someone')
        event = make_event(owner=user)
        Notification.objects.create(
            user=user, object_type=ContentType.objects.get_for_model(Event), object_id=event.pk,
            event_type='netbox_calendar.scheduled',
        )
        dashboard = Dashboard.objects.create(user=user, layout=[], config={})
        dashboard.add_widget(UpcomingEventsWidget(), x=0, y=0)
        dashboard.save()

        out = StringIO()
        call_command('prepare_calendar_uninstall', dry_run=True, stdout=out)
        self.assertIn('1 notifications', out.getvalue())
        self.assertTrue(Notification.objects.exists())

        call_command('prepare_calendar_uninstall', interactive=False, stdout=StringIO())
        self.assertFalse(Notification.objects.exists())
        dashboard.refresh_from_db()
        self.assertEqual(dashboard.config, {})
        self.assertTrue(Event.objects.filter(pk=event.pk).exists())
