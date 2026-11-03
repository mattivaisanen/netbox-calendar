"""
Optional integrations. Nothing here imports another plugin; this plugin
works the same without them.

netbox-change-approval: its scheduled change requests (approved, waiting
for their "Apply at" time) are shown on the month view and the timeline as
read-only entries. Events, event types and roles are NetBox objects, so
change-approval gates changes to them like any other object; see
docs/configuration.md for letting them through without approval.
"""
from datetime import timedelta

from django.apps import apps

CHANGE_APPROVAL = 'netbox_change_approval'

# How long a change request is drawn, from its "Apply at" time.
CHANGE_REQUEST_LENGTH = timedelta(minutes=30)


def has_change_approval():
    return apps.is_installed(CHANGE_APPROVAL)


def change_request_entries(user, after, before):
    """Calendar entries for change requests scheduled in [after, before) that `user` may view."""
    if not has_change_approval():
        return []
    from .agenda import Entry

    try:
        model = apps.get_model(CHANGE_APPROVAL, 'ChangeRequest')
        queryset = model.objects.filter(
            status='pending', scheduled_for__gte=after - CHANGE_REQUEST_LENGTH, scheduled_for__lt=before,
        )
        if hasattr(queryset, 'restrict'):
            queryset = queryset.restrict(user, 'view')
        found = []
        for change_request in queryset[:500]:
            start = change_request.scheduled_for
            found.append(Entry(
                title=str(change_request),
                start=start,
                end=start + CHANGE_REQUEST_LENGTH,
                url=change_request.get_absolute_url(),
                color='9e9e9e',
                kind='change_request',
                icon='check-decagram-outline',
                type_name='Change request',
            ))
        return found
    except Exception:
        # A newer or older change-approval without these fields: show nothing rather than fail.
        return []
