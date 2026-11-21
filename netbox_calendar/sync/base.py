"""
What every calendar provider has: signing in with OAuth, and pushing and
removing one event. engine.py calls these; it never talks to a service
directly.
"""
import base64
import contextvars
import hashlib
import json

import requests
from django.conf import settings

from ..ical import absolute_url
from ..lookup import describe


class SyncError(Exception):
    """A push failed; the message is shown on the connection."""


class AuthExpired(SyncError):
    """The user's sign-in no longer works; they have to connect again."""


class NotFound(SyncError):
    """The outside object is gone (deleted in the calendar app, say)."""


def digest(value):
    """A short, stable fingerprint of a JSON-able value."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def id_token_claims(id_token):
    """
    The claims of the ID token that came with the tokens. It came straight
    from the provider's token endpoint over HTTPS, so its signature needn't be
    checked to read the account name (OpenID Connect Core 3.1.3.7).
    """
    try:
        part = id_token.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
    except (IndexError, ValueError):
        return {}


#: The user whose calendar is being synced (set by engine._sync()). Calendar
#: entries name only the objects that user may view.
viewer = contextvars.ContextVar('netbox_calendar_sync_viewer', default=None)


def concerns_text(event):
    """What the event concerns, in words, as the user being synced may see it."""
    return describe(event, user=viewer.get())


def event_text(event):
    """(location, description) every provider shows for an event: what it concerns, and the details."""
    objects_text = concerns_text(event)
    link = absolute_url(event.get_absolute_url())
    text = '\n\n'.join(part for part in (
        event.description,
        f'Concerns: {objects_text}' if objects_text else '',
        f'Owner: {event.owner_name}' if event.event_owner_id else '',
        f'Impact: {event.get_impact_display()}',
        event.comments,
        link,
    ) if part)
    return objects_text, text


class Provider:
    #: Short name, stored on CalendarConnection.provider and used in URLs.
    name = ''
    #: Shown to users.
    label = ''
    #: Shown on the card and buttons ("Connect Outlook Calendar").
    title = ''
    #: Material Design icon name.
    icon = 'calendar-sync'
    #: True: the user signs in on the provider's page (OAuth). False: the
    #: user types a server, user name and password (connect_password()).
    uses_oauth = True

    def is_enabled(self):
        """Turned on in the plugin settings."""
        raise NotImplementedError

    def missing_settings(self):
        """Names of required settings that are empty. Empty list: ready to use."""
        raise NotImplementedError

    def is_ready(self):
        return self.is_enabled() and not self.missing_settings()

    #: Seconds to wait for the service before giving up on a request.
    timeout = 20

    def _http(self, method, url, **kwargs):
        """
        One request to the service: (status, parsed JSON or {}, headers).
        Uses NetBox's HTTP_PROXIES. The only method that touches the
        network; tests replace it.
        """
        try:
            response = requests.request(
                method, url, timeout=self.timeout, proxies=getattr(settings, 'HTTP_PROXIES', None), **kwargs,
            )
        except requests.RequestException as error:
            # Unreachable, DNS, TLS or timeout: kept on the connection like any other failure.
            raise SyncError(f'Could not reach {url.split("?")[0]}: {type(error).__name__}')
        try:
            body = response.json() if response.content else {}
        except ValueError:
            body = {}
        return response.status_code, body, response.headers

    # Signing in (OAuth 2.0 authorization code flow with PKCE).

    def authorize_url(self, redirect_uri, state, challenge, login_hint=''):
        """The address the user's browser is sent to, to sign in and consent."""
        raise NotImplementedError

    def finish(self, connection, code, redirect_uri, verifier):
        """Trade the code from the sign-in for tokens, stored on `connection` (not saved)."""
        raise NotImplementedError

    # Signing in with a password (CalDAV).

    def fixed_server(self):
        """A server address set by the admin, so users don't type one. Empty: users type it."""
        return ''

    def connect_password(self, connection, server, username, password):
        """Check the login and store it on `connection` (not saved)."""
        raise NotImplementedError

    # Pushing.

    def prepare(self, connection):
        """
        Get ready to push: a fresh access token, and the calendar that holds
        NetBox's events. Returns True when that calendar was made anew, so
        everything has to be pushed again.
        """
        raise NotImplementedError

    def fingerprint(self, event):
        """Changes whenever what would be pushed for `event` changes."""
        raise NotImplementedError

    def push(self, connection, event, synced):
        """Create or update `event` in the calendar; updates `synced` (not saved)."""
        raise NotImplementedError

    def remove(self, connection, synced):
        """Delete the outside copy of a synced event. Already gone is fine."""
        raise NotImplementedError

    def disconnect(self, connection, remove_calendar=True):
        """Forget the connection on the provider's side, optionally deleting NetBox's calendar."""
        raise NotImplementedError
