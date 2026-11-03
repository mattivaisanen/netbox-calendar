"""
The "Upcoming Events" dashboard widget: the next events, optionally only
some types or only your own.
"""
from datetime import timedelta

from django import forms
from django.db.models import Q
from django.template.loader import render_to_string
# Not gettext_lazy: DashboardWidget.__str__ returns the title as is, and
# Python needs a real str there (NetBox's own widgets do the same).
from django.utils.translation import gettext as _
from django.utils import timezone

from extras.dashboard.utils import register_widget
from extras.dashboard.widgets import DashboardWidget, WidgetConfigForm

from . import agenda
from .models import Event, EventType


def _type_choices():
    return [(event_type.slug, event_type.name) for event_type in EventType.objects.order_by('name')]


@register_widget
class UpcomingEventsWidget(DashboardWidget):
    default_title = _('Upcoming Events')
    description = _('Running and upcoming scheduled events.')
    template_name = 'netbox_calendar/inc/widget.html'
    width = 4
    height = 4
    default_config = {'days': 14, 'limit': 10, 'event_types': [], 'only_mine': False}

    class ConfigForm(WidgetConfigForm):
        days = forms.IntegerField(min_value=1, max_value=366, initial=14, label=_('Days ahead'))
        limit = forms.IntegerField(min_value=1, max_value=50, initial=10, label=_('At most'))
        event_types = forms.MultipleChoiceField(
            choices=_type_choices, required=False, label=_('Event types'), help_text=_('None picked: all.'),
        )
        only_mine = forms.BooleanField(required=False, label=_('Only mine'),
                                       help_text=_('Events you own or are on.'))

    def render(self, request):
        if not request.user.has_perm('netbox_calendar.view_event'):
            return render_to_string(self.template_name, {'denied': True})
        config = {**self.default_config, **(self.config or {})}
        events = Event.objects.restrict(request.user, 'view').exclude(status='cancelled')
        if config.get('event_types'):
            events = events.filter(event_type__slug__in=config['event_types'])
        if config.get('only_mine'):
            events = events.filter(Q(event_owner=request.user) | Q(people__user=request.user)).distinct()
        now = timezone.now()
        entries = agenda.event_entries(events, now, now + timedelta(days=int(config.get('days') or 14)))
        entries.sort(key=lambda entry: entry.start)
        limit = int(config.get('limit') or 10)
        return render_to_string(self.template_name, {
            'entries': entries[:limit],
            'more': max(len(entries) - limit, 0),
            'days': config.get('days'),
        }, request=request)
