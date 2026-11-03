"""
REST API: /api/plugins/calendar/. Changing events, their links and their
people follows the same rule as the web pages (permissions.py).
"""
from rest_framework.exceptions import PermissionDenied

from netbox.api.viewsets import NetBoxModelViewSet

from .. import filtersets
from ..models import Event, EventObject, EventPerson, EventRole, EventType
from ..permissions import can_edit, editable
from . import serializers

SAFE_METHODS = ('GET', 'HEAD', 'OPTIONS')


class EventTypeViewSet(NetBoxModelViewSet):
    queryset = EventType.objects.select_related('notify_group').prefetch_related('tags')
    serializer_class = serializers.EventTypeSerializer
    filterset_class = filtersets.EventTypeFilterSet


class EventRoleViewSet(NetBoxModelViewSet):
    queryset = EventRole.objects.prefetch_related('tags')
    serializer_class = serializers.EventRoleSerializer
    filterset_class = filtersets.EventRoleFilterSet


class EventViewSet(NetBoxModelViewSet):
    queryset = Event.objects.select_related('event_type', 'event_owner', 'filter_object_type').prefetch_related(
        'tags', 'links__object_type',
    )
    serializer_class = serializers.EventSerializer
    filterset_class = filtersets.EventFilterSet

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.method not in SAFE_METHODS:
            queryset = editable(queryset, self.request.user)
        return queryset

    def perform_create(self, serializer):
        # The creator owns a new event unless the request names an owner.
        if 'event_owner' not in serializer.validated_data and self.request.user.is_authenticated:
            serializer.validated_data['event_owner'] = self.request.user
        super().perform_create(serializer)


class EventPartViewSet(NetBoxModelViewSet):
    """Links and people: changing them needs permission to change their event."""

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.method not in SAFE_METHODS:
            queryset = queryset.filter(event__in=editable(Event.objects.all(), self.request.user))
        return queryset

    def _check(self, serializer):
        event = serializer.validated_data.get('event') or serializer.instance.event
        if not can_edit(self.request.user, event):
            raise PermissionDenied('You may not change this event.')

    def perform_create(self, serializer):
        self._check(serializer)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        self._check(serializer)
        super().perform_update(serializer)


class EventObjectViewSet(EventPartViewSet):
    queryset = EventObject.objects.select_related('event', 'object_type')
    serializer_class = serializers.EventObjectSerializer
    filterset_fields = ('event', 'object_type', 'object_id')


class EventPersonViewSet(EventPartViewSet):
    queryset = EventPerson.objects.select_related('event', 'role', 'user', 'group', 'contact')
    serializer_class = serializers.EventPersonSerializer
    filterset_fields = ('event', 'role', 'user', 'contact')
