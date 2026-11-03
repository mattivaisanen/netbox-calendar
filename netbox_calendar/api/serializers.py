from django.contrib.contenttypes.models import ContentType
from rest_framework import serializers

from core.models import ObjectType
from netbox.api.fields import ChoiceField, ContentTypeField
from netbox.api.gfk_fields import GFKSerializerField
from netbox.api.serializers import ChangeLogMessageSerializer, NetBoxModelSerializer, ValidatedModelSerializer
from tenancy.api.serializers import ContactSerializer
from users.api.serializers import GroupSerializer, UserSerializer

from ..choices import EventStatusChoices, ImpactChoices, NoticeChoices
from ..models import Event, EventObject, EventPerson, EventRole, EventType


class EventTypeSerializer(NetBoxModelSerializer):
    notify_on = serializers.ListField(child=serializers.ChoiceField(choices=NoticeChoices), required=False)
    notify_group = GroupSerializer(nested=True, required=False, allow_null=True)

    class Meta:
        model = EventType
        fields = (
            'id', 'url', 'display_url', 'display', 'name', 'slug', 'color', 'icon', 'description',
            'default_duration', 'change_freeze', 'notify_on', 'reminders', 'notify_owner', 'notify_people',
            'notify_group', 'notify_subscribers', 'notify_contacts', 'send_email', 'tags', 'custom_fields',
            'created', 'last_updated',
        )
        brief_fields = ('id', 'url', 'display', 'name', 'slug', 'color', 'description')


class EventRoleSerializer(NetBoxModelSerializer):
    class Meta:
        model = EventRole
        fields = (
            'id', 'url', 'display_url', 'display', 'name', 'slug', 'can_edit', 'notify', 'weight', 'description',
            'tags', 'custom_fields', 'created', 'last_updated',
        )
        brief_fields = ('id', 'url', 'display', 'name', 'slug', 'description')


class EventSerializer(NetBoxModelSerializer):
    event_type = EventTypeSerializer(nested=True)
    status = ChoiceField(choices=EventStatusChoices, required=False)
    impact = ChoiceField(choices=ImpactChoices, required=False)
    event_owner = UserSerializer(nested=True, required=False, allow_null=True)
    filter_object_type = ContentTypeField(queryset=ObjectType.objects.all(), required=False, allow_null=True)
    recurrence = serializers.SerializerMethodField(read_only=True)
    links = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Event
        fields = (
            'id', 'url', 'display_url', 'display', 'title', 'event_type', 'status', 'impact', 'start', 'end',
            'all_day', 'event_owner', 'description', 'comments', 'rrule', 'recurrence', 'skipped', 'reminders',
            'filter_object_type', 'filter_query', 'links', 'tags', 'custom_fields', 'created', 'last_updated',
        )
        brief_fields = ('id', 'url', 'display', 'title', 'start', 'end', 'status')

    def get_recurrence(self, obj):
        return obj.recurrence_text()

    def get_links(self, obj):
        return [
            {'id': link.pk, 'object_type': f'{link.object_type.app_label}.{link.object_type.model}',
             'object_id': link.object_id, 'object': link.object_repr}
            for link in obj.links.all()
        ]


class EventObjectSerializer(ChangeLogMessageSerializer, ValidatedModelSerializer):
    """An event's link to one NetBox object."""
    event = EventSerializer(nested=True)
    object_type = ContentTypeField(queryset=ObjectType.objects.all())
    object = GFKSerializerField(read_only=True)

    class Meta:
        model = EventObject
        fields = ('id', 'url', 'display', 'event', 'object_type', 'object_id', 'object', 'object_repr', 'created',
                  'last_updated')
        read_only_fields = ('object_repr',)
        brief_fields = ('id', 'url', 'display', 'event', 'object_type', 'object_id')

    def validate(self, data):
        content_type = data.get('object_type') or getattr(self.instance, 'object_type', None)
        object_id = data.get('object_id') or getattr(self.instance, 'object_id', None)
        if content_type is not None:
            model = ContentType.objects.get_for_id(content_type.pk).model_class()
            if model is None or not model.objects.filter(pk=object_id).exists():
                raise serializers.ValidationError({'object_id': 'No such object.'})
        return super().validate(data)


class EventPersonSerializer(ChangeLogMessageSerializer, ValidatedModelSerializer):
    """A user, user group or contact on an event, with a role."""
    event = EventSerializer(nested=True)
    role = EventRoleSerializer(nested=True)
    user = UserSerializer(nested=True, required=False, allow_null=True)
    group = GroupSerializer(nested=True, required=False, allow_null=True)
    contact = ContactSerializer(nested=True, required=False, allow_null=True)

    class Meta:
        model = EventPerson
        fields = ('id', 'url', 'display', 'event', 'role', 'user', 'group', 'contact', 'created', 'last_updated')
        brief_fields = ('id', 'url', 'display', 'event', 'role', 'user', 'group', 'contact')
