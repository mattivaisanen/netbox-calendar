import django_tables2 as tables
from django.utils.translation import gettext_lazy as _

from netbox.tables import NetBoxTable, columns

from .models import Event, EventRole, EventType


class EventTypeTable(NetBoxTable):
    name = tables.Column(linkify=True)
    color = columns.ColorColumn()
    change_freeze = columns.BooleanColumn()
    send_email = columns.BooleanColumn()
    notify_group = tables.Column(linkify=True)
    events = columns.LinkedCountColumn(
        viewname='plugins:netbox_calendar:event_list', url_params={'event_type_id': 'pk'}, verbose_name=_('Events'),
    )
    tags = columns.TagColumn(url_name='plugins:netbox_calendar:eventtype_list')

    class Meta(NetBoxTable.Meta):
        model = EventType
        fields = (
            'pk', 'id', 'name', 'slug', 'color', 'icon', 'description', 'default_duration', 'change_freeze',
            'notify_on', 'reminders', 'notify_group', 'send_email', 'events', 'tags', 'created', 'last_updated',
        )
        default_columns = ('pk', 'name', 'color', 'default_duration', 'change_freeze', 'send_email', 'events')


class EventRoleTable(NetBoxTable):
    name = tables.Column(linkify=True)
    can_edit = columns.BooleanColumn()
    notify = columns.BooleanColumn()
    tags = columns.TagColumn(url_name='plugins:netbox_calendar:eventrole_list')

    class Meta(NetBoxTable.Meta):
        model = EventRole
        fields = ('pk', 'id', 'name', 'slug', 'can_edit', 'notify', 'weight', 'description', 'tags', 'created',
                  'last_updated')
        default_columns = ('pk', 'name', 'can_edit', 'notify', 'weight', 'description')


class EventTable(NetBoxTable):
    title = tables.Column(linkify=True)
    event_type = columns.ColoredLabelColumn(verbose_name=_('Type'))
    status = columns.ChoiceFieldColumn()
    impact = columns.ChoiceFieldColumn()
    all_day = columns.BooleanColumn()
    event_owner = tables.Column()
    recurrence = tables.Column(accessor='rrule', verbose_name=_('Repeats'), orderable=False)
    objects = tables.TemplateColumn(
        template_code='{% for link in record.links.all|slice:":3" %}{{ link.object_repr }}{% if not forloop.last %}, '
                      '{% endif %}{% endfor %}{% if record.links.all|length > 3 %} …{% endif %}',
        verbose_name=_('Objects'), orderable=False,
    )
    tags = columns.TagColumn(url_name='plugins:netbox_calendar:event_list')

    class Meta(NetBoxTable.Meta):
        model = Event
        fields = (
            'pk', 'id', 'title', 'event_type', 'status', 'impact', 'start', 'end', 'all_day', 'event_owner',
            'recurrence', 'objects', 'description', 'tags', 'created', 'last_updated',
        )
        default_columns = (
            'pk', 'title', 'event_type', 'status', 'impact', 'start', 'end', 'event_owner', 'recurrence', 'objects',
        )

    def render_event_owner(self, value, record):
        # Full name, not the username.
        return record.owner_name

    def render_recurrence(self, value, record):
        return record.recurrence_text()
