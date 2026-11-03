"""
What the plugin adds to other NetBox pages:

- an "Events" card on the page of any object with events (running and
  upcoming ones), with a button to schedule a new one for it;
- a banner on that page while an event on it is running;
- a "Change freeze" badge in the top bar on every page while a change
  freeze event runs;
- "Schedule event" on object lists, for the selected objects.
"""
from datetime import timedelta

from django.contrib.contenttypes.models import ContentType
from django.urls import reverse
from django.utils import timezone

from netbox.plugins import PluginTemplateExtension

from . import agenda
from .forms import linkable_types, target_field, type_label
from .lookup import events_for_object
from .models import Event
from .plugin_settings import on

# How far ahead the Events card looks, and how many it lists.
CARD_DAYS = 90
CARD_LIMIT = 8


def _memo(request, key, compute):
    """compute() once per request."""
    store = request.__dict__.setdefault('_netbox_calendar', {})
    if key not in store:
        store[key] = compute()
    return store[key]


def _linkable(model):
    return linkable_types().filter(pk=ContentType.objects.get_for_model(model).pk).exists()


def pick_param(content_type):
    """The add-event form's picker for included objects of this type."""
    return target_field(type_label(content_type))


def schedule_url(content_type, pks):
    """The add-event form with these objects ready to link."""
    query = '&'.join(f'{pick_param(content_type)}={pk}' for pk in pks)
    return f"{reverse('plugins:netbox_calendar:event_add')}?{query}"


class ObjectEvents(PluginTemplateExtension):
    """The Events card and the running-event banner, on any object's page."""
    models = None

    def _entries(self):
        obj = self.context.get('object')
        request = self.context['request']
        if obj is None or not getattr(obj, 'pk', None) or obj._meta.app_label == 'netbox_calendar':
            return None
        if not request.user.has_perm('netbox_calendar.view_event') or not _linkable(type(obj)):
            return None

        def compute():
            now = timezone.now()
            events = events_for_object(obj, Event.objects.restrict(request.user, 'view').exclude(status='cancelled'))
            return agenda.event_entries(events, now, now + timedelta(days=CARD_DAYS))

        return _memo(request, ('entries', obj._meta.label_lower, obj.pk), compute)

    def right_page(self):
        entries = self._entries()
        if entries is None:
            return ''
        request = self.context['request']
        obj = self.context['object']
        content_type = ContentType.objects.get_for_model(obj)
        can_add = request.user.has_perm('netbox_calendar.add_event')
        if not entries and not can_add:
            return ''
        entries = sorted(entries, key=lambda entry: entry.start)
        return self.render('netbox_calendar/inc/object_card.html', extra_context={
            'entries': entries[:CARD_LIMIT],
            'more': max(len(entries) - CARD_LIMIT, 0),
            'can_add': can_add,
            'add_url': schedule_url(content_type, [obj.pk]),
            'list_url': (
                f"{reverse('plugins:netbox_calendar:event_list')}"
                f"?object_type={content_type.app_label}.{content_type.model}&object_id={obj.pk}"
            ),
            'days': CARD_DAYS,
        })

    def alerts(self):
        entries = self._entries()
        running = [entry for entry in entries or () if entry.is_running]
        if not running:
            return ''
        return self.render('netbox_calendar/inc/object_alerts.html', extra_context={'running': running})


class FreezeBadge(PluginTemplateExtension):
    """The change freeze badge in the top bar, on every page."""
    models = None

    def navbar(self):
        request = self.context.get('request')
        if request is None or not request.user.is_authenticated or not on('freeze_banner'):
            return ''

        def compute():
            now = timezone.now()
            events = Event.objects.filter(event_type__change_freeze=True).exclude(status='cancelled')
            return agenda.event_entries(events, now, now + timedelta(seconds=1))

        running = _memo(request, 'freeze', compute)
        if not running:
            return ''
        return self.render('netbox_calendar/inc/freeze_badge.html', extra_context={'running': running})


class ScheduleSelected(PluginTemplateExtension):
    """ "Schedule event" above object lists, for the rows ticked in the table."""
    models = None

    def list_buttons(self):
        model = self.context.get('model')
        request = self.context.get('request')
        if model is None or request is None or model._meta.app_label == 'netbox_calendar':
            return ''
        if not request.user.has_perm('netbox_calendar.add_event') or not _linkable(model):
            return ''
        content_type = ContentType.objects.get_for_model(model)
        return self.render('netbox_calendar/inc/schedule_button.html', extra_context={
            'add_url': schedule_url(content_type, []),
            'pick_param': pick_param(content_type),
        })


template_extensions = (ObjectEvents, FreezeBadge, ScheduleSelected)
