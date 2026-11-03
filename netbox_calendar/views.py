import secrets
from datetime import date, datetime, timedelta

from django.contrib import messages
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.db.models import Q
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.text import capfirst
from django.utils.translation import gettext as _
from django.views.generic import View

from dcim.models import Device
from extras.forms import JournalEntryForm
from extras.models import JournalEntry
from netbox.views import generic
from utilities.htmx import htmx_partial
from utilities.views import ConditionalLoginRequiredMixin, register_model_view

from . import agenda, filtersets, forms, ical, plugin_settings, recurrence, tables
from .lookup import DEVICE_MATCHES, Targets, affected_devices, describe, filtered_objects
from .models import Event, EventObject, EventPerson, EventRole, EventType, FeedToken, PluginSetting
from .permissions import can_edit, editable


#
# Event types
#

@register_model_view(EventType, 'list', path='', detail=False)
class EventTypeListView(generic.ObjectListView):
    queryset = EventType.objects.select_related('notify_group')
    filterset = filtersets.EventTypeFilterSet
    filterset_form = forms.EventTypeFilterForm
    table = tables.EventTypeTable


@register_model_view(EventType)
class EventTypeView(generic.ObjectView):
    queryset = EventType.objects.all()

    def get_extra_context(self, request, instance):
        return {'reminders_text': forms.describe_minutes(instance.reminders)}


@register_model_view(EventType, 'add', detail=False)
@register_model_view(EventType, 'edit')
class EventTypeEditView(generic.ObjectEditView):
    queryset = EventType.objects.all()
    form = forms.EventTypeForm


@register_model_view(EventType, 'delete')
class EventTypeDeleteView(generic.ObjectDeleteView):
    queryset = EventType.objects.all()


@register_model_view(EventType, 'bulk_import', path='import', detail=False)
class EventTypeBulkImportView(generic.BulkImportView):
    queryset = EventType.objects.all()
    model_form = forms.EventTypeImportForm


@register_model_view(EventType, 'bulk_edit', path='edit', detail=False)
class EventTypeBulkEditView(generic.BulkEditView):
    queryset = EventType.objects.all()
    filterset = filtersets.EventTypeFilterSet
    table = tables.EventTypeTable
    form = forms.EventTypeBulkEditForm


@register_model_view(EventType, 'bulk_delete', path='delete', detail=False)
class EventTypeBulkDeleteView(generic.BulkDeleteView):
    queryset = EventType.objects.all()
    filterset = filtersets.EventTypeFilterSet
    table = tables.EventTypeTable


#
# Event roles
#

@register_model_view(EventRole, 'list', path='', detail=False)
class EventRoleListView(generic.ObjectListView):
    queryset = EventRole.objects.all()
    filterset = filtersets.EventRoleFilterSet
    filterset_form = forms.EventRoleFilterForm
    table = tables.EventRoleTable


@register_model_view(EventRole)
class EventRoleView(generic.ObjectView):
    queryset = EventRole.objects.all()


@register_model_view(EventRole, 'add', detail=False)
@register_model_view(EventRole, 'edit')
class EventRoleEditView(generic.ObjectEditView):
    queryset = EventRole.objects.all()
    form = forms.EventRoleForm


@register_model_view(EventRole, 'delete')
class EventRoleDeleteView(generic.ObjectDeleteView):
    queryset = EventRole.objects.all()


@register_model_view(EventRole, 'bulk_import', path='import', detail=False)
class EventRoleBulkImportView(generic.BulkImportView):
    queryset = EventRole.objects.all()
    model_form = forms.EventRoleImportForm


@register_model_view(EventRole, 'bulk_edit', path='edit', detail=False)
class EventRoleBulkEditView(generic.BulkEditView):
    queryset = EventRole.objects.all()
    filterset = filtersets.EventRoleFilterSet
    table = tables.EventRoleTable
    form = forms.EventRoleBulkEditForm


@register_model_view(EventRole, 'bulk_delete', path='delete', detail=False)
class EventRoleBulkDeleteView(generic.BulkDeleteView):
    queryset = EventRole.objects.all()
    filterset = filtersets.EventRoleFilterSet
    table = tables.EventRoleTable


#
# Events
#

EVENT_QUERYSET = Event.objects.select_related('event_type', 'event_owner').prefetch_related(
    'links', 'tags', 'occurrence_changes',
)


@register_model_view(Event, 'list', path='', detail=False)
class EventListView(generic.ObjectListView):
    queryset = EVENT_QUERYSET
    filterset = filtersets.EventFilterSet
    filterset_form = forms.EventFilterForm
    table = tables.EventTable


def _same_targets(event, other, devices):
    """Whether two events concern a same object: one picked by both, or a device both affect."""
    keys = set(event.links.filter(exclude=False).values_list('object_type_id', 'object_id'))
    shared = other.links.filter(exclude=False).values_list('object_type_id', 'object_id', 'object_type__app_label',
                                                           'object_type__model')
    for type_id, object_id, app_label, model in shared:
        # A shared site or role alone is no clash: their devices are compared below.
        if (type_id, object_id) in keys and f'{app_label}.{model}' not in DEVICE_MATCHES:
            return True
    return devices.filter(pk__in=affected_devices(other).values('pk')).exists()


def conflicts(event, user):
    """
    [(other event, occurrence)] overlapping this event's next occurrence
    on a shared object: one both pick, or a device both affect.
    """
    if not plugin_settings.on('conflict_warnings'):
        return []
    occurrence = event.next_occurrence()
    if occurrence is None:
        return []
    devices = affected_devices(event)
    # Candidates: events linking something this one links, or one of its devices.
    keys = list(event.links.filter(exclude=False).values_list('object_type_id', 'object_id'))
    device_type = ContentType.objects.get_for_model(Device)
    keys += [(device_type.pk, pk) for pk in devices.values_list('pk', flat=True)[:DEVICES_SHOWN]]
    if not keys:
        return []

    condition = Q()
    for type_id, object_id in keys:
        condition |= Q(links__object_type_id=type_id, links__object_id=object_id, links__exclude=False)
    others = Event.objects.restrict(user, 'view').exclude(pk=event.pk).exclude(status='cancelled').filter(condition)
    found = []
    for other in others.distinct().select_related('event_type'):
        overlapping = other.occurrences(occurrence.start, occurrence.end)
        if overlapping and _same_targets(event, other, devices):
            found.append((other, overlapping[0]))
    return found


def target_groups(event):
    """The event's links by kind for the Objects card: criteria, picked ones, then excluded ones."""
    groups = {}
    for link in event.links.select_related('object_type'):
        label = forms.type_label(link.object_type)
        model = link.object_type.model_class()
        name = model._meta.verbose_name_plural if model else link.object_type.name
        group = groups.setdefault((link.exclude, label), {
            'name': _('Except {types}').format(types=name) if link.exclude else name[:1].upper() + name[1:],
            'exclude': link.exclude,
            'criterion': label in DEVICE_MATCHES and not link.exclude,
            'links': [],
        })
        group['links'].append(link)
    order = sorted(groups, key=lambda key: (key[0], key[1] not in DEVICE_MATCHES, forms._type_order(key[1])))
    return [groups[key] for key in order]


#: How many affected devices the event page lists.
DEVICES_SHOWN = 100

#: How many of the newest journal entries the event page lists; the Journal tab has them all.
JOURNAL_SHOWN = 5


@register_model_view(Event)
class EventView(generic.ObjectView):
    queryset = EVENT_QUERYSET

    def get_extra_context(self, request, instance):
        targets = Targets(instance)
        devices = affected_devices(instance, targets).restrict(request.user, 'view')
        devices_total = devices.count()
        now = timezone.now()
        upcoming = instance.occurrences(now, now + timedelta(days=3660))[:10] if instance.rrule else []
        return {
            'target_groups': target_groups(instance),
            'targets_text': describe(instance, targets),
            'devices': devices.select_related('site', 'role', 'device_type').order_by('name')[:DEVICES_SHOWN],
            'devices_total': devices_total,
            'devices_more': max(devices_total - DEVICES_SHOWN, 0),
            'filter_count': (filtered_objects(instance).count() if instance.filter_object_type_id else None),
            'people': instance.people.select_related('role', 'user', 'group', 'contact'),
            'upcoming': upcoming,
            'next_occurrence': instance.next_occurrence(now),
            'occurrence_changes': [
                (change, recurrence.is_occurrence(instance, change.original_start))
                for change in instance.occurrence_changes.all()
            ],
            'reminders_text': forms.describe_minutes(instance.effective_reminders()),
            'reminders_own': instance.reminders is not None,
            'conflicts': conflicts(instance, request.user),
            'can_edit': can_edit(request.user, instance),
            'last_day': instance.last_day,
            **journal_context(request, instance),
        }


def journal_context(request, event):
    """The event's newest journal entries, and a form to add one, as the user may see and add them."""
    context = {}
    if request.user.has_perm('extras.view_journalentry'):
        entries = JournalEntry.objects.restrict(request.user, 'view').filter(
            assigned_object_type=ContentType.objects.get_for_model(Event),
            assigned_object_id=event.pk,
        )
        context['journal'] = entries.select_related('created_by').order_by('-created')[:JOURNAL_SHOWN]
        context['journal_total'] = entries.count()
    if request.user.has_perm('extras.add_journalentry'):
        context['journal_form'] = JournalEntryForm()
    return context


@register_model_view(Event, 'add', detail=False)
@register_model_view(Event, 'edit')
class EventEditView(generic.ObjectEditView):
    queryset = Event.objects.all()
    form = forms.EventForm
    # The objects section is drawn as three boxes (inc/targets.html).
    template_name = 'netbox_calendar/event_edit.html'
    htmx_template_name = 'netbox_calendar/inc/event_form.html'

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        # Only an existing event is subject to who may edit it.
        if getattr(self, '_permission_action', None) == 'change':
            return editable(queryset, request.user)
        return queryset

    def alter_object(self, obj, request, url_args, url_kwargs):
        # The form hides user pickers from those who may not view users.
        obj._calendar_editor = request.user
        if not obj.pk and obj.event_owner_id is None and request.user.is_authenticated:
            obj.event_owner = request.user
        return obj


@register_model_view(Event, 'delete')
class EventDeleteView(generic.ObjectDeleteView):
    queryset = Event.objects.all()

    def get_queryset(self, request):
        return editable(super().get_queryset(request), request.user)


@register_model_view(Event, 'bulk_import', path='import', detail=False)
class EventBulkImportView(generic.BulkImportView):
    queryset = Event.objects.all()
    model_form = forms.EventImportForm


@register_model_view(Event, 'bulk_edit', path='edit', detail=False)
class EventBulkEditView(generic.BulkEditView):
    queryset = Event.objects.all()
    filterset = filtersets.EventFilterSet
    table = tables.EventTable
    form = forms.EventBulkEditForm

    def get_queryset(self, request):
        return editable(super().get_queryset(request), request.user)


@register_model_view(Event, 'bulk_delete', path='delete', detail=False)
class EventBulkDeleteView(generic.BulkDeleteView):
    queryset = Event.objects.all()
    filterset = filtersets.EventFilterSet
    table = tables.EventTable

    def get_queryset(self, request):
        return editable(super().get_queryset(request), request.user)


class EventChangeMixin(ConditionalLoginRequiredMixin):
    """For the event page's own actions: the event, if the user may change it."""

    def get_event(self, request, pk):
        event = get_object_or_404(Event.objects.restrict(request.user, 'view'), pk=pk)
        if not can_edit(request.user, event):
            raise PermissionDenied(_('You may not change this event.'))
        return event


def _names(objects):
    """ "a", "a or b", "a, b or c"."""
    names = [str(obj) for obj in objects]
    return names[0] if len(names) == 1 else _('{names} or {last}').format(names=', '.join(names[:-1]), last=names[-1])


def _phrase(objects):
    """ "site Tampere 1 or Tampere 2" for objects of one type."""
    return f'{objects[0]._meta.verbose_name} {_names(objects)}'


def _by_type(objects):
    """[[objects of one type], ...] in the order the types first appear."""
    groups = {}
    for obj in objects:
        groups.setdefault(type(obj), []).append(obj)
    return list(groups.values())


class EventTargetRowView(ConditionalLoginRequiredMixin, View):
    """
    One row of the event form's objects section (HTMX): a new, empty row,
    or a row redrawn after its mode or type changed. Objects already picked
    stay when only the mode changes.
    """
    def get(self, request):
        if not request.user.has_perm('netbox_calendar.view_event'):
            raise PermissionDenied
        exclude = request.GET.get('target_mode') == forms.EXCLUDE
        label = request.GET.get('target_type', '')
        values = []
        for key in request.GET:
            parsed = forms.parse_target_field(key)
            if parsed and parsed[0] == label:
                values += [pk for pk in request.GET.getlist(key) if pk.isdigit()]
        form = forms.TargetRowForm(exclude=exclude, label=label, values=values)
        return render(request, 'netbox_calendar/inc/target_row.html', {
            'exclude': form.exclude, 'label': form.label, 'picker': form.picker, 'type_groups': form.type_groups,
        })


class EventTargetsPreviewView(ConditionalLoginRequiredMixin, View):
    """
    The event form's Result (HTMX): reads the objects rows and the filter
    from the address, as the form would save them, and says in plain words
    what the event covers and how many devices that is.
    """
    template_name = 'netbox_calendar/inc/target_summary.html'
    LIMIT = 10

    def get(self, request):
        if not request.user.has_perm('netbox_calendar.view_event'):
            raise PermissionDenied
        types = {forms.type_label(content_type): content_type for content_type in forms.linkable_types()}
        objects = []
        for key in request.GET:
            parsed = forms.parse_target_field(key)
            if not parsed or parsed[0] not in types:
                continue
            label, exclude = parsed
            pks = [pk for pk in request.GET.getlist(key) if pk.isdigit()]
            model = types[label].model_class()
            objects += [(obj, exclude) for obj in model.objects.restrict(request.user, 'view').filter(pk__in=pks)]

        # An event not saved, with the filter as typed, so the rule reads as on save.
        event = Event()
        filter_type = request.GET.get('filter_object_type')
        if filter_type and filter_type.isdigit() and ContentType.objects.filter(pk=filter_type).exists():
            event.filter_object_type_id = int(filter_type)
            event.filter_query = request.GET.get('filter_query', '')
        targets = Targets(event, objects)

        # The rule as lines: what is in, then what is left out.
        included = []
        criteria = [_phrase(objs) for objs in targets.criteria.values()]
        if targets.filter_is_criterion:
            criteria.append(_('matching the filter {filter}').format(filter=event.filter_query))
        if criteria:
            included.append(_('Devices and VMs with {criteria}').format(criteria=_(' and ').join(criteria)))
        included += [capfirst(_phrase(objs)) for objs in _by_type(targets.picked)]
        if event.filter_object_type_id and not targets.filter_is_criterion and targets.filter_model:
            included.append(_('{types} matching the filter {filter}').format(
                types=targets.filter_model._meta.verbose_name_plural.capitalize(), filter=event.filter_query,
            ))
        excluded = []
        for objs in _by_type(targets.excluded):
            if objs[0]._meta.label_lower in DEVICE_MATCHES:
                excluded.append(_('Devices and VMs with {criterion}').format(criterion=_phrase(objs)))
            else:
                excluded.append(capfirst(_phrase(objs)))

        devices = affected_devices(event, targets).restrict(request.user, 'view').order_by('name')
        device_count = devices.count()
        others = [obj for obj in targets.picked if not isinstance(obj, Device)]
        if event.filter_object_type_id and not targets.filter_is_criterion and targets.filtered is not None:
            others_count = len(others) + targets.filtered.count()
        else:
            others_count = len(others)
        return render(request, self.template_name, {
            'included': included,
            'excluded': excluded,
            'device_count': device_count,
            'devices': devices[:self.LIMIT],
            'more_devices': max(device_count - self.LIMIT, 0),
            'other_count': others_count,
        })


class EventLinkView(EventChangeMixin, View):
    """Link objects of one type to an event."""
    template_name = 'netbox_calendar/event_link.html'

    def get(self, request, pk):
        event = self.get_event(request, pk)
        form = forms.LinkObjectsForm(initial=request.GET.dict())
        if htmx_partial(request):
            return render(request, 'htmx/form.html', {'form': form})
        return render(request, self.template_name, {'object': event, 'form': form})

    def post(self, request, pk):
        event = self.get_event(request, pk)
        form = forms.LinkObjectsForm(data=request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {'object': event, 'form': form})
        form.save_links(event)
        messages.success(request, _('Linked {count} object(s).').format(count=len(form.cleaned_data['link_objects'])))
        return redirect(event.get_absolute_url())


class EventUnlinkView(EventChangeMixin, View):
    """Remove one link: GET asks to confirm, POST removes it."""
    template_name = 'netbox_calendar/event_unlink.html'

    def get(self, request, pk, link_pk):
        event = self.get_event(request, pk)
        link = get_object_or_404(EventObject, pk=link_pk, event=event)
        return render(request, self.template_name, {'object': event, 'link': link})

    def post(self, request, pk, link_pk):
        event = self.get_event(request, pk)
        link = get_object_or_404(EventObject, pk=link_pk, event=event)
        link.snapshot()
        link.delete()
        messages.success(request, _('Removed {object}.').format(object=link.object_repr))
        return redirect(event.get_absolute_url())


class EventPersonView(EventChangeMixin, View):
    """Add a person to an event, or change their role."""
    template_name = 'netbox_calendar/event_person.html'

    def get_person(self, event, person_pk):
        if person_pk is None:
            return EventPerson(event=event)
        return get_object_or_404(EventPerson, pk=person_pk, event=event)

    def get(self, request, pk, person_pk=None):
        event = self.get_event(request, pk)
        form = forms.EventPersonForm(instance=self.get_person(event, person_pk))
        return render(request, self.template_name, {'object': event, 'form': form})

    def post(self, request, pk, person_pk=None):
        event = self.get_event(request, pk)
        person = self.get_person(event, person_pk)
        if person.pk:
            person.snapshot()
        form = forms.EventPersonForm(data=request.POST, instance=person)
        if not form.is_valid():
            return render(request, self.template_name, {'object': event, 'form': form})
        form.save()
        return redirect(event.get_absolute_url())


class EventPersonDeleteView(EventChangeMixin, View):
    """
    Remove a person from an event. GET asks first, in NetBox's pop-up
    (the Remove button on the event page loads it); POST removes.
    """

    def get(self, request, pk, person_pk):
        event = self.get_event(request, pk)
        person = get_object_or_404(EventPerson.objects.select_related('user', 'group', 'contact'), pk=person_pk,
                                   event=event)
        if not htmx_partial(request):
            return redirect(event.get_absolute_url())
        return render(request, 'netbox_calendar/inc/confirm_remove.html', {
            'title': _('Remove person'),
            'question': _('Remove {person} ({role}) from {event}?').format(
                person=person.person_name, role=person.role, event=event,
            ),
            'form_url': request.path,
        })

    def post(self, request, pk, person_pk):
        event = self.get_event(request, pk)
        person = get_object_or_404(EventPerson, pk=person_pk, event=event)
        person.snapshot()
        person.delete()
        return redirect(event.get_absolute_url())


class EventOccurrenceView(EventChangeMixin, View):
    """
    Skip, move or bring back one occurrence of a repeating event, always
    with a reason. The occurrence is named by its original start
    (?occurrence=<ISO time>).
    """
    template_name = 'netbox_calendar/event_occurrence.html'

    def get_original_start(self, event, data):
        try:
            moment = datetime.fromisoformat(data.get('occurrence', ''))
        except ValueError:
            raise Http404
        if timezone.is_naive(moment):
            moment = timezone.make_aware(moment)
        # A change left behind by an edited rule can still be brought back.
        if not recurrence.is_occurrence(event, moment) and \
                not event.occurrence_changes.filter(original_start=moment).exists():
            raise Http404
        return moment

    def get(self, request, pk):
        event = self.get_event(request, pk)
        original_start = self.get_original_start(event, request.GET)
        form = forms.OccurrenceForm(event, original_start, initial=request.GET.dict())
        if htmx_partial(request):
            # The action picker re-renders the form with the fields that action needs.
            return render(request, 'htmx/form.html', {'form': form})
        return render(request, self.template_name, {'object': event, 'form': form, 'original_start': original_start})

    def post(self, request, pk):
        event = self.get_event(request, pk)
        original_start = self.get_original_start(event, request.POST)
        form = forms.OccurrenceForm(event, original_start, data=request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {
                'object': event, 'form': form, 'original_start': original_start,
            })
        action = form.save()
        message = {
            form.SKIP: _('Skipped the occurrence of {when}.'),
            form.MOVE: _('Moved the occurrence of {when}.'),
            form.RESTORE: _('Brought back the occurrence of {when}.'),
        }[action]
        messages.success(request, message.format(when=timezone.localtime(original_start).strftime('%Y-%m-%d %H:%M')))
        return redirect(event.get_absolute_url())


class EventJournalView(ConditionalLoginRequiredMixin, View):
    """
    Add a journal entry from the event page and come back to it.

    NetBox's own add view always returns to the Journal tab, so the event page
    posts here instead. The rule is NetBox's: anyone who may view the event and
    add journal entries, not only those who may edit the event.
    """

    def post(self, request, pk):
        event = get_object_or_404(Event.objects.restrict(request.user, 'view'), pk=pk)
        if not request.user.has_perm('extras.add_journalentry'):
            raise PermissionDenied(_('You may not add journal entries.'))

        # The entry always belongs to this event, whatever the browser sends.
        data = request.POST.copy()
        data['assigned_object_type'] = ContentType.objects.get_for_model(Event).pk
        data['assigned_object_id'] = event.pk
        form = JournalEntryForm(data)
        if not form.is_valid():
            for field, errors in form.errors.items():
                label = form.fields[field].label if field in form.fields else ''
                messages.error(request, f"{label}: {' '.join(errors)}" if label else ' '.join(errors))
            return redirect(event.get_absolute_url())

        with transaction.atomic():
            entry = form.save(commit=False)
            entry.created_by = request.user
            entry.save()
            form.save_m2m()
            # As in NetBox's edit views: undo the save if a permission constraint does not allow it.
            if not JournalEntry.objects.restrict(request.user, 'add').filter(pk=entry.pk).exists():
                raise PermissionDenied(_('You may not add this journal entry.'))

        messages.success(request, _('Added a journal entry.'))
        return redirect(event.get_absolute_url())


class EventICSView(ConditionalLoginRequiredMixin, View):
    """One event as an .ics file, to add it to a calendar by hand."""

    def get(self, request, pk):
        event = get_object_or_404(Event.objects.restrict(request.user, 'view'), pk=pk)
        text = ical.calendar([event], name=event.title, describe=_describe)
        response = HttpResponse(text, content_type='text/calendar; charset=utf-8')
        response['Content-Disposition'] = f'attachment; filename="event-{event.pk}.ics"'
        return response


def _describe(event):
    # What the event concerns, in words (the iCal LOCATION).
    return describe(event)


#
# Calendar pages
#

def filtered_events(request):
    """The events the user may view, narrowed by the page's filters."""
    queryset = Event.objects.restrict(request.user, 'view')
    return filtersets.EventFilterSet(request.GET, queryset, request=request).qs


def filter_query(request, drop=()):
    """The page's filters as a query string, without `drop`, for links that keep them."""
    query = request.GET.copy()
    for key in ('year', 'month', 'start', 'days', *drop):
        query.pop(key, None)
    return query.urlencode()


class MonthView(ConditionalLoginRequiredMixin, View):
    """The month calendar: whole weeks, each day with its events."""

    def get(self, request):
        if not request.user.has_perm('netbox_calendar.view_event'):
            raise PermissionDenied
        today = timezone.localdate()
        try:
            year = int(request.GET.get('year', today.year))
            month = int(request.GET.get('month', today.month))
            shown = date(year, month, 1)
        except ValueError:
            shown = today.replace(day=1)
        first_weekday = int(plugin_settings.get_setting('first_weekday', 0) or 0)
        first, last = agenda.month_bounds(shown.year, shown.month, first_weekday)
        after = agenda.local_midnight(first)
        before = agenda.local_midnight(last + timedelta(days=1))
        entries = agenda.entries(filtered_events(request), after, before, request.user)
        previous = (shown - timedelta(days=1)).replace(day=1)
        following = (shown + timedelta(days=32)).replace(day=1)
        return render(request, 'netbox_calendar/month.html', {
            'shown': shown,
            'weeks': agenda.month_grid(shown.year, shown.month, entries, first_weekday),
            'weekday_names': agenda.weekday_names(first_weekday),
            'previous': previous,
            'following': following,
            'today': today,
            'query': filter_query(request),
            'filter_form': forms.CalendarFilterForm(request.GET),
            'keep_month': {'year': shown.year, 'month': shown.month},
            'active_tab': 'month',
        })


class TimelineView(ConditionalLoginRequiredMixin, View):
    """Every event in date order, grouped by day: from today on, or past ones."""
    STEP = 30

    def get(self, request):
        if not request.user.has_perm('netbox_calendar.view_event'):
            raise PermissionDenied
        today = timezone.localdate()
        past = request.GET.get('past') == 'true'
        try:
            days = max(1, min(int(request.GET.get('days', self.STEP)), 3660))
        except ValueError:
            days = self.STEP
        if past:
            after = agenda.local_midnight(today - timedelta(days=days))
            before = timezone.now()
        else:
            after = timezone.now()
            before = agenda.local_midnight(today + timedelta(days=days + 1))
        entries = agenda.entries(filtered_events(request), after, before, request.user)
        groups = agenda.by_day(entries, first_day=None if past else today)
        if past:
            groups.reverse()
        return render(request, 'netbox_calendar/timeline.html', {
            'groups': groups,
            'past': past,
            'days': days,
            'more_days': days + self.STEP,
            'today': today,
            'query': filter_query(request, drop=('past',)),
            'filter_form': forms.CalendarFilterForm(request.GET),
            'keep_timeline': {'past': 'true'} if past else {},
            'active_tab': 'timeline',
        })


#
# iCal feed
#

def feed_token(user, reset=False):
    """The user's feed token, made on first use or when reset."""
    token = FeedToken.objects.filter(user=user).first()
    if token is None or reset:
        value = secrets.token_urlsafe(32)
        token, _created = FeedToken.objects.update_or_create(user=user, defaults={'token': value})
    return token


class MyFeedView(ConditionalLoginRequiredMixin, View):
    """The user's personal feed address, with optional filters, and a reset button."""

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        token = feed_token(request.user)
        path = reverse('plugins:netbox_calendar:feed', args=[token.token])
        return render(request, 'netbox_calendar/feed.html', {
            'enabled': plugin_settings.on('ical_feeds'),
            # The netbox_url setting when set, else the address this page was opened at.
            'feed_url': ical.absolute_url(path) if plugin_settings.get_setting('netbox_url') else
            request.build_absolute_uri(path),
            'base_url': plugin_settings.get_setting('netbox_url') or '',
            'event_types': EventType.objects.restrict(request.user, 'view'),
            'active_tab': 'feed',
        })

    def post(self, request):
        if not request.user.is_authenticated:
            raise PermissionDenied
        feed_token(request.user, reset=True)
        messages.success(request, _('Your feed has a new address. The old one no longer works.'))
        return redirect('plugins:netbox_calendar:my_feed')


class FeedView(View):
    """
    A user's events as an iCal feed, for Outlook and other calendars. No
    login: the secret token in the address stands for the user, and the
    feed holds only events that user may view. Filters as on the event list
    (event_type, status, mine, tag, ...) narrow it.
    """

    def get(self, request, token):
        if not plugin_settings.on('ical_feeds'):
            raise Http404
        found = FeedToken.objects.filter(token=token).select_related('user').first()
        if found is None or not found.user.is_active:
            raise Http404
        user = found.user
        request.user = user
        queryset = Event.objects.restrict(user, 'view')
        queryset = filtersets.EventFilterSet(request.GET, queryset, request=request).qs
        # Single events from the last 90 days on; repeating series whole.
        since = timezone.now() - timedelta(days=90)
        queryset = queryset.filter(Q(end__gte=since) | ~Q(rrule='')).select_related(
            'event_type', 'event_owner',
        ).prefetch_related('occurrence_changes')
        text = ical.calendar(queryset, name=_('NetBox events'), describe=_describe, refresh='PT1H')
        response = HttpResponse(text, content_type='text/calendar; charset=utf-8')
        # Shown inline, never cached by proxies: the feed is personal.
        response['Content-Disposition'] = 'inline; filename="netbox-events.ics"'
        response['Cache-Control'] = 'private, no-cache'
        return response


#
# Settings
#

class SettingsView(ConditionalLoginRequiredMixin, View):
    """Calendar -> Settings, superusers only. See plugin_settings.py."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        if not request.user.is_superuser:
            raise PermissionDenied(_('Only superusers can change Calendar settings.'))
        return super().dispatch(request, *args, **kwargs)

    def _render(self, request, form):

        saved = {row.key: row for row in PluginSetting.objects.select_related('updated_by')}
        rows = [
            {'field': form[setting.key], 'source': plugin_settings.source(setting.key), 'saved': saved.get(setting.key)}
            for setting in plugin_settings.SETTINGS
        ]
        return render(request, 'netbox_calendar/settings.html', {'form': form, 'rows': rows})

    def get(self, request):
        return self._render(request, forms.SettingsForm())

    def post(self, request):
        reset_key = request.POST.get('reset')
        if reset_key in plugin_settings.BY_KEY:
            plugin_settings.reset(reset_key)
            messages.success(request, _('{setting} now follows configuration.py.').format(
                setting=plugin_settings.BY_KEY[reset_key].label,
            ))
            return redirect('plugins:netbox_calendar:settings')
        form = forms.SettingsForm(request.POST)
        if not form.is_valid():
            return self._render(request, form)
        for key, value in form.stored_values().items():
            plugin_settings.save(key, value, user=request.user)
        messages.success(request, _('Settings saved.'))
        return redirect('plugins:netbox_calendar:settings')
