"""
Google Calendar (Google Workspace or a personal Google account) through
the Calendar API. Each user signs in with their own Google account and
consents once; NetBox keeps a refresh token.

The scope is `calendar.app.created`: NetBox may create calendars and
manage only those, and can't see or change anything else in the account.
Events go into a calendar of their own ("NetBox" by default).

Google understands iCal rules directly, so a repeating event is one
series with its RRULE, and skipped occurrences are EXDATEs. A moved
occurrence is changed in place as an instance of the series.

NetBox picks the Google event ids itself, from the event and its
schedule, so a retried create can't make a duplicate, and a new time or
rule (a new id) replaces the series cleanly.
"""
import logging
from datetime import timedelta, timezone as dt_timezone
from urllib.parse import quote, urlencode

from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .. import recurrence
from ..plugin_settings import config_value, get_setting, on
from . import tokens
from .base import AuthExpired, NotFound, Provider, SyncError, digest, event_text, id_token_claims

logger = logging.getLogger('netbox_calendar.sync')

AUTH_URL = 'https://accounts.google.com/o/oauth2/v2/auth'
TOKEN_URL = 'https://oauth2.googleapis.com/token'
API_URL = 'https://www.googleapis.com/calendar/v3'

#: Only calendars NetBox makes itself, plus the account's address.
SCOPES = 'openid email https://www.googleapis.com/auth/calendar.app.created'

#: Google allows at most five reminders, each at most four weeks ahead.
MAX_REMINDERS = 5
MAX_REMINDER_MINUTES = 40320


def _zone():
    return settings.TIME_ZONE or 'UTC'


def _when(event, value):
    """A Google start or end: a date for all-day events, else local time in NetBox's zone."""
    local = timezone.localtime(value)
    if event.all_day:
        return {'date': local.date().isoformat()}
    return {'dateTime': local.strftime('%Y-%m-%dT%H:%M:%S'), 'timeZone': _zone()}


def _rule_moment(event, value):
    """An occurrence's original start as Google writes it in instance ids and EXDATEs (UTC, or a date)."""
    local = timezone.localtime(value)
    if event.all_day:
        return local.strftime('%Y%m%d')
    return value.astimezone(dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def payload(event):
    """
    Everything the event becomes in Google, as plain data: the series or
    single event (`body`), and the moved occurrences (`moved`, {original
    start: new start and end}).
    """
    objects_text, text = event_text(event)
    reminders = sorted({minutes for minutes in (event.effective_reminders() or []) if minutes <= MAX_REMINDER_MINUTES})
    body = {
        'summary': event.title,
        'description': text,
        'location': objects_text,
        'start': _when(event, event.start),
        'end': _when(event, event.end),
        'status': 'tentative' if event.status == 'planned' else 'confirmed',
        # Impact "none" doesn't block anyone's time.
        'transparency': 'transparent' if event.impact == 'none' else 'opaque',
        'reminders': {
            'useDefault': False,
            'overrides': [{'method': 'popup', 'minutes': minutes} for minutes in reminders[-MAX_REMINDERS:]],
        },
        'source': {'title': 'NetBox', 'url': _link(event)} if _link(event).startswith('http') else None,
    }
    if body['source'] is None:
        del body['source']
    moved = {}
    if event.rrule:
        lines = [f'RRULE:{event.rrule}']
        for original, change in sorted(recurrence.changes(event).items()):
            if not recurrence.is_occurrence(event, original):
                continue
            if change.is_moved:
                moved[_rule_moment(event, original)] = {
                    'start': _when(event, change.start), 'end': _when(event, change.end),
                }
            elif event.all_day:
                lines.append(f'EXDATE;VALUE=DATE:{_rule_moment(event, original)}')
            else:
                lines.append(f'EXDATE:{_rule_moment(event, original)}')
        body['recurrence'] = lines
    return {'body': body, 'moved': moved}


def _link(event):
    from ..ical import absolute_url

    return absolute_url(event.get_absolute_url())


def _schedule(data):
    """What only a new series can change: the times, the rule and the moved occurrences."""
    body = data['body']
    return digest([body['start'], body['end'], body.get('recurrence'), data['moved']])


def _event_id(connection, event, schedule):
    """
    The Google id of this version of the event. Google allows the
    characters a-v and 0-9; a hex digest fits.
    """
    return 'nb' + digest([connection.pk, event.pk, schedule])[:40]


class GoogleProvider(Provider):
    name = 'google'
    label = _('Google Calendar')
    title = _('Google Calendar')
    icon = 'google'

    # Settings

    def is_enabled(self):
        return on('google_sync')

    def client_id(self):
        return (get_setting('google_client_id') or '').strip()

    def client_secret(self):
        # Only in configuration.py, never on the Settings page.
        return (config_value('google_client_secret') or '').strip()

    def calendar_name(self):
        return (get_setting('sync_calendar_name') or '').strip() or 'NetBox'

    def missing_settings(self):
        missing = []
        if not self.client_id():
            missing.append('google_client_id')
        if not self.client_secret():
            missing.append('google_client_secret')
        return missing

    # Tokens

    def _token_request(self, fields):
        status, body, _headers = self._http('POST', TOKEN_URL, data={
            'client_id': self.client_id(), 'client_secret': self.client_secret(), **fields,
        })
        if status != 200:
            message = body.get('error_description') or body.get('error') or f'HTTP {status}'
            if body.get('error') == 'invalid_grant':
                raise AuthExpired(message)
            raise SyncError(message)
        return body

    @staticmethod
    def _store_tokens(connection, body):
        connection.access_token = tokens.encrypt(body.get('access_token', ''))
        connection.access_expires = timezone.now() + timedelta(seconds=int(body.get('expires_in', 3600)))
        if body.get('refresh_token'):
            connection.refresh_token = tokens.encrypt(body['refresh_token'])

    def _access_token(self, connection):
        token = tokens.decrypt(connection.access_token)
        if token and connection.access_expires and connection.access_expires > timezone.now() + timedelta(minutes=2):
            return token
        refresh = tokens.decrypt(connection.refresh_token)
        if not refresh:
            raise AuthExpired('No stored sign-in. Connect again.')
        self._store_tokens(connection, self._token_request({'grant_type': 'refresh_token', 'refresh_token': refresh}))
        connection.save(update_fields=('access_token', 'access_expires', 'refresh_token'))
        return tokens.decrypt(connection.access_token)

    def _api(self, connection, method, path, json_body=None, params=None, retry=True):
        """A Calendar API call as the connection's user. Returns the response body."""
        status, body, _headers = self._http(
            method, f'{API_URL}{path}', json=json_body, params=params,
            headers={'Authorization': f'Bearer {self._access_token(connection)}'},
        )
        if status == 401 and retry:
            connection.access_expires = None
            return self._api(connection, method, path, json_body, params, retry=False)
        if status < 400:
            return body
        error = body.get('error', {}) if isinstance(body, dict) else {}
        text = f"{status}: {error.get('message', '')}".strip(': ') if isinstance(error, dict) else str(error)
        if status in (404, 410):
            raise NotFound(f'{method} {path}: {text}')
        if status == 401:
            raise AuthExpired('Google refused the sign-in. Connect again.')
        raise SyncError(text)

    # Signing in

    def authorize_url(self, redirect_uri, state, challenge, login_hint=''):
        query = {
            'client_id': self.client_id(),
            'redirect_uri': redirect_uri,
            'response_type': 'code',
            'scope': SCOPES,
            'state': state,
            'code_challenge': challenge,
            'code_challenge_method': 'S256',
            # A refresh token, every time (Google only gives one with consent).
            'access_type': 'offline',
            'prompt': 'consent select_account',
            'include_granted_scopes': 'true',
        }
        if login_hint:
            query['login_hint'] = login_hint
        return f'{AUTH_URL}?{urlencode(query)}'

    def finish(self, connection, code, redirect_uri, verifier):
        body = self._token_request({
            'grant_type': 'authorization_code', 'code': code, 'redirect_uri': redirect_uri,
            'code_verifier': verifier,
        })
        if not body.get('refresh_token'):
            raise SyncError('Google gave no refresh token. Remove NetBox under your Google account\'s '
                            'third-party access and connect again.')
        if 'calendar.app.created' not in body.get('scope', 'calendar.app.created'):
            raise SyncError('The calendar permission was not granted. Connect again and allow it.')
        self._store_tokens(connection, body)
        connection.account = (id_token_claims(body.get('id_token', '')).get('email') or '')[:255]

    # Pushing

    def _calendar_path(self, connection):
        return f'/calendars/{quote(connection.calendar_id, safe="")}'

    def prepare(self, connection):
        self._access_token(connection)
        if connection.calendar_id:
            try:
                self._api(connection, 'GET', self._calendar_path(connection))
                return False
            except NotFound:
                pass  # Deleted in Google Calendar: make it again.
        created = self._api(connection, 'POST', '/calendars', {
            'summary': self.calendar_name(), 'timeZone': _zone(),
        })
        connection.calendar_id = created['id']
        connection.save(update_fields=('calendar_id',))
        return True

    def fingerprint(self, event):
        return digest(payload(event))

    def push(self, connection, event, synced):
        data = payload(event)
        schedule = _schedule(data)
        event_id = _event_id(connection, event, schedule)
        events = f'{self._calendar_path(connection)}/events'
        if synced.remote_id == event_id:
            # Only the text changed: replace the series' details, keep its id.
            self._api(connection, 'PUT', f'{events}/{event_id}', {**data['body'], 'id': event_id})
        else:
            self.remove(connection, synced)
            try:
                self._api(connection, 'POST', events, {**data['body'], 'id': event_id})
            except SyncError as error:
                if not str(error).startswith('409'):
                    raise
                # Made before (a retried job) or deleted and now wanted again.
                self._api(connection, 'PUT', f'{events}/{event_id}', {**data['body'], 'id': event_id})
            synced.remote_id = event_id
        # Moved occurrences: instances of the series, changed in place.
        for moment, times in data['moved'].items():
            self._api(connection, 'PATCH', f'{events}/{event_id}_{moment}', {
                **{key: value for key, value in data['body'].items() if key not in ('recurrence', 'start', 'end')},
                **times,
            })
        synced.extra = {}

    def remove(self, connection, synced):
        if synced.remote_id:
            try:
                self._api(connection, 'DELETE', f'{self._calendar_path(connection)}/events/{synced.remote_id}')
            except NotFound:
                pass
        synced.remote_id = ''
        synced.extra = {}

    def disconnect(self, connection, remove_calendar=True):
        if remove_calendar and connection.calendar_id:
            try:
                self._api(connection, 'DELETE', self._calendar_path(connection))
            except NotFound:
                pass
        # Give the refresh token back, so NetBox drops out of the account's third-party access.
        refresh = tokens.decrypt(connection.refresh_token)
        if refresh:
            try:
                self._http('POST', 'https://oauth2.googleapis.com/revoke', data={'token': refresh})
            except Exception:  # Revoking is a courtesy; disconnecting goes on regardless.
                logger.warning('Revoking the Google token of connection #%s failed', connection.pk)
