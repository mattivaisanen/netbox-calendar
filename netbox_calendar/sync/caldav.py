"""
CalDAV (RFC 4791): Nextcloud, Fastmail, iCloud, SOGo, Zimbra, Radicale,
Baïkal, mailbox.org and other calendar servers. Users connect with a
server address, their user name and an app password; no app registration
is needed, and a server inside your network works too.

NetBox makes a calendar of its own on the server (MKCALENDAR in the
user's calendar home), or uses the calendar the address points at when it
is one. Each event is one .ics resource there, written by ical.py the same
as the feed: a series with EXDATEs for skipped occurrences and
RECURRENCE-ID overrides for moved ones, so nothing is lost on the way.

Stored on CalendarConnection: `server` the address the user gave,
`account` the user name, `refresh_token` the encrypted app password,
`calendar_id` the calendar's URL, `own_calendar` whether NetBox made it.
"""
import logging
import re
import secrets
from urllib.parse import quote, urljoin, urlparse
from xml.sax.saxutils import escape as xml_escape

import requests
from django.conf import settings
from django.utils.translation import gettext_lazy as _

from .. import ical
from ..plugin_settings import get_setting, on
from . import tokens
from .base import AuthExpired, NotFound, Provider, SyncError, digest

try:
    # Answers come from servers users name: parse them safely when NetBox has defusedxml (it usually does).
    from defusedxml import ElementTree
except ImportError:  # pragma: no cover
    from xml.etree import ElementTree

logger = logging.getLogger('netbox_calendar.sync')

DAV = '{DAV:}'
CAL = '{urn:ietf:params:xml:ns:caldav}'
MAX_REDIRECTS = 5

PROPFIND_BODY = (
    '<?xml version="1.0" encoding="utf-8"?>'
    '<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
    '<d:prop><d:resourcetype/><d:current-user-principal/><c:calendar-home-set/></d:prop>'
    '</d:propfind>'
)


def _mkcalendar_body(name):
    name = xml_escape(name)
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<c:mkcalendar xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:set><d:prop>'
        f'<d:displayname>{name}</d:displayname>'
        '<c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set>'
        '</d:prop></d:set></c:mkcalendar>'
    )


def ics(event):
    """The event as the .ics resource stored on the server."""
    # No METHOD (not allowed in a stored resource) and no attendees, so the
    # server sends nobody an invitation.
    return ical.calendar([event], name=None, method=None, describe=ical_describe)


def ical_describe(event):
    from ..lookup import describe

    return describe(event)


def _without_stamp(text):
    """The .ics without DTSTAMP and SEQUENCE, which change on every save: for fingerprints."""
    return re.sub(r'^(DTSTAMP|SEQUENCE):.*\r?\n', '', text, flags=re.MULTILINE)


class CalDAVProvider(Provider):
    name = 'caldav'
    label = _('CalDAV')
    title = _('CalDAV Calendar')
    icon = 'calendar-sync-outline'
    uses_oauth = False

    def is_enabled(self):
        return on('caldav_sync')

    def missing_settings(self):
        return []

    def fixed_server(self):
        return (get_setting('caldav_server') or '').strip()

    # HTTP (WebDAV methods, raw bodies)

    def _dav(self, connection, method, url, body=None, headers=None, password=None):
        """
        One WebDAV request with the connection's login, following redirects
        by hand (requests would turn a redirected PROPFIND into a GET).
        Returns (status, text, final URL).
        """
        auth = (connection.account, password if password is not None else tokens.decrypt(connection.refresh_token))
        for _hop in range(MAX_REDIRECTS):
            try:
                response = requests.request(
                    method, url, data=body.encode() if isinstance(body, str) else body, headers=headers or {},
                    auth=auth, timeout=self.timeout, allow_redirects=False,
                    proxies=getattr(settings, 'HTTP_PROXIES', None),
                )
            except requests.RequestException as error:
                # Unreachable, DNS, TLS or timeout: shown to the user, never a server error.
                raise SyncError(f'Could not reach {url}: {type(error).__name__}')
            if response.status_code in (301, 302, 303, 307, 308) and response.headers.get('Location'):
                url = urljoin(url, response.headers['Location'])
                continue
            break
        status = response.status_code
        if status == 401:
            raise AuthExpired('The server refused the user name or password.')
        if status == 404:
            raise NotFound(f'{method} {url}: not found')
        if status >= 400:
            raise SyncError(f'{method} {url}: HTTP {status} {response.reason}')
        return status, response.text, url

    def _propfind(self, connection, url, password=None):
        """{href: {'calendar': bool, 'principal': href, 'home': href}} of `url` (Depth 0)."""
        _status, text, final = self._dav(
            connection, 'PROPFIND', url, PROPFIND_BODY,
            {'Depth': '0', 'Content-Type': 'application/xml; charset=utf-8'}, password,
        )
        try:
            root = ElementTree.fromstring(text)
        except Exception:
            raise SyncError(f'{url} is not a CalDAV address (the answer was not WebDAV XML).')
        found = {'calendar': False, 'principal': '', 'home': '', 'url': final}
        for response in root.iter(f'{DAV}response'):
            for prop in response.iter(f'{DAV}prop'):
                resourcetype = prop.find(f'{DAV}resourcetype')
                if resourcetype is not None and resourcetype.find(f'{CAL}calendar') is not None:
                    found['calendar'] = True
                principal = prop.find(f'{DAV}current-user-principal/{DAV}href')
                if principal is not None and principal.text:
                    found['principal'] = urljoin(final, principal.text.strip())
                home = prop.find(f'{CAL}calendar-home-set/{DAV}href')
                if home is not None and home.text:
                    found['home'] = urljoin(final, home.text.strip())
        return found

    def _discover(self, connection, server, password=None):
        """
        The calendar URL for `server`: the server address itself when it is
        a calendar, else a new calendar in the user's calendar home.
        """
        url = server if server.endswith('/') else server + '/'
        parsed = urlparse(url)
        try:
            found = self._propfind(connection, url, password)
        except AuthExpired:
            raise
        except SyncError:
            if parsed.path not in ('', '/'):
                raise
            # A bare host: try the well-known address (RFC 6764).
            found = self._propfind(connection, f'{parsed.scheme}://{parsed.netloc}/.well-known/caldav', password)
        if found['calendar']:
            return found['url'], False
        home = found['home']
        if not home and found['principal']:
            home = self._propfind(connection, found['principal'], password)['home']
        if not home:
            raise SyncError(f'No calendars found at {server}. Give the address of a calendar instead.')
        calendar = urljoin(home if home.endswith('/') else home + '/', f'netbox-{secrets.token_hex(4)}/')
        try:
            self._dav(connection, 'MKCALENDAR', calendar, _mkcalendar_body(self.calendar_name()),
                      {'Content-Type': 'application/xml; charset=utf-8'}, password)
        except SyncError as error:
            raise SyncError(f'The server would not create a calendar ({error}). Make one yourself and '
                            'connect with its address.')
        return calendar, True

    def calendar_name(self):
        return (get_setting('sync_calendar_name') or '').strip() or 'NetBox'

    # Signing in

    def connect_password(self, connection, server, username, password):
        server = (self.fixed_server() or server or '').strip()
        if not server.startswith(('https://', 'http://')):
            raise SyncError('Give the server address, starting with https://.')
        if not username or not password:
            raise SyncError('Give the user name and the app password.')
        connection.server = server[:500]
        connection.account = username[:255]
        calendar, created = self._discover(connection, server, password)
        connection.calendar_id = calendar
        connection.refresh_token = tokens.encrypt(password)
        # A calendar the user picked themselves stays when they disconnect.
        connection.own_calendar = created

    # Pushing

    def prepare(self, connection):
        try:
            self._propfind(connection, connection.calendar_id)
            return False
        except NotFound:
            if not connection.own_calendar:
                raise SyncError('The calendar is gone from the server. Disconnect and connect again.')
        connection.calendar_id, _created = self._discover(connection, connection.server)
        connection.save(update_fields=('calendar_id',))
        return True

    def fingerprint(self, event):
        return digest(_without_stamp(ics(event)))

    def push(self, connection, event, synced):
        href = synced.remote_id or urljoin(connection.calendar_id, quote(f'netbox-{event.pk}.ics'))
        self._dav(connection, 'PUT', href, ics(event), {'Content-Type': 'text/calendar; charset=utf-8'})
        synced.remote_id = href

    def remove(self, connection, synced):
        if synced.remote_id:
            try:
                self._dav(connection, 'DELETE', synced.remote_id)
            except NotFound:
                pass
        synced.remote_id = ''

    def disconnect(self, connection, remove_calendar=True):
        # Only a calendar NetBox made is deleted whole; in one the user picked, only NetBox's events.
        if not remove_calendar:
            return
        if connection.own_calendar:
            try:
                self._dav(connection, 'DELETE', connection.calendar_id)
            except NotFound:
                pass
            return
        for synced in connection.synced.all():
            self.remove(connection, synced)
