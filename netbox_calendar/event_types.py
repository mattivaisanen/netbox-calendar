"""
The plugin's NetBox event types, one per notice, for Event Rule triggers
and bell notifications.

NetBox builds the Event Rule form's trigger list once, while the core
"extras" app starts, which is before any plugin's ready(). So these are
registered when the plugin package is imported (from __init__.py), and
this module must not import Django models.
"""
from django.utils.translation import gettext_lazy as _

from netbox.events import EventType
from netbox.registry import registry

from .choices import NoticeChoices

#: NetBox event type names, one per notice: (name, text, kind).
EVENT_TYPES = {
    NoticeChoices.SCHEDULED: ('netbox_calendar.scheduled', _('Event scheduled'), 'info'),
    NoticeChoices.CHANGED: ('netbox_calendar.changed', _('Event changed'), 'warning'),
    NoticeChoices.CANCELLED: ('netbox_calendar.cancelled', _('Event cancelled'), 'danger'),
    NoticeChoices.REMINDER: ('netbox_calendar.reminder', _('Event reminder'), 'info'),
    NoticeChoices.STARTED: ('netbox_calendar.started', _('Event started'), 'warning'),
    NoticeChoices.ENDED: ('netbox_calendar.ended', _('Event ended'), 'success'),
}


class TranslatedEventType(EventType):
    # NetBox prints an event with str(event); a lazy translation must be
    # made plain text first, or the notification menu fails to load.
    def __str__(self):
        return str(self.text)


def register():
    """Add the event types to NetBox's registry (once)."""
    for name, text, kind in EVENT_TYPES.values():
        if name not in registry['event_types']:
            TranslatedEventType(name, text, kind=kind).register()
