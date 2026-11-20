"""
What every calendar provider has: signing in with OAuth, and pushing and
removing one event. engine.py calls these; it never talks to a service
directly.
"""
import hashlib
import json


class SyncError(Exception):
    """A push failed; the message is shown on the connection."""


class AuthExpired(SyncError):
    """The user's sign-in no longer works; they have to connect again."""


class NotFound(SyncError):
    """The outside object is gone (deleted in the calendar app, say)."""


def digest(value):
    """A short, stable fingerprint of a JSON-able value."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


class Provider:
    #: Short name, stored on CalendarConnection.provider and used in URLs.
    name = ''
    #: Shown to users.
    label = ''
    #: Material Design icon name.
    icon = 'calendar-sync'

    def is_enabled(self):
        """Turned on in the plugin settings."""
        raise NotImplementedError

    def missing_settings(self):
        """Names of required settings that are empty. Empty list: ready to use."""
        raise NotImplementedError

    def is_ready(self):
        return self.is_enabled() and not self.missing_settings()

    # Signing in (OAuth 2.0 authorization code flow with PKCE).

    def authorize_url(self, redirect_uri, state, challenge, login_hint=''):
        """The address the user's browser is sent to, to sign in and consent."""
        raise NotImplementedError

    def finish(self, connection, code, redirect_uri, verifier):
        """Trade the code from the sign-in for tokens, stored on `connection` (not saved)."""
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
