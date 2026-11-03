"""
Who may change an event.

NetBox's own object permissions always apply. With the restrict_editing
setting (on by default) a user who has the change or delete permission
may also only change events they own, or are on with a role that can
edit. Superusers, and users with the extra action "manage" on events
(Admin -> Permissions, Additional actions), may change every event.
"""
from django.db.models import Q

from .models import Event
from .plugin_settings import get_setting

MANAGE = 'netbox_calendar.manage_event'


def manages_all(user):
    """True when `user` may change every event they have permission for."""
    if not get_setting('restrict_editing', True):
        return True
    return user.is_superuser or user.has_perm(MANAGE)


def editable(queryset, user):
    """The events of `queryset` that `user` may change, under this plugin's rule."""
    if manages_all(user):
        return queryset
    if not user.is_authenticated:
        return queryset.none()
    return queryset.filter(
        Q(event_owner=user)
        | Q(people__user=user, people__role__can_edit=True)
        | Q(people__group__users=user, people__role__can_edit=True)
    ).distinct()


def can_edit(user, event):
    """True when `user` may change `event`: NetBox permission and this plugin's rule."""
    if not user.has_perm('netbox_calendar.change_event', event):
        return False
    return editable(Event.objects.filter(pk=event.pk), user).exists()
