"""Outlook sync against a fake Microsoft Graph: no network."""
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone

from ..models import CalendarConnection, Event, OccurrenceChange
from ..sync import engine, tokens
from ..sync.outlook import OutlookProvider, graph_recurrence
from .utils import make_event, make_user

# The real plugin config with sync turned on; other plugins keep theirs.
PLUGIN_CONFIG = {
    **settings.PLUGINS_CONFIG,
    'netbox_calendar': {
        **settings.PLUGINS_CONFIG.get('netbox_calendar', {}),
        'outlook_sync': True, 'outlook_client_id': 'client', 'outlook_client_secret': 'secret',
    },
}


class FakeGraph:
    """Enough of Graph for the sync: calendars, events, instances, tokens."""

    def __init__(self):
        self.calendars, self.events, self.calls, self.next_id = {}, {}, [], 0

    def _id(self):
        self.next_id += 1
        return f'id{self.next_id}'

    def __call__(self, method, url, **kwargs):
        path = url.split('/v1.0', 1)[-1]
        self.calls.append((method, path))
        body = kwargs.get('json') or {}
        if url.endswith('/token'):
            return 200, {'access_token': 'access', 'refresh_token': 'refresh', 'expires_in': 3600}, {}
        if path == '/me/calendars' and method == 'POST':
            calendar_id = self._id()
            self.calendars[calendar_id] = body
            return 201, {'id': calendar_id}, {}
        if path.startswith('/me/calendars/') and path.endswith('/events') and method == 'POST':
            event_id = self._id()
            self.events[event_id] = dict(body)
            return 201, {'id': event_id}, {}
        if path.startswith('/me/calendars/'):
            calendar_id = path.split('/')[3]
            if calendar_id not in self.calendars:
                return 404, {}, {}
            if method == 'DELETE':
                del self.calendars[calendar_id]
            return 200, {'id': calendar_id}, {}
        if path.endswith('/instances'):
            # One instance per call, at the start asked for plus an hour.
            series_id = path.split('/')[3]
            start = kwargs['params']['startDateTime']
            from datetime import datetime
            original = datetime.fromisoformat(start) + timedelta(hours=1)
            instance_id = f'{series_id}-{original:%Y%m%d}'
            return 200, {'value': [{'id': instance_id, 'originalStart': original.isoformat()}]}, {}
        event_id = path.split('/')[3]
        if method == 'DELETE':
            self.events.pop(event_id, None)
            return 204, {}, {}
        if method == 'PATCH':
            self.events.setdefault(event_id, {}).update(body)
            return 200, {'id': event_id}, {}
        return 404, {}, {}


@override_settings(PLUGINS_CONFIG=PLUGIN_CONFIG, TIME_ZONE='Europe/Helsinki')
class OutlookSyncTests(TestCase):

    def setUp(self):
        self.user = make_user('owner', is_superuser=True)
        self.graph = FakeGraph()
        patcher = mock.patch.object(OutlookProvider, '_http', side_effect=self.graph)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.connection = CalendarConnection.objects.create(
            user=self.user, provider='outlook', refresh_token=tokens.encrypt('refresh'),
        )

    def test_recurrence(self):
        # The 2nd Tuesday of a month, 10:00 local time.
        local = timezone.localtime().replace(hour=10, minute=0, second=0, microsecond=0)
        while local.weekday() != 1 or not 8 <= local.day <= 14:
            local += timedelta(days=1)
        event = Event(start=local, end=local + timedelta(hours=1), rrule='FREQ=MONTHLY;BYDAY=2TU')
        self.assertEqual(graph_recurrence(event)['pattern'], {
            'type': 'relativeMonthly', 'interval': 1, 'daysOfWeek': ['tuesday'], 'index': 'second',
        })
        event.rrule = 'FREQ=HOURLY;INTERVAL=6'
        self.assertIsNone(graph_recurrence(event))

    def test_push_update_and_remove(self):
        event = make_event(self.user, title='Core upgrade')
        engine.sync_connection(self.connection)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.status, CalendarConnection.STATUS_ACTIVE)
        (remote,) = self.graph.events.values()
        self.assertEqual(remote['subject'], 'Core upgrade')

        # Unchanged: nothing sent.
        calls = len(self.graph.calls)
        engine.sync_connection(self.connection)
        self.assertEqual([c for c in self.graph.calls[calls:] if c[0] != 'GET'], [])

        # A new title is patched in place; cancelling removes it.
        # update(): no signals, so an approval plugin can't hold the edit back.
        Event.objects.filter(pk=event.pk).update(title='Core upgrade v2')
        engine.sync_connection(self.connection)
        self.assertEqual(list(self.graph.events.values())[0]['subject'], 'Core upgrade v2')
        Event.objects.filter(pk=event.pk).update(status='cancelled')
        engine.sync_connection(self.connection)
        self.assertEqual(self.graph.events, {})

    def test_skipped_occurrence_is_deleted(self):
        event = make_event(self.user, rrule='FREQ=DAILY;COUNT=5')
        OccurrenceChange.objects.create(event=event, original_start=event.start + timedelta(days=2), reason='Holiday')
        engine.sync_connection(self.connection)
        deletes = [path for method, path in self.graph.calls if method == 'DELETE']
        self.assertEqual(len(deletes), 1)

    def test_expired_sign_in(self):
        make_event(self.user)
        with mock.patch.object(OutlookProvider, '_http', return_value=(400, {'error': 'invalid_grant'}, {})):
            engine.sync_connection(self.connection)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.status, CalendarConnection.STATUS_EXPIRED)
