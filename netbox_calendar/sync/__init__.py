"""
Calendar sync: each user can connect an outside calendar (Outlook today),
and NetBox pushes the events they care about into it, one way.

- providers: one class per calendar service (outlook.py), registered here.
  A provider signs the user in, keeps their tokens fresh and turns an
  event into that service's format.
- engine.py: works out which events belong in a user's calendar and
  sends only what changed since the last sync (SyncedEvent fingerprints).
- tokens.py: encrypts the tokens stored in the database.

Pushes run in NetBox's background worker: one job after every saved
change (signals.py) and an hourly job that catches anything missed
(jobs.py).
"""
from .outlook import OutlookProvider

#: Every provider the plugin knows, by name.
PROVIDERS = {provider.name: provider for provider in (OutlookProvider(),)}


def get_provider(name):
    """The provider called `name`, or None."""
    return PROVIDERS.get(name)


def enabled_providers():
    """Providers turned on in the plugin settings, configured or not."""
    return [provider for provider in PROVIDERS.values() if provider.is_enabled()]


def any_enabled():
    return bool(enabled_providers())
