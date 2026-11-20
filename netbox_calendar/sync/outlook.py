"""
Outlook (Microsoft 365) through Microsoft Graph, with delegated
permissions: each user signs in with their own Microsoft account and
consents once. NetBox then acts only on that user's mailbox, with
Calendars.ReadWrite, and keeps a refresh token to stay signed in.

Events go into a calendar of their own ("NetBox" by default), so users can
colour, hide or delete it without touching their own events.

- A single event is one Outlook event.
- A repeating event is an Outlook series when Graph can express its rule
  (graph_recurrence()). A skipped occurrence is deleted from the series, a
  moved one is changed in place.
- A rule Graph can't express (hourly, several weekdays with numbers, ...)
  is sent as single events: the occurrences from SINCE before now to AHEAD
  after it, kept up to date by the hourly sync.

Only NetBox talks to Microsoft (login.microsoftonline.com and
graph.microsoft.com, over HTTPS). The user's browser does the sign-in
redirect, so NetBox itself needs no address reachable from the internet.
"""
import base64
import json
import logging
import time as time_module
from datetime import datetime, timedelta, timezone as dt_timezone
from urllib.parse import quote, urlencode

import requests
from django.conf import settings
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .. import recurrence
from ..ical import absolute_url
from ..lookup import describe
from ..plugin_settings import config_value, get_setting, on
from . import tokens
from .base import AuthExpired, NotFound, Provider, SyncError, digest

logger = logging.getLogger('netbox_calendar.sync')

#: What NetBox asks the user to consent to. openid/profile/email name the
#: account; offline_access gives the refresh token.
SCOPES = 'openid profile email offline_access Calendars.ReadWrite'

#: Seconds to wait for Microsoft before giving up on a request.
TIMEOUT = 20

#: Rules Graph can't express are sent as single events in this window.
SINCE = timedelta(days=30)
AHEAD = timedelta(days=365)
MAX_SINGLES = 400

WEEKDAYS = {
    'MO': 'monday', 'TU': 'tuesday', 'WE': 'wednesday', 'TH': 'thursday',
    'FR': 'friday', 'SA': 'saturday', 'SU': 'sunday',
}
INDEXES = {1: 'first', 2: 'second', 3: 'third', 4: 'fourth', -1: 'last'}
#: RRULE parts graph_recurrence() understands. A rule with any other part is sent as single events.
KNOWN_PARTS = {'FREQ', 'INTERVAL', 'BYDAY', 'BYMONTHDAY', 'BYMONTH', 'BYSETPOS', 'COUNT', 'UNTIL', 'WKST'}


#
# Turning an event into Graph's format
#

def _zone():
    return settings.TIME_ZONE or 'UTC'


def _local_text(value):
    return timezone.localtime(value).strftime('%Y-%m-%dT%H:%M:%S')


def _when(event, value):
    """A Graph dateTimeTimeZone: local wall-clock time in NetBox's time zone."""
    if event.all_day:
        return {'dateTime': timezone.localtime(value).strftime('%Y-%m-%dT00:00:00'), 'timeZone': _zone()}
    return {'dateTime': _local_text(value), 'timeZone': _zone()}


def _byday(value):
    """'2TU' -> (2, 'TU'); 'MO' -> (None, 'MO'). ValueError when it isn't one."""
    value = value.strip()
    day = value[-2:]
    if day not in WEEKDAYS:
        raise ValueError(value)
    number = value[:-2]
    return (int(number) if number else None), day


def graph_recurrence(event):
    """
    The event's RRULE as a Graph patternedRecurrence, or None when Graph
    can't express it (the event is then sent as single events).
    """
    if not event.rrule:
        return None
    parts = recurrence._parts(event.rrule)
    if set(parts) - KNOWN_PARTS:
        return None
    start = timezone.localtime(event.start)
    try:
        interval = int(parts.get('INTERVAL', 1))
        days = [_byday(value) for value in parts['BYDAY'].split(',')] if 'BYDAY' in parts else []
        monthdays = [int(value) for value in parts['BYMONTHDAY'].split(',')] if 'BYMONTHDAY' in parts else []
        months = [int(value) for value in parts['BYMONTH'].split(',')] if 'BYMONTH' in parts else []
        setpos = [int(value) for value in parts['BYSETPOS'].split(',')] if 'BYSETPOS' in parts else []
    except ValueError:
        return None
    if len(monthdays) > 1 or len(months) > 1 or len(setpos) > 1 or any(day < 1 for day in monthdays):
        return None
    numbers = {number for number, _day in days}
    plain_days = [WEEKDAYS[day] for _number, day in days]
    first_day = WEEKDAYS.get(parts.get('WKST', 'MO'), 'monday')
    weekday_of_start = list(WEEKDAYS)[start.weekday()]
    freq = parts.get('FREQ')

    if freq == 'DAILY':
        if monthdays or months or setpos or numbers - {None}:
            return None
        if days:
            # "Every weekday" and similar: Graph has no daily pattern with days, but weekly does the same.
            if interval != 1:
                return None
            pattern = {'type': 'weekly', 'interval': 1, 'daysOfWeek': plain_days, 'firstDayOfWeek': first_day}
        else:
            pattern = {'type': 'daily', 'interval': interval}
    elif freq == 'WEEKLY':
        if monthdays or months or setpos or numbers - {None}:
            return None
        pattern = {
            'type': 'weekly', 'interval': interval, 'firstDayOfWeek': first_day,
            'daysOfWeek': plain_days or [WEEKDAYS[weekday_of_start]],
        }
    elif freq in ('MONTHLY', 'YEARLY'):
        if freq == 'MONTHLY' and months:
            return None
        relative = _relative(days, setpos)
        if relative is False or (relative and monthdays):
            return None
        if relative:
            index, weekdays = relative
            pattern = {'type': 'relativeMonthly', 'interval': interval, 'daysOfWeek': weekdays, 'index': index}
        else:
            if days:
                return None
            pattern = {'type': 'absoluteMonthly', 'interval': interval,
                       'dayOfMonth': monthdays[0] if monthdays else start.day}
        if freq == 'YEARLY':
            pattern['type'] = pattern['type'].replace('Monthly', 'Yearly')
            pattern['month'] = months[0] if months else start.month
    else:
        # HOURLY and anything else.
        return None

    if 'COUNT' in parts:
        try:
            ends = {'type': 'numbered', 'numberOfOccurrences': int(parts['COUNT'])}
        except ValueError:
            return None
    elif 'UNTIL' in parts:
        try:
            until = datetime.strptime(parts['UNTIL'], '%Y%m%dT%H%M%SZ').replace(tzinfo=dt_timezone.utc)
        except ValueError:
            return None
        ends = {'type': 'endDate', 'endDate': timezone.localtime(until).date().isoformat()}
    else:
        ends = {'type': 'noEnd'}
    ends.update({'startDate': start.date().isoformat(), 'recurrenceTimeZone': _zone()})

    # Graph starts a series on its first match on or after the start date.
    # When the rule doesn't happen at the start itself, the two would differ.
    if not recurrence.is_occurrence(event, event.start):
        return None
    return {'pattern': pattern, 'range': ends}


def _relative(days, setpos):
    """
    (index, [weekday]) for "the 2nd Tuesday" style rules, None when the
    rule isn't one, False when it is one Graph can't express.
    """
    numbered = [(number, day) for number, day in days if number is not None]
    if not numbered and not setpos:
        return None
    if numbered and (setpos or len(days) > 1):
        return False
    if numbered:
        number, day = numbered[0]
        return (INDEXES[number], [WEEKDAYS[day]]) if number in INDEXES else False
    # BYSETPOS with plain weekdays: "the last weekday of the month".
    if not days or setpos[0] not in INDEXES:
        return False
    return INDEXES[setpos[0]], [WEEKDAYS[day] for _number, day in days]


def _details(event):
    """What every Outlook copy of the event shows, apart from its time."""
    objects_text = describe(event)
    link = absolute_url(event.get_absolute_url())
    text = '\n\n'.join(part for part in (
        event.description,
        f'Concerns: {objects_text}' if objects_text else '',
        f'Owner: {event.owner_name}' if event.event_owner_id else '',
        f'Impact: {event.get_impact_display()}',
        event.comments,
        link,
    ) if part)
    if event.impact == 'none':
        show_as = 'free'
    elif event.status == 'planned':
        show_as = 'tentative'
    else:
        show_as = 'busy'
    reminders = event.effective_reminders() or []
    details = {
        'subject': event.title,
        'body': {'contentType': 'text', 'content': text},
        'location': {'displayName': objects_text[:255]},
        'categories': [event.event_type.name],
        'showAs': show_as,
        'isReminderOn': bool(reminders),
    }
    if reminders:
        # Outlook has one reminder per event: the earliest of NetBox's.
        details['reminderMinutesBeforeStart'] = max(reminders)
    return details


def _times(event, start, end):
    return {'start': _when(event, start), 'end': _when(event, end), 'isAllDay': event.all_day}


def _occurrence_key(value):
    return value.astimezone(dt_timezone.utc).strftime('%Y%m%dT%H%M%SZ')


def payload(event, now=None):
    """
    Everything the event becomes in Outlook, as plain data:

    - `mode` 'single', 'series' or 'singles' (a rule Graph can't express);
    - `details` (title, text, ...) shared by every copy;
    - `times` and `recurrence` of the event or series;
    - `exceptions`: {occurrence: None (skipped) or times (moved)};
    - `singles`: {occurrence: times} for mode 'singles'.
    """
    data = {'details': _details(event), 'exceptions': {}, 'singles': {}}
    if not event.rrule:
        data.update(mode='single', times=_times(event, event.start, event.end), recurrence=None)
        return data
    rule = graph_recurrence(event)
    if rule is not None:
        data.update(mode='series', times=_times(event, event.start, event.end), recurrence=rule)
        for original, change in sorted(recurrence.changes(event).items()):
            if not recurrence.is_occurrence(event, original):
                continue
            data['exceptions'][_occurrence_key(original)] = (
                _times(event, change.start, change.end) if change.is_moved else None
            )
        return data
    now = now or timezone.now()
    data.update(mode='singles', times=None, recurrence=None)
    for occurrence in recurrence.occurrences(event, now - SINCE, now + AHEAD, limit=MAX_SINGLES):
        data['singles'][_occurrence_key(occurrence.original_start)] = _times(event, occurrence.start, occurrence.end)
    return data


def _schedule(data):
    """The parts of a payload that only a new series can change."""
    return digest([data['mode'], data['times'], data['recurrence'], data['exceptions']])


#
# The provider
#

class OutlookProvider(Provider):
    name = 'outlook'
    label = _('Outlook')
    icon = 'microsoft-outlook'

    # Settings

    def is_enabled(self):
        return on('outlook_sync')

    def client_id(self):
        return (get_setting('outlook_client_id') or '').strip()

    def client_secret(self):
        # Only in configuration.py, never on the Settings page.
        return (config_value('outlook_client_secret') or '').strip()

    def tenant(self):
        return (get_setting('outlook_tenant') or '').strip() or 'organizations'

    def login_url(self):
        return (config_value('outlook_login_url') or 'https://login.microsoftonline.com').rstrip('/')

    def graph_url(self):
        return (config_value('outlook_graph_url') or 'https://graph.microsoft.com/v1.0').rstrip('/')

    def calendar_name(self):
        return (get_setting('sync_calendar_name') or '').strip() or 'NetBox'

    def missing_settings(self):
        missing = []
        if not self.client_id():
            missing.append('outlook_client_id')
        if not self.client_secret():
            missing.append('outlook_client_secret')
        return missing

    # HTTP

    def _http(self, method, url, **kwargs):
        """One request to Microsoft. Tests replace this."""
        response = requests.request(
            method, url, timeout=TIMEOUT, proxies=getattr(settings, 'HTTP_PROXIES', None), **kwargs,
        )
        try:
            body = response.json() if response.content else {}
        except ValueError:
            body = {}
        return response.status_code, body, response.headers

    def _token_request(self, fields):
        status, body, _headers = self._http(
            'POST', f'{self.login_url()}/{quote(self.tenant())}/oauth2/v2.0/token',
            data={'client_id': self.client_id(), 'client_secret': self.client_secret(), 'scope': SCOPES, **fields},
        )
        if status != 200:
            message = body.get('error_description') or body.get('error') or f'HTTP {status}'
            if body.get('error') in ('invalid_grant', 'interaction_required', 'consent_required'):
                raise AuthExpired(message.splitlines()[0])
            raise SyncError(message.splitlines()[0])
        return body

    @staticmethod
    def _store_tokens(connection, body):
        connection.access_token = tokens.encrypt(body.get('access_token', ''))
        connection.access_expires = timezone.now() + timedelta(seconds=int(body.get('expires_in', 3600)))
        # Microsoft hands out a new refresh token now and then; keep the latest.
        if body.get('refresh_token'):
            connection.refresh_token = tokens.encrypt(body['refresh_token'])

    def _access_token(self, connection):
        """A working access token, refreshed when it is about to run out."""
        token = tokens.decrypt(connection.access_token)
        if token and connection.access_expires and connection.access_expires > timezone.now() + timedelta(minutes=2):
            return token
        refresh = tokens.decrypt(connection.refresh_token)
        if not refresh:
            raise AuthExpired('No stored sign-in. Connect again.')
        self._store_tokens(connection, self._token_request({'grant_type': 'refresh_token', 'refresh_token': refresh}))
        connection.save(update_fields=('access_token', 'access_expires', 'refresh_token'))
        return tokens.decrypt(connection.access_token)

    def _graph(self, connection, method, path, json_body=None, params=None, retry=True):
        """A Graph call as the connection's user. Returns the response body."""
        headers = {
            'Authorization': f'Bearer {self._access_token(connection)}',
            # Times come back in NetBox's time zone, the same as sent.
            'Prefer': f'outlook.timezone="{_zone()}"',
        }
        status, body, response_headers = self._http(
            method, f'{self.graph_url()}{path}', headers=headers, json=json_body, params=params,
        )
        if status == 401 and retry:
            # The access token was revoked early: get a new one, once.
            connection.access_expires = None
            return self._graph(connection, method, path, json_body, params, retry=False)
        if status in (429, 503, 504) and retry:
            # Throttled: wait as long as Graph asks (at most 10 s), once.
            try:
                wait = min(int(response_headers.get('Retry-After', 2)), 10)
            except (TypeError, ValueError):
                wait = 2
            time_module.sleep(wait)
            return self._graph(connection, method, path, json_body, params, retry=False)
        if status < 400:
            return body
        error = body.get('error', {}) if isinstance(body, dict) else {}
        text = f"{error.get('code', f'HTTP {status}')}: {error.get('message', '')}".strip(': ')
        if error.get('code') == 'MailboxNotEnabledForRESTAPI':
            # Graph answers 404 for an account without an Exchange Online mailbox.
            raise SyncError(f'{text} The signed-in account has no Outlook mailbox.')
        if status == 404:
            raise NotFound(f'{method} {path}: {text}')
        if status == 401:
            raise AuthExpired('Microsoft refused the sign-in. Connect again.')
        raise SyncError(text)

    # Signing in

    def authorize_url(self, redirect_uri, state, challenge, login_hint=''):
        query = {
            'client_id': self.client_id(),
            'response_type': 'code',
            'redirect_uri': redirect_uri,
            'response_mode': 'query',
            'scope': SCOPES,
            'state': state,
            'code_challenge': challenge,
            'code_challenge_method': 'S256',
            'prompt': 'select_account',
        }
        if login_hint:
            query['login_hint'] = login_hint
        return f'{self.login_url()}/{quote(self.tenant())}/oauth2/v2.0/authorize?{urlencode(query)}'

    def finish(self, connection, code, redirect_uri, verifier):
        body = self._token_request({
            'grant_type': 'authorization_code', 'code': code, 'redirect_uri': redirect_uri,
            'code_verifier': verifier,
        })
        if not body.get('refresh_token'):
            raise SyncError('Microsoft gave no refresh token; check that offline_access is allowed.')
        self._store_tokens(connection, body)
        claims = _id_token_claims(body.get('id_token', ''))
        connection.account = (
            claims.get('preferred_username') or claims.get('email') or claims.get('name') or ''
        )[:255]

    # Pushing

    def _calendar_path(self, connection):
        return f'/me/calendars/{quote(connection.calendar_id, safe="")}'

    def prepare(self, connection):
        self._access_token(connection)
        if connection.calendar_id:
            try:
                self._graph(connection, 'GET', self._calendar_path(connection), params={'$select': 'id'})
                return False
            except NotFound:
                pass  # Deleted in Outlook: make it again below.
        created = self._graph(connection, 'POST', '/me/calendars', {'name': self.calendar_name()})
        connection.calendar_id = created['id']
        connection.save(update_fields=('calendar_id',))
        return True

    def fingerprint(self, event):
        return digest(payload(event))

    def push(self, connection, event, synced):
        data = payload(event)
        schedule = _schedule(data)
        same_series = synced.remote_id and synced.extra.get('schedule') == schedule
        if data['mode'] == 'singles':
            if synced.remote_id:
                # It was a series or a single event before: replace it.
                self._delete(connection, synced.remote_id)
                synced.remote_id = ''
            self._push_singles(connection, event, synced, data)
        elif same_series:
            # Only the title, text and such changed: update in place.
            self._graph(connection, 'PATCH', f'/me/events/{quote(synced.remote_id, safe="")}', data['details'])
            for instance_id in synced.extra.get('moved', {}).values():
                self._graph(connection, 'PATCH', f'/me/events/{quote(instance_id, safe="")}', data['details'])
        else:
            # New, or its time or rule changed: a fresh event or series is
            # simpler and safer than reworking Outlook's exceptions.
            self.remove(connection, synced)
            body = {**data['details'], **data['times'], 'transactionId': _transaction_id(connection, event, schedule)}
            if data['recurrence']:
                body['recurrence'] = data['recurrence']
            created = self._graph(connection, 'POST', f'{self._calendar_path(connection)}/events', body)
            synced.remote_id = created['id']
            synced.extra = {'schedule': schedule, 'moved': self._apply_exceptions(connection, event, created, data)}

    def _apply_exceptions(self, connection, event, created, data):
        """Delete skipped and move moved occurrences of a new series. Returns {occurrence: instance id} of moved ones."""
        moved = {}
        for key, times in data['exceptions'].items():
            original = datetime.strptime(key, '%Y%m%dT%H%M%SZ').replace(tzinfo=dt_timezone.utc)
            instance_id = self._instance(connection, created['id'], original, event.duration)
            if instance_id is None:
                logger.warning('Occurrence %s of event #%s not found in Outlook', key, event.pk)
                continue
            path = f'/me/events/{quote(instance_id, safe="")}'
            if times is None:
                self._graph(connection, 'DELETE', path)
            else:
                self._graph(connection, 'PATCH', path, times)
                moved[key] = instance_id
        return moved

    def _instance(self, connection, series_id, original, duration):
        """The id of the series' occurrence that the rule puts at `original`."""
        found = self._graph(
            connection, 'GET', f'/me/events/{quote(series_id, safe="")}/instances',
            params={
                'startDateTime': (original - timedelta(hours=1)).isoformat(),
                'endDateTime': (original + duration + timedelta(hours=1)).isoformat(),
                '$select': 'id,originalStart',
            },
        )
        for instance in found.get('value', []):
            if _parse_utc(instance.get('originalStart')) == original:
                return instance['id']
        return None

    def _push_singles(self, connection, event, synced, data):
        """Rules Graph can't express: one Outlook event per occurrence, added and removed as the window moves."""
        known = dict(synced.extra.get('singles', {}))
        details_digest = digest(data['details'])
        details_changed = synced.extra.get('details') != details_digest
        for key in list(known):
            entry = known[key]
            if key not in data['singles'] or entry.get('times') != digest(data['singles'][key]):
                self._delete(connection, entry['id'])
                del known[key]
            elif details_changed:
                self._graph(connection, 'PATCH', f'/me/events/{quote(entry["id"], safe="")}', data['details'])
        for key, times in data['singles'].items():
            if key in known:
                continue
            created = self._graph(connection, 'POST', f'{self._calendar_path(connection)}/events', {
                **data['details'], **times, 'transactionId': _transaction_id(connection, event, key + digest(times)),
            })
            known[key] = {'id': created['id'], 'times': digest(times)}
        synced.extra = {'singles': known, 'details': details_digest}

    def _delete(self, connection, remote_id):
        try:
            self._graph(connection, 'DELETE', f'/me/events/{quote(remote_id, safe="")}')
        except NotFound:
            pass

    def remove(self, connection, synced):
        if synced.remote_id:
            self._delete(connection, synced.remote_id)
        for entry in synced.extra.get('singles', {}).values():
            self._delete(connection, entry['id'])
        synced.remote_id = ''
        synced.extra = {}

    def disconnect(self, connection, remove_calendar=True):
        # Microsoft has no call to revoke one app's refresh token; forgetting it is enough.
        if remove_calendar and connection.calendar_id:
            try:
                self._graph(connection, 'DELETE', self._calendar_path(connection))
            except NotFound:
                pass


def _transaction_id(connection, event, tag):
    """
    Graph's guard against duplicates: a create retried with the same
    transactionId is ignored.
    """
    return digest([connection.pk, event.pk, tag])[:40]


def _parse_utc(text):
    """A Graph time ("2026-12-08T07:00:00Z" or with an offset) as a UTC datetime."""
    if not text:
        return None
    try:
        value = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt_timezone.utc)
    return value.astimezone(dt_timezone.utc).replace(microsecond=0)


def _id_token_claims(id_token):
    """
    The claims of the ID token that came with the tokens. It came straight
    from Microsoft's token endpoint over HTTPS, so its signature needn't be
    checked to read the account name (OpenID Connect Core 3.1.3.7).
    """
    try:
        part = id_token.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}
