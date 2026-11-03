import django_filters
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.utils.translation import gettext_lazy as _

from netbox.filtersets import NetBoxModelFilterSet
from users.models import Group

from .choices import EventStatusChoices, ImpactChoices
from .models import Event, EventRole, EventType, involving

User = get_user_model()


class EventTypeFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = EventType
        fields = ('id', 'name', 'slug', 'color', 'change_freeze', 'send_email', 'default_duration')

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(Q(name__icontains=value) | Q(description__icontains=value))


class EventRoleFilterSet(NetBoxModelFilterSet):
    class Meta:
        model = EventRole
        fields = ('id', 'name', 'slug', 'can_edit', 'notify', 'weight')

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(Q(name__icontains=value) | Q(description__icontains=value))


class EventFilterSet(NetBoxModelFilterSet):
    event_type_id = django_filters.ModelMultipleChoiceFilter(queryset=EventType.objects.all(), label=_('Type (ID)'))
    event_type = django_filters.ModelMultipleChoiceFilter(
        field_name='event_type__slug', queryset=EventType.objects.all(), to_field_name='slug', label=_('Type (slug)'),
    )
    status = django_filters.MultipleChoiceFilter(choices=EventStatusChoices)
    impact = django_filters.MultipleChoiceFilter(choices=ImpactChoices)
    owner_id = django_filters.ModelMultipleChoiceFilter(
        field_name='event_owner', queryset=User.objects.all(), label=_('Owner (ID)'),
    )
    person_id = django_filters.ModelMultipleChoiceFilter(
        field_name='people__user', queryset=User.objects.all(), label=_('Person (user ID)'),
    )
    group_id = django_filters.ModelMultipleChoiceFilter(
        field_name='people__group', queryset=Group.objects.all(), label=_('Person (group ID)'),
    )
    mine = django_filters.BooleanFilter(method='filter_mine', label=_('Owned by or including me'))
    recurring = django_filters.BooleanFilter(method='filter_recurring', label=_('Repeating'))
    # A repeating event counts while its series may still run.
    after = django_filters.IsoDateTimeFilter(method='filter_after', label=_('Ends after'))
    before = django_filters.IsoDateTimeFilter(method='filter_before', label=_('Starts before'))
    # Read together in filter_object(): object_type=dcim.device&object_id=12.
    object_type = django_filters.CharFilter(method='filter_object', label=_('Linked object type (app.model)'))
    object_id = django_filters.NumberFilter(method='filter_object_id', label=_('Linked object ID'))

    class Meta:
        model = Event
        fields = ('id', 'title', 'all_day', 'start', 'end', 'rrule')

    def search(self, queryset, name, value):
        if not value.strip():
            return queryset
        return queryset.filter(
            Q(title__icontains=value) | Q(description__icontains=value) | Q(comments__icontains=value)
            | Q(links__object_repr__icontains=value)
        ).distinct()

    def filter_mine(self, queryset, name, value):
        user = getattr(self.request, 'user', None)
        if not value or user is None or not user.is_authenticated:
            return queryset
        return queryset.filter(involving(user)).distinct()

    def filter_recurring(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.exclude(rrule='') if value else queryset.filter(rrule='')

    def filter_after(self, queryset, name, value):
        return queryset.filter(Q(end__gt=value) | ~Q(rrule=''))

    def filter_before(self, queryset, name, value):
        return queryset.filter(start__lt=value)

    def filter_object(self, queryset, name, value):
        object_id = self.data.get('object_id')
        if '.' not in value:
            return queryset.none()
        app_label, model = value.lower().split('.', 1)
        content_type = ContentType.objects.filter(app_label=app_label, model=model).first()
        if content_type is None:
            return queryset.none()
        # Excluded links don't count: the event is not about those objects.
        condition = Q(links__object_type=content_type, links__exclude=False)
        if object_id:
            condition &= Q(links__object_id=object_id)
        return queryset.filter(condition).distinct()

    def filter_object_id(self, queryset, name, value):
        # With object_type, filter_object() uses it.
        if self.data.get('object_type'):
            return queryset
        return queryset.filter(links__object_id=value, links__exclude=False).distinct()
