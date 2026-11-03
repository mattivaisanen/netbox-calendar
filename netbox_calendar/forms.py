import re
from datetime import datetime, time, timedelta

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from core.models import ObjectType
from netbox.forms import (
    NetBoxModelBulkEditForm, NetBoxModelFilterSetForm, NetBoxModelForm, NetBoxModelImportForm,
)
from tenancy.models import Contact
from users.models import Group
from utilities.forms import add_blank_choice
from utilities.forms.fields import (
    ColorField, CommentField, ContentTypeChoiceField, CSVChoiceField, CSVModelChoiceField, DynamicModelChoiceField,
    DynamicModelMultipleChoiceField, SlugField, TagFilterField,
)
from utilities.forms.rendering import FieldSet
from utilities.forms.utils import get_field_value
from utilities.forms.widgets import DatePicker, DateTimePicker, HTMXSelect

from . import plugin_settings, recurrence
from .choices import EventStatusChoices, ImpactChoices, NoticeChoices
from .lookup import DEVICE_MATCHES, EXCLUDABLE
from .models import Event, EventObject, EventPerson, EventRole, EventType, OccurrenceChange, display_name

User = get_user_model()


class InlineCheckboxes(forms.CheckboxSelectMultiple):
    """
    Tick boxes side by side, styled like NetBox's own checkboxes. NetBox's
    input template gives every <input> the "form-control" class, so Django's
    default tick boxes show up as empty text fields stacked above their labels.
    """
    template_name = 'netbox_calendar/widgets/inline_checkboxes.html'
    option_template_name = 'netbox_calendar/widgets/inline_checkbox_option.html'


def flatpickr_first_weekday():
    """The calendar's first day of the week as flatpickr counts it (0 = Sunday)."""
    return (int(plugin_settings.get_setting('first_weekday', 0) or 0) + 1) % 7


class CalendarPickerMixin:
    """
    NetBox's date picker (flatpickr), starting the week on the calendar's
    first weekday and shading weekends. NetBox's own setup always starts on
    Sunday; inc/form_script.html sets the picker up again.
    """
    template_name = 'netbox_calendar/widgets/picker.html'

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['widget']['attrs']['data-first-weekday'] = flatpickr_first_weekday()
        return context


class CalendarDatePicker(CalendarPickerMixin, DatePicker):
    pass


class CalendarDateTimePicker(CalendarPickerMixin, DateTimePicker):
    pass


class ToggleButtons(forms.RadioSelect):
    """
    A choice shown as a row of buttons (one pressed). Picking another one
    redraws the When section (HTMX), showing only the field that choice uses.
    """
    template_name = 'netbox_calendar/widgets/toggle_buttons.html'


class NextTimesWidget(forms.Widget):
    """
    Read-only line with the next times a repeating event happens. Refreshed
    (HTMX) whenever a field of the When section changes; set `text` before
    rendering.
    """
    template_name = 'netbox_calendar/widgets/next_times.html'
    text = ''

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['widget']['text'] = self.text
        return context

    def value_from_datadict(self, data, files, name):
        return None


class LengthInput(forms.TextInput):
    """
    The length box, with the end it gives (start + length) beside it in a
    muted, read-only box. Refreshed (HTMX) whenever a field of the When
    section changes; set `end_text` before rendering.
    """
    template_name = 'netbox_calendar/widgets/length_input.html'
    end_text = ''

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        context['widget']['end_text'] = self.end_text
        return context


def when_text(value, all_day=False):
    """A start or end in NetBox's short date (and time) format, with the weekday: "Tue 10.11.2026 10:00"."""
    value = timezone.localtime(value)
    return f"{date_format(value, 'D')} {date_format(value, 'SHORT_DATE_FORMAT' if all_day else 'SHORT_DATETIME_FORMAT')}"


class UserLabelMixin:
    """
    Label picked users with their full name, the way NetBox's own user
    search lists them ("jdoe (Jane Doe)"), so a saved choice reads the same
    when the form is opened again.
    """
    def label_from_instance(self, obj):
        name = display_name(obj)
        return f'{obj.username} ({name})' if name != obj.username else obj.username


class UserChoiceField(UserLabelMixin, DynamicModelChoiceField):
    pass


class UserMultipleChoiceField(UserLabelMixin, DynamicModelMultipleChoiceField):
    pass

REPEAT_CHOICES = (
    (recurrence.NONE, _('Does not repeat')),
    (recurrence.DAILY, _('Daily')),
    (recurrence.WEEKLY, _('Weekly')),
    (recurrence.MONTHLY_DATE, _('Monthly on the same date')),
    (recurrence.MONTHLY_WEEKDAY, _('Monthly on the same weekday (such as the 2nd Tuesday)')),
    (recurrence.YEARLY, _('Yearly')),
    (recurrence.CUSTOM, _('Custom rule (iCal RRULE)')),
)

WEEKDAY_CHOICES = (
    ('MO', _('Mon')), ('TU', _('Tue')), ('WE', _('Wed')), ('TH', _('Thu')),
    ('FR', _('Fri')), ('SA', _('Sat')), ('SU', _('Sun')),
)

#: "1d, 2h, 30m" in minutes.
UNITS = {'w': 7 * 1440, 'd': 1440, 'h': 60, 'm': 1}


def parse_minutes(text):
    """
    Reminder times as minutes: "1d, 2h, 30m" -> [1440, 120, 30]. A bare
    number is minutes. Empty: None. "none": [] (no reminders).
    """
    text = (text or '').strip().lower()
    if not text:
        return None
    if text in ('none', '-', '0'):
        return []
    found = []
    for part in re.split(r'[,\s]+', text):
        if not part:
            continue
        match = re.fullmatch(r'(\d+)([wdhm]?)', part)
        if not match:
            raise ValidationError(_('"{part}" is not a time; use numbers with w, d, h or m, such as 1d, 2h.').format(
                part=part,
            ))
        found.append(int(match.group(1)) * UNITS[match.group(2) or 'm'])
    return sorted(set(found), reverse=True)


def parse_length(text):
    """
    An event's length in minutes: "1h 30m", "2d", "1d4h"; a bare number is
    minutes. Empty: None.
    """
    text = (text or '').strip().lower()
    if not text:
        return None
    if not re.fullmatch(r'(\s*\d+\s*[wdhm]?\s*,?)+', text):
        raise ValidationError(_('"{text}" is not a length; use numbers with w, d, h or m, such as 1h 30m.').format(
            text=text,
        ))
    minutes = sum(int(count) * UNITS[unit or 'm'] for count, unit in re.findall(r'(\d+)\s*([wdhm]?)', text))
    if minutes < 1:
        raise ValidationError(_('The length must be at least one minute.'))
    return minutes


def format_length(minutes):
    """90 -> "1h 30m", 1440 -> "1d"."""
    parts = []
    for unit in ('d', 'h', 'm'):
        count, minutes = divmod(minutes, UNITS[unit])
        if count:
            parts.append(f'{count}{unit}')
    return ' '.join(parts) or '0m'


def format_minutes(values):
    """[1440, 90] -> "1d, 1h 30m"."""
    if values is None:
        return ''
    if not values:
        return 'none'
    parts = []
    for value in values:
        for unit in ('w', 'd', 'h', 'm'):
            if value and value % UNITS[unit] == 0:
                parts.append(f'{value // UNITS[unit]}{unit}')
                break
        else:
            parts.append('0m')
    return ', '.join(parts)


def describe_minutes(values):
    """[1440, 90] -> "1 day, 1 hour 30 minutes", for pages and choice labels."""
    names = (
        ('w', lambda n: ngettext('%(n)d week', '%(n)d weeks', n)),
        ('d', lambda n: ngettext('%(n)d day', '%(n)d days', n)),
        ('h', lambda n: ngettext('%(n)d hour', '%(n)d hours', n)),
        ('m', lambda n: ngettext('%(n)d minute', '%(n)d minutes', n)),
    )
    texts = []
    for value in values or ():
        parts = []
        for unit, name in names:
            count, value = divmod(value, UNITS[unit])
            if count:
                parts.append(name(count) % {'n': count})
        texts.append(' '.join(parts) or _('at the start'))
    return ', '.join(texts)


#: Reminder times offered as tick boxes, in minutes before the start.
REMINDER_PRESETS = (15, 30, 60, 120, 1440, 2 * 1440, 10080)

#: Where an event's reminders come from.
REMINDERS_FROM_TYPE = 'type'
REMINDERS_OWN = 'own'
REMINDERS_NONE = 'none'
REMINDER_SOURCE_CHOICES = (
    (REMINDERS_FROM_TYPE, _("Same as the event type")),
    (REMINDERS_OWN, _('Pick for this event')),
    (REMINDERS_NONE, _('No reminders')),
)


def initial_minutes(value):
    """Minutes passed in a URL ("60" or a list of strings) as a list; None when empty or unreadable."""
    if isinstance(value, str):
        value = value.split(',')
    try:
        return [int(v) for v in value if str(v).strip()] or None
    except (TypeError, ValueError):
        return None


class ReminderFieldsMixin:
    """
    Reminders as tick boxes for common times, plus a text box for any
    other time ("3d, 45m"). Both together make the stored list of minutes.
    """
    def setup_reminder_fields(self, values):
        """Fill the two fields from stored `values` (minutes), unless the form is bound."""
        if self.is_bound or values is None:
            return
        self.initial.setdefault('reminder_presets', [str(v) for v in values if v in REMINDER_PRESETS])
        other = [v for v in values if v not in REMINDER_PRESETS]
        self.initial.setdefault('reminder_other', format_minutes(other) if other else '')

    def clean_reminder_other(self):
        return parse_minutes(self.cleaned_data.get('reminder_other')) or []

    def picked_reminders(self):
        """The ticked and typed times as one list of minutes, longest first."""
        picked = {int(v) for v in self.cleaned_data.get('reminder_presets') or ()}
        picked.update(self.cleaned_data.get('reminder_other') or ())
        return sorted(picked, reverse=True)


def reminder_preset_field():
    """Tick boxes for the common reminder times."""
    return forms.MultipleChoiceField(
        # A callable, so the labels are translated per request.
        choices=lambda: [(str(v), describe_minutes([v])) for v in REMINDER_PRESETS], required=False,
        label=_('Before the start'), widget=InlineCheckboxes,
    )


def reminder_other_field():
    """A text box for any reminder time not among the tick boxes."""
    return forms.CharField(
        required=False, label=_('Other times'),
        help_text=_('Any other times before the start, such as "3d, 45m" (w, d, h, m).'),
    )


def linkable_types():
    """
    Object types events can be linked to: NetBox objects, this plugin's own
    left out, and tags (a condition on devices and VMs).
    """
    linkable = ObjectType.objects.with_feature('bookmarks').exclude(app_label='netbox_calendar').values('pk')
    return ContentType.objects.filter(
        Q(pk__in=linkable) | Q(app_label='extras', model='tag'),
    ).order_by('app_label', 'model')


#: Included or excluded objects, in the event form's field names.
INCLUDE, EXCLUDE = 'include', 'exclude'

#: Kinds of object listed first when adding objects to an event, in this order.
COMMON_TYPES = (
    'dcim.device', 'virtualization.virtualmachine', 'dcim.devicerole', 'dcim.devicetype', 'dcim.manufacturer',
    'dcim.platform', 'dcim.site', 'dcim.location', 'dcim.region', 'dcim.sitegroup', 'dcim.rack',
    'virtualization.cluster', 'tenancy.tenant', 'extras.tag', 'circuits.circuit',
)


def type_label(content_type):
    """"dcim.site" for the content type of sites."""
    return f'{content_type.app_label}.{content_type.model}'


def target_field(label, exclude=False):
    """The event form's field for included or excluded objects of `label` ("dcim.site" -> "include__dcim__site")."""
    app_label, model = label.split('.')
    return f'{EXCLUDE if exclude else INCLUDE}__{app_label}__{model}'


def parse_target_field(name):
    """(label, exclude) of a field name made by target_field(), or None for any other name."""
    parts = name.split('__')
    if len(parts) != 3 or parts[0] not in (INCLUDE, EXCLUDE):
        return None
    return f'{parts[1]}.{parts[2]}', parts[0] == EXCLUDE


def _type_order(label):
    return (COMMON_TYPES.index(label) if label in COMMON_TYPES else len(COMMON_TYPES), label)


def _as_list(value):
    if value in (None, ''):
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]


#: Sent with the event form when its objects section was shown, so saving
#: knows an empty section means "no objects" (and not "left out").
OBJECTS_SHOWN = 'objects_shown'


def target_type_groups(exclude=False):
    """
    The type menu of one objects row, as [(group, [(label, name)])]: kinds
    that pick devices and VMs by where or what they are, then specific
    objects. An exclude row offers only the kinds that can be excluded.
    """
    types = {type_label(content_type): content_type for content_type in linkable_types()}
    ordered = sorted(types, key=_type_order)
    if exclude:
        ordered = [label for label in ordered if label in EXCLUDABLE]
    # Without the scope roll-up a site, role, ... is just one more object.
    matching = DEVICE_MATCHES if plugin_settings.get_setting('scope_rollup', True) else ()
    groups = [
        (_('Devices and VMs by'), [(label, types[label].name.capitalize()) for label in ordered if label in matching]),
        (_('Specific objects'), [(label, types[label].name.capitalize()) for label in ordered if label not in matching]),
    ]
    return [(group, options) for group, options in groups if options]


def target_picker(label):
    """The objects picker of one row, for objects of type `label` ("dcim.site")."""
    model = ContentType.objects.get_by_natural_key(*label.split('.')).model_class()
    return DynamicModelMultipleChoiceField(
        queryset=model.objects.all(), required=False, label=model._meta.verbose_name_plural.capitalize(),
    )


class TargetRowForm(forms.Form):
    """
    One row of the event form's objects section on its own: include or
    exclude, a type, and a picker of that type. Drawn by the row view when a
    row is added or its mode or type changes (inc/target_row.html).
    """
    def __init__(self, exclude=False, label='', values=(), *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.exclude = exclude
        self.type_groups = target_type_groups(exclude)
        offered = {value for _group, options in self.type_groups for value, _name in options}
        # A type this mode does not offer (a circuit switched to Exclude) starts the row over.
        self.label = label if label in offered else ''
        self.picker = None
        if self.label:
            name = target_field(self.label, exclude)
            self.fields[name] = target_picker(self.label)
            self.initial[name] = list(values)
            self.picker = self[name]


class TargetFieldsMixin:
    """
    The event form's objects: a list of rows, each "include or exclude, one
    type of object, the objects". One picker field per type and mode in use
    (named by target_field()); rows are added, changed and removed in the
    page without redrawing the form (EventTargetRowView). Opening the form
    shows a row per kind the event links now. See lookup.py for how the rows
    combine.
    """
    def setup_target_fields(self):
        types = {type_label(content_type): content_type for content_type in linkable_types()}

        # What each picker shows: the values sent with the form (or in the
        # address), and the event's links when the form is first opened.
        source = self.data if self.is_bound else self.initial
        values = {}
        if self.instance.pk and not self.is_bound and OBJECTS_SHOWN not in source:
            for link in self.instance.links.select_related('object_type'):
                values.setdefault(target_field(type_label(link.object_type), link.exclude), []).append(link.object_id)
        for key in source:
            if parse_target_field(key):
                values[key] = source.getlist(key) if hasattr(source, 'getlist') else _as_list(source[key])

        self.target_names = []
        for name in sorted(values, key=lambda n: (parse_target_field(n)[1], _type_order(parse_target_field(n)[0]))):
            label, exclude = parse_target_field(name)
            if label not in types or (exclude and label not in EXCLUDABLE):
                continue
            self.fields[name] = target_picker(label)
            if not self.is_bound:
                self.initial[name] = values[name]
            self.target_names.append(name)

    @property
    def target_rows(self):
        """The rows for the template: exclude, type and picker (a bound field) of each."""
        rows = []
        for name in self.target_names:
            label, exclude = parse_target_field(name)
            rows.append({
                'exclude': exclude, 'label': label, 'picker': self[name],
                'type_groups': target_type_groups(exclude),
            })
        return rows

    def clean_targets(self):
        """An object can't be both included and excluded."""
        for name in self.target_names:
            label, exclude = parse_target_field(name)
            if exclude:
                both = set(self.cleaned_data.get(name) or ()) & set(self.cleaned_data.get(target_field(label)) or ())
                if both:
                    self.add_error(name, _('Included and excluded at once: {objects}.').format(
                        objects=', '.join(str(obj) for obj in both),
                    ))

    def save_targets(self, event):
        """
        Make the event's links match the pickers: link what was added, unlink
        what was taken out, switch what moved between included and excluded.
        Links to objects deleted since are kept (the event page shows them).
        """
        if OBJECTS_SHOWN not in self.data and not self.target_names:
            # Sent without the objects section (by a script, say): leave the links be.
            return
        wanted = {}
        for name in self.target_names:
            _label, exclude = parse_target_field(name)
            for obj in self.cleaned_data.get(name) or ():
                wanted[(ContentType.objects.get_for_model(obj).pk, obj.pk)] = (obj, exclude)
        for link in event.links.select_related('object_type'):
            key = (link.object_type_id, link.object_id)
            if key in wanted:
                _obj, exclude = wanted.pop(key)
                if link.exclude != exclude:
                    link.snapshot()
                    link.exclude = exclude
                    link.save()
            elif link.object is not None:
                link.snapshot()
                link.delete()
        for (type_id, object_id), (obj, exclude) in wanted.items():
            EventObject.objects.create(
                event=event, object_type_id=type_id, object_id=object_id, object_repr=str(obj)[:200], exclude=exclude,
            )


class LinkFieldsMixin:
    """
    Two fields to link objects of any other type: the type, and the
    objects. A new type re-renders the form (HTMX), so the object list
    searches that type.
    """
    def setup_link_fields(self):
        content_type = None
        value = get_field_value(self, 'link_type')
        if value:
            content_type = linkable_types().filter(pk=value).first()
        model = content_type.model_class() if content_type else None
        if model is None:
            # Nothing to search until a type is picked.
            del self.fields['link_objects']
            return
        field = self.fields['link_objects']
        field.queryset = model.objects.all()
        field.label = _('{type} to link').format(type=model._meta.verbose_name_plural.capitalize())
        field.widget.attrs['selector'] = model._meta.label_lower

    def save_links(self, event):
        """Link the picked objects to `event` (already saved), included or excluded. Adds only."""
        content_type = self.cleaned_data.get('link_type')
        exclude = bool(self.cleaned_data.get('exclude'))
        for obj in self.cleaned_data.get('link_objects') or ():
            link, created = EventObject.objects.get_or_create(
                event=event, object_type=content_type, object_id=obj.pk,
                defaults={'object_repr': str(obj)[:200], 'exclude': exclude},
            )
            if not created and link.exclude != exclude:
                link.snapshot()
                link.exclude = exclude
                link.save()


#
# Event types and roles
#

class EventTypeForm(ReminderFieldsMixin, NetBoxModelForm):
    slug = SlugField()
    color = ColorField(label=_('Color'))
    notify_on = forms.MultipleChoiceField(
        choices=NoticeChoices, required=False, label=_('Notify on'), widget=InlineCheckboxes,
        help_text=_('What people are told about. None ticked: nothing.'),
    )
    reminder_presets = reminder_preset_field()
    reminder_other = reminder_other_field()
    notify_group = DynamicModelChoiceField(queryset=Group.objects.all(), required=False, label=_('Notify group'))

    fieldsets = (
        FieldSet('name', 'slug', 'color', 'icon', 'description', 'default_duration', 'change_freeze', 'tags',
                 name=_('Event type')),
        FieldSet('notify_on', name=_('When')),
        FieldSet('reminder_presets', 'reminder_other', name=_('Reminders')),
        FieldSet('notify_owner', 'notify_people', 'notify_group', 'notify_subscribers', 'notify_contacts',
                 'send_email', name=_('Who')),
    )

    class Meta:
        model = EventType
        fields = (
            'name', 'slug', 'color', 'icon', 'description', 'default_duration', 'change_freeze', 'notify_on',
            'notify_owner', 'notify_people', 'notify_group', 'notify_subscribers', 'notify_contacts',
            'send_email', 'tags',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setup_reminder_fields(self.instance.reminders if self.instance.pk else None)

    def clean(self):
        super().clean()
        # Nothing ticked or typed: no reminders for events of this type.
        self.instance.reminders = self.picked_reminders()
        return self.cleaned_data


class EventTypeFilterForm(NetBoxModelFilterSetForm):
    model = EventType
    fieldsets = (FieldSet('q', 'filter_id', 'tag', 'change_freeze'),)
    change_freeze = forms.NullBooleanField(required=False, label=_('Change freeze'), widget=forms.Select(
        choices=(('', '---------'), ('true', _('Yes')), ('false', _('No'))),
    ))
    tag = TagFilterField(model)


class EventTypeBulkEditForm(NetBoxModelBulkEditForm):
    model = EventType
    color = ColorField(required=False, label=_('Color'))
    default_duration = forms.IntegerField(required=False, min_value=1, label=_('Default duration'))
    change_freeze = forms.NullBooleanField(required=False, label=_('Change freeze'))
    send_email = forms.NullBooleanField(required=False, label=_('Send e-mail'))
    notify_group = DynamicModelChoiceField(queryset=Group.objects.all(), required=False, label=_('Notify group'))
    description = forms.CharField(required=False, max_length=200, label=_('Description'))

    fieldsets = (FieldSet('color', 'default_duration', 'change_freeze', 'send_email', 'notify_group', 'description'),)
    nullable_fields = ('notify_group', 'description')


class EventTypeImportForm(NetBoxModelImportForm):
    slug = SlugField()

    class Meta:
        model = EventType
        fields = ('name', 'slug', 'color', 'icon', 'description', 'default_duration', 'change_freeze', 'send_email',
                  'tags')


class EventRoleForm(NetBoxModelForm):
    slug = SlugField()

    fieldsets = (FieldSet('name', 'slug', 'can_edit', 'notify', 'weight', 'description', 'tags', name=_('Role')),)

    class Meta:
        model = EventRole
        fields = ('name', 'slug', 'can_edit', 'notify', 'weight', 'description', 'tags')


class EventRoleFilterForm(NetBoxModelFilterSetForm):
    model = EventRole
    fieldsets = (FieldSet('q', 'filter_id', 'tag'),)
    tag = TagFilterField(model)


class EventRoleBulkEditForm(NetBoxModelBulkEditForm):
    model = EventRole
    can_edit = forms.NullBooleanField(required=False, label=_('Can edit'))
    notify = forms.NullBooleanField(required=False, label=_('Notified'))
    weight = forms.IntegerField(required=False, min_value=0, label=_('Weight'))
    description = forms.CharField(required=False, max_length=200, label=_('Description'))

    fieldsets = (FieldSet('can_edit', 'notify', 'weight', 'description'),)
    nullable_fields = ('description',)


class EventRoleImportForm(NetBoxModelImportForm):
    slug = SlugField()

    class Meta:
        model = EventRole
        fields = ('name', 'slug', 'can_edit', 'notify', 'weight', 'description', 'tags')


#
# Events
#

#: What "Every" counts, by kind of repeat.
INTERVAL_HELP = {
    recurrence.DAILY: _('In days: 1 every day, 2 every other day.'),
    recurrence.WEEKLY: _('In weeks: 1 every week, 2 every other week.'),
    recurrence.MONTHLY_DATE: _('In months: 1 every month, 3 every quarter.'),
    recurrence.MONTHLY_WEEKDAY: _('In months: 1 every month, 3 every quarter.'),
    recurrence.YEARLY: _('In years: 1 every year, 2 every other year.'),
}

#: Values of EventForm's hidden tab fields: which tab of "When" and "Repeat" is open.
LENGTH, END = 'length', 'end'
NEVER, UNTIL, COUNT = 'never', 'until', 'count'


class EventForm(ReminderFieldsMixin, TargetFieldsMixin, NetBoxModelForm):
    """
    Add or edit an event. Besides the event's own fields it has the objects
    section (TargetFieldsMixin): saving links what was added and unlinks
    what was taken out. People are added on the event page. Repeat fields
    build the iCal rule (recurrence.build()).
    """
    event_type = DynamicModelChoiceField(queryset=EventType.objects.all(), label=_('Type'))
    event_owner = UserChoiceField(
        queryset=User.objects.all(), required=False, label=_('Owner'),
        help_text=_('Empty on a new event: you.'),
    )
    start = forms.DateTimeField(label=_('Start'), widget=CalendarDateTimePicker())
    # The end is given either as a length or as an end time (End by); all-day events give the last day.
    length = forms.CharField(
        label=_('Length'), required=False, widget=LengthInput(),
        help_text=_("Such as 30m, 2h or 1d 4h. Empty: the type's default duration."),
    )
    end = forms.DateTimeField(
        label=_('End'), required=False, widget=CalendarDateTimePicker(),
        help_text=_("Empty: the type's default duration. All-day events: the last day."),
    )
    end_by = forms.ChoiceField(
        choices=((LENGTH, _('Length')), (END, _('End date'))), required=False, label=_('End by'),
        widget=ToggleButtons(),
    )
    # Ticking All day redraws the When section, with dates instead of dates and times.
    all_day = forms.BooleanField(
        required=False, label=_('All day'),
        widget=forms.CheckboxInput(attrs={
            'hx-get': '.', 'hx-include': '#form_fields', 'hx-target': '#when_fields', 'hx-select': '#when_fields',
            'hx-swap': 'outerHTML',
        }),
    )

    repeat = forms.ChoiceField(
        choices=REPEAT_CHOICES, required=False, label=_('Repeat'),
        widget=HTMXSelect(hx_target_id='when_fields'),
    )
    repeat_interval = forms.IntegerField(
        min_value=1, required=False, initial=1, label=_('Every'),
        help_text=_('1 every time, 2 every other time, and so on.'),
    )
    repeat_weekdays = forms.MultipleChoiceField(
        choices=WEEKDAY_CHOICES, required=False, label=_('On'), widget=InlineCheckboxes,
        help_text=_("Must include the start's weekday."),
    )
    repeat_until = forms.DateField(
        required=False, label=_('Until'), widget=CalendarDatePicker(),
        help_text=_('Repeats up to and including this day.'),
    )
    repeat_count = forms.IntegerField(
        min_value=1, required=False, label=_('Times'), help_text=_('How many times in all.'),
    )
    # Repeating goes on, or stops on a day or after a number of times.
    repeat_end_by = forms.ChoiceField(
        choices=((NEVER, _('No end')), (UNTIL, _('Until a day')), (COUNT, _('Number of times'))), required=False,
        label=_('Ends'), widget=ToggleButtons(),
    )
    repeat_next = forms.CharField(required=False, label=_('Next'), widget=NextTimesWidget())
    reminder_source = forms.ChoiceField(
        choices=REMINDER_SOURCE_CHOICES, required=False, initial=REMINDERS_FROM_TYPE, label=_('Reminders'),
        widget=HTMXSelect(hx_target_id='reminder_fields'),
    )
    reminder_presets = reminder_preset_field()
    reminder_other = reminder_other_field()

    # Marks that the objects section was sent; its rows' pickers are added in __init__().
    objects_shown = forms.CharField(required=False, initial='1', widget=forms.HiddenInput)
    filter_object_type = ContentTypeChoiceField(
        queryset=ContentType.objects.none(), required=False, label=_('Object type'),
    )
    filter_query = forms.CharField(
        required=False, max_length=1000, label=_('Filter'),
        help_text=_('Rarely needed. A filter as in an object list\'s address after "?", such as '
                    'status=active&tag=core. Objects added later match too.'),
    )

    comments = CommentField()

    fieldsets = (
        FieldSet('title', 'event_type', 'status', 'impact', 'event_owner', 'description', 'tags', name=_('Event')),
        # Start, end and repeat in one section; the repeat fields in use are set in __init__().
        FieldSet('start', name=_('When'), html_id='when_fields'),
        # The pickers in use are added to this section in __init__().
        # The template draws this section as rows (TargetFieldsMixin.target_rows).
        FieldSet(name=_('Objects'), html_id='target_fields'),
        FieldSet('filter_object_type', 'filter_query', name=_('Objects by filter')),
        FieldSet('reminder_source', 'reminder_presets', 'reminder_other', name=_('Notifications'),
                 html_id='reminder_fields'),
    )

    class Meta:
        model = Event
        fields = (
            'title', 'event_type', 'status', 'impact', 'event_owner', 'description', 'start', 'end', 'all_day', 'rrule',
            'filter_object_type', 'filter_query', 'comments', 'tags',
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['filter_object_type'].queryset = linkable_types()
        instance = self.instance
        self.setup_target_fields()
        self.fieldsets = tuple(
            FieldSet(*self.target_names, name=_('Objects'), html_id='target_fields')
            if getattr(fieldset, 'html_id', None) == 'target_fields' else fieldset
            for fieldset in self.fieldsets
        )

        if instance.pk and not self.is_bound:
            # Show the repeat fields for the stored rule, unless the form is re-rendered with new values.
            if 'repeat' not in self.initial:
                simple = recurrence.parse_simple(instance.rrule, instance.start)
                self.initial['repeat'] = simple['kind']
                if simple['kind'] not in (recurrence.NONE, recurrence.CUSTOM):
                    self.initial.update({
                        'repeat_interval': simple['interval'],
                        'repeat_weekdays': simple['weekdays'],
                        'repeat_until': simple['until'],
                        'repeat_count': simple['count'],
                    })
            # An all-day event is stored up to the midnight after its last day; show the last day.
            if instance.all_day and 'end' not in self.initial:
                self.initial['end'] = timezone.localtime(instance.end) - timedelta(days=1)
            if 'length' not in self.initial:
                self.initial['length'] = format_length(int((instance.end - instance.start).total_seconds() // 60))

        # NetBox draws a hidden empty "all_day" before the tick box, so a ticked box
        # sends both values, which come in as a list.
        if isinstance(self.initial.get('all_day'), (list, tuple)):
            self.initial['all_day'] = any(self.initial['all_day'])
        # Redrawn by HTMX (the When section's End by is sent along): an unticked
        # All day sends nothing, so it is off rather than the stored value.
        if not self.is_bound and 'end_by' in self.initial and 'all_day' not in self.initial:
            self.initial['all_day'] = False
        # An end given in the address (and no length) is used as it is.
        if not self.is_bound and self.initial.get('end') and not self.initial.get('length'):
            self.initial.setdefault('end_by', END)
        if not self.is_bound:
            self.initial.setdefault('end_by', LENGTH)

        if self.all_day_value():
            # All day: dates, no End by; the end is the last day (empty: the start day only).
            for name in ('start', 'end'):
                self.fields[name].widget = CalendarDatePicker()
                value = self.initial.get(name)
                if isinstance(value, str) and len(value) > 10:
                    self.initial[name] = value[:10]
            self.fields['end'].label = _('Last day')
            self.fields['end'].help_text = _('For events over several days. Empty: the start day only.')
            self.fields.pop('end_by')
            self.fields.pop('length')
        else:
            # Only the field End by uses: a length (with the end it gives beside it) or an end date.
            if self.end_by_value() == LENGTH:
                self.fields.pop('end')
                self.fields['length'].widget.end_text = self.length_end_text()
            else:
                self.fields.pop('length')

        # Reminders: from the event type, picked for this event, or none.
        reminders = instance.reminders
        if not instance.pk and 'reminders' in self.initial:
            # A cloned event brings its own reminders along in the URL.
            reminders = initial_minutes(self.initial.pop('reminders'))
        if not self.is_bound and 'reminder_source' not in self.initial:
            self.initial['reminder_source'] = (
                REMINDERS_FROM_TYPE if reminders is None else REMINDERS_OWN if reminders else REMINDERS_NONE
            )
        self.setup_reminder_fields(reminders)
        if get_field_value(self, 'reminder_source') != REMINDERS_OWN:
            # The tick boxes only matter when picking reminders for this event.
            self.fields.pop('reminder_presets')
            self.fields.pop('reminder_other')

        # Only the fields the chosen kind of repeat uses.
        repeat = get_field_value(self, 'repeat') or recurrence.NONE
        unused = {
            recurrence.NONE: ('repeat_interval', 'repeat_weekdays', 'repeat_until', 'repeat_count', 'rrule'),
            recurrence.CUSTOM: ('repeat_interval', 'repeat_weekdays', 'repeat_until', 'repeat_count'),
        }.get(repeat, ('rrule',) if repeat == recurrence.WEEKLY else ('rrule', 'repeat_weekdays'))
        for name in unused:
            self.fields.pop(name, None)
        # Ends: no end, until a day or after a number of times; only the field in use.
        if 'repeat_until' in self.fields:
            if not self.is_bound and 'repeat_end_by' not in self.initial:
                self.initial['repeat_end_by'] = (
                    COUNT if self.initial.get('repeat_count') else UNTIL if self.initial.get('repeat_until') else NEVER
                )
            ends = self.repeat_end_value()
            for name, used_by in (('repeat_until', UNTIL), ('repeat_count', COUNT)):
                if ends != used_by:
                    self.fields.pop(name)
        else:
            self.fields.pop('repeat_end_by')
        if repeat == recurrence.NONE:
            self.fields.pop('repeat_next')
        else:
            self.fields['repeat_next'].widget.text = self.next_times_text(repeat)
        if 'repeat_interval' in self.fields:
            self.fields['repeat_interval'].help_text = INTERVAL_HELP.get(repeat, '')
        if 'repeat_weekdays' in self.fields and not get_field_value(self, 'repeat_weekdays'):
            # Weekly starts out on the start's weekday.
            start = self.start_value()
            if start is not None:
                self.initial['repeat_weekdays'] = [recurrence.WEEKDAYS[start.weekday()]]
        self.fieldsets = tuple(
            self.when_fieldset() if getattr(fieldset, 'html_id', None) == 'when_fields' else fieldset
            for fieldset in self.fieldsets
        )

        # Picking the owner needs permission to view users; without it the
        # owner stays as it is (yourself on a new event).
        editor = getattr(instance, '_calendar_editor', None)
        if editor is not None and not editor.has_perm('users.view_user'):
            self.fields.pop('event_owner', None)

    def when_fieldset(self):
        """
        The When section: the start, all day, its end as a length or an end
        date (End by), then how it repeats with the fields that kind of repeat
        uses, ending on a day or after a number of times (Ends), and the next
        times. Changing All day, End by, Repeat or Ends redraws the section
        (HTMX).
        """
        names = (
            'start', 'all_day', 'end_by', 'length', 'end', 'repeat', 'repeat_interval', 'repeat_weekdays', 'rrule',
            'repeat_end_by', 'repeat_until', 'repeat_count', 'repeat_next',
        )
        return FieldSet(*(name for name in names if name in self.fields), name=_('When'), html_id='when_fields')

    def end_by_value(self):
        if self.is_bound:
            return self.data.get('end_by') or (LENGTH if self.data.get('length') else END)
        return get_field_value(self, 'end_by') or LENGTH

    def repeat_end_value(self):
        """How the repeat ends: NEVER, UNTIL or COUNT. Without a choice (API-like posts), the value given."""
        if self.is_bound:
            data = self.data
            return data.get('repeat_end_by') or (
                COUNT if data.get('repeat_count') else UNTIL if data.get('repeat_until') else NEVER
            )
        return get_field_value(self, 'repeat_end_by') or NEVER

    def all_day_value(self):
        if self.is_bound:
            values = self.data.getlist('all_day') if hasattr(self.data, 'getlist') else [self.data.get('all_day')]
        else:
            values = [get_field_value(self, 'all_day')]
        return any(value is True or str(value).lower() in ('on', 'true', '1') for value in values)

    def length_end_text(self):
        """The end the typed length gives, as in "ends Tue 10.11.2026 12:00"; empty when it can't be worked out."""
        start = self.start_value()
        if start is None:
            return ''
        try:
            minutes = parse_length(get_field_value(self, 'length'))
        except ValidationError:
            return ''
        if minutes is None:
            try:
                minutes = EventType.objects.get(pk=get_field_value(self, 'event_type')).default_duration
            except (EventType.DoesNotExist, ValueError, TypeError):
                return ''
        return _('ends {when}').format(when=when_text(start + timedelta(minutes=minutes)))

    def next_times_text(self, repeat, count=3):
        """The next `count` times of the repeat as typed ("Tue 10.11.2026 10:00, ..."); never more, so no end is fine."""
        start = self.start_value()
        if start is None:
            return _('Set the start to see the next times.')
        all_day = self.all_day_value()
        if all_day:
            start = timezone.make_aware(datetime.combine(start.date(), time()), timezone.get_current_timezone())

        def value(name):
            if name not in self.fields:
                return None
            try:
                return self.fields[name].clean(get_field_value(self, name))
            except ValidationError:
                return None

        until, times = value('repeat_until'), value('repeat_count')
        # Only the field in use is in the form, so the other one is None already.
        if repeat == recurrence.CUSTOM:
            try:
                rule = recurrence.normalize(get_field_value(self, 'rrule') or '', start)
            except ValueError:
                rule = ''
        else:
            rule = recurrence.build(
                repeat, start, value('repeat_interval') or 1, value('repeat_weekdays') or (), until, times,
            )
        if not rule:
            return _('Give the rule to see the next times.')
        event = Event(start=start, end=start + timedelta(minutes=1), rrule=rule)
        now = timezone.now()
        found = recurrence.occurrences(event, now, now + timedelta(days=3660), limit=count)
        if not found:
            return _('No more times: the repeat has ended.')
        return ', '.join(when_text(occurrence.start, all_day) for occurrence in found)

    def start_value(self):
        """The start as typed or given, as a local time; None when missing or not a time."""
        try:
            start = self.fields['start'].clean(get_field_value(self, 'start'))
        except ValidationError:
            return None
        return timezone.localtime(start) if start is not None else None

    def clean_length(self):
        return parse_length(self.cleaned_data.get('length'))

    def clean(self):
        super().clean()
        data = self.cleaned_data
        start, end, all_day = data.get('start'), data.get('end'), data.get('all_day')
        event_type = data.get('event_type')
        length = data.get('length')
        # End by decides; without it (API-like posts) a length wins over an end. All day: the last day.
        end_by = END if all_day else data.get('end_by') or (LENGTH if length else END)
        if end_by == LENGTH:
            end = None
            if start and length:
                end = start + timedelta(minutes=length)
        if start and not end:
            end = start + timedelta(minutes=event_type.default_duration if event_type else 60)
            if all_day:
                end = start
        if start and end and all_day:
            # Any time within the last day; the model rounds to the midnight after it.
            local = timezone.localtime(end)
            end = timezone.make_aware(datetime.combine(local.date(), time(12)), timezone.get_current_timezone())
        if start and end:
            data['end'] = end
            self.instance.end = end

        # A new event left without an owner belongs to whoever adds it.
        editor = getattr(self.instance, '_calendar_editor', None)
        if 'event_owner' in self.fields and not data.get('event_owner') and not self.instance.pk and editor is not None:
            data['event_owner'] = editor

        repeat = data.get('repeat') or recurrence.NONE
        if repeat == recurrence.CUSTOM:
            if not data.get('rrule'):
                self.add_error('rrule', _('Type the rule, or pick another kind of repeat.'))
        elif start:
            # Only the end Ends picks counts; without a pick, both given is an error.
            until, count = data.get('repeat_until'), data.get('repeat_count')
            repeat_end_by = data.get('repeat_end_by')
            if repeat_end_by == NEVER:
                until = count = None
            elif repeat_end_by == UNTIL:
                count = None
            elif repeat_end_by == COUNT:
                until = None
            elif until and count:
                self.add_error('repeat_count', _('Give an end date or a number of times, not both.'))
            local = timezone.localtime(start)
            if until and until < local.date():
                self.add_error('repeat_until', _('The repeat cannot end before the start.'))
                until = None  # Keeps the model from failing on the same rule.
            weekdays = data.get('repeat_weekdays')
            if repeat == recurrence.WEEKLY and weekdays and recurrence.WEEKDAYS[local.weekday()] not in weekdays:
                self.add_error('repeat_weekdays', _('The start is on a {day}; tick it too, or move the start.').format(
                    day=date_format(local, 'l'),
                ))
            data['rrule'] = recurrence.build(
                repeat, start, data.get('repeat_interval') or 1, data.get('repeat_weekdays') or (), until, count,
            )
            self.instance.rrule = data['rrule']

        source = data.get('reminder_source') or REMINDERS_FROM_TYPE
        if source == REMINDERS_OWN:
            self.instance.reminders = self.picked_reminders()
            if not self.instance.reminders and 'reminder_presets' in self.fields:
                self.add_error('reminder_presets', _('Tick or type at least one time, or choose "No reminders".'))
        else:
            self.instance.reminders = None if source == REMINDERS_FROM_TYPE else []

        self.clean_targets()
        return data

    def save(self, *args, **kwargs):
        event = super().save(*args, **kwargs)
        self.save_targets(event)
        return event


class EventFilterForm(NetBoxModelFilterSetForm):
    model = Event
    fieldsets = (
        FieldSet('q', 'filter_id', 'tag'),
        FieldSet('event_type_id', 'status', 'impact', 'recurring', name=_('Event')),
        FieldSet('after', 'before', name=_('When')),
        FieldSet('owner_id', 'person_id', 'mine', name=_('People')),
    )
    event_type_id = DynamicModelMultipleChoiceField(
        queryset=EventType.objects.all(), required=False, label=_('Type'),
    )
    status = forms.MultipleChoiceField(choices=EventStatusChoices, required=False, label=_('Status'))
    impact = forms.MultipleChoiceField(choices=ImpactChoices, required=False, label=_('Impact'))
    recurring = forms.NullBooleanField(required=False, label=_('Repeating'), widget=forms.Select(
        choices=(('', '---------'), ('true', _('Yes')), ('false', _('No'))),
    ))
    after = forms.DateTimeField(required=False, label=_('Ends after'), widget=CalendarDateTimePicker())
    before = forms.DateTimeField(required=False, label=_('Starts before'), widget=CalendarDateTimePicker())
    owner_id = UserMultipleChoiceField(queryset=User.objects.all(), required=False, label=_('Owner'))
    person_id = UserMultipleChoiceField(queryset=User.objects.all(), required=False, label=_('Person'))
    mine = forms.NullBooleanField(required=False, label=_('Mine'), widget=forms.Select(
        choices=(('', '---------'), ('true', _('Owned by or including me'))),
    ))
    tag = TagFilterField(model)


class EventBulkEditForm(NetBoxModelBulkEditForm):
    model = Event
    event_type = DynamicModelChoiceField(queryset=EventType.objects.all(), required=False, label=_('Type'))
    status = forms.ChoiceField(choices=add_blank_choice(EventStatusChoices), required=False, label=_('Status'))
    impact = forms.ChoiceField(choices=add_blank_choice(ImpactChoices), required=False, label=_('Impact'))
    event_owner = UserChoiceField(queryset=User.objects.all(), required=False, label=_('Owner'))
    description = forms.CharField(required=False, max_length=200, label=_('Description'))
    comments = CommentField()

    fieldsets = (FieldSet('event_type', 'status', 'impact', 'event_owner', 'description'),)
    nullable_fields = ('event_owner', 'description', 'comments')


class EventImportForm(NetBoxModelImportForm):
    event_type = CSVModelChoiceField(
        queryset=EventType.objects.all(), to_field_name='name', label=_('Type'), help_text=_('Event type name'),
    )
    status = CSVChoiceField(choices=EventStatusChoices, required=False, label=_('Status'))
    impact = CSVChoiceField(choices=ImpactChoices, required=False, label=_('Impact'))
    event_owner = CSVModelChoiceField(
        queryset=User.objects.all(), to_field_name='username', required=False, label=_('Owner'),
        help_text=_('Username'),
    )

    class Meta:
        model = Event
        fields = (
            'title', 'event_type', 'status', 'impact', 'start', 'end', 'all_day', 'event_owner', 'description', 'rrule',
            'comments', 'tags',
        )


#
# The event page: link objects, add people
#

class LinkObjectsForm(LinkFieldsMixin, forms.Form):
    link_type = ContentTypeChoiceField(
        queryset=ContentType.objects.none(), label=_('Object type'), widget=HTMXSelect(),
    )
    link_objects = DynamicModelMultipleChoiceField(queryset=ContentType.objects.none(), label=_('Objects'))
    exclude = forms.BooleanField(
        required=False, label=_('Exclude'),
        help_text=_('Leave these out of the event, with the devices and VMs they cover. For devices, VMs and '
                    'what groups them (sites, roles, ...).'),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['link_type'].queryset = linkable_types()
        self.setup_link_fields()

    def clean(self):
        super().clean()
        content_type = self.cleaned_data.get('link_type')
        if self.cleaned_data.get('exclude') and content_type and type_label(content_type) not in EXCLUDABLE:
            self.add_error('exclude', _('Objects of this kind cannot be excluded.'))
        return self.cleaned_data


class AddPeopleForm(forms.Form):
    """
    Add people to an event in one role: any mix of users, groups and
    contacts. Add more people in another role with the same form again.
    """
    role = DynamicModelChoiceField(queryset=EventRole.objects.all(), label=_('Role'))
    users = UserMultipleChoiceField(queryset=User.objects.all(), required=False, label=_('Users'))
    groups = DynamicModelMultipleChoiceField(
        queryset=Group.objects.all(), required=False, label=_('Groups'),
        help_text=_('Everyone in the group is on the event.'),
    )
    contacts = DynamicModelMultipleChoiceField(
        queryset=Contact.objects.all(), required=False, label=_('Contacts'),
        help_text=_('People without a NetBox account; notified by e-mail.'),
    )

    fieldsets = (FieldSet('role', 'users', 'groups', 'contacts'),)

    def __init__(self, *args, editor=None, **kwargs):
        super().__init__(*args, **kwargs)
        # Picking users or groups needs permission to view them.
        if editor is not None and not editor.has_perm('users.view_user'):
            self.fields.pop('users')
        if editor is not None and not editor.has_perm('users.view_group'):
            self.fields.pop('groups')

    def clean(self):
        super().clean()
        if not any(self.cleaned_data.get(name) for name in ('users', 'groups', 'contacts')):
            raise ValidationError(_('Pick at least one user, group or contact.'))
        return self.cleaned_data

    def save(self, event):
        """Add the picked people; anyone already in the role is left as is. Returns how many were added."""
        role = self.cleaned_data['role']
        added = 0
        for field, kind in (('users', 'user'), ('groups', 'group'), ('contacts', 'contact')):
            for value in self.cleaned_data.get(field) or ():
                added += EventPerson.objects.get_or_create(event=event, role=role, **{kind: value})[1]
        return added


class EventPersonForm(forms.ModelForm):
    """Change one person on an event: their role, or who it is."""
    role = DynamicModelChoiceField(queryset=EventRole.objects.all(), label=_('Role'))
    user = UserChoiceField(queryset=User.objects.all(), required=False, label=_('User'))
    group = DynamicModelChoiceField(queryset=Group.objects.all(), required=False, label=_('Group'))
    contact = DynamicModelChoiceField(queryset=Contact.objects.all(), required=False, label=_('Contact'))

    fieldsets = (FieldSet('role', 'user', 'group', 'contact'),)

    class Meta:
        model = EventPerson
        fields = ('role', 'user', 'group', 'contact')


class OccurrenceForm(forms.Form):
    """
    Skip, move or bring back one occurrence of a repeating event. Every
    choice needs a reason; it is kept on the change, in the event's journal
    and in the notice. For an all-day event the times are days.
    """
    SKIP, MOVE, RESTORE = 'skip', 'move', 'restore'

    occurrence = forms.CharField(widget=forms.HiddenInput)
    action = forms.ChoiceField(label=_('Action'), widget=HTMXSelect())
    start = forms.DateTimeField(label=_('New start'), required=False, widget=CalendarDateTimePicker())
    end = forms.DateTimeField(label=_('New end'), required=False, widget=CalendarDateTimePicker())
    reason = forms.CharField(
        label=_('Reason'), max_length=200,
        help_text=_('Why. Shown on the event, in its journal and in the notice people get.'),
    )

    def __init__(self, event, original_start, *args, **kwargs):
        self.event = event
        self.original_start = original_start
        self.change = OccurrenceChange.objects.filter(event=event, original_start=original_start).first()
        if self.change is not None:
            # For the change log: the change as it was before this form.
            self.change.snapshot()
        super().__init__(*args, **kwargs)

        choices = [(self.SKIP, _('Skip this occurrence')), (self.MOVE, _('Move this occurrence'))]
        if self.change is not None:
            choices.append((self.RESTORE, _('Bring back to the original time')))
        self.fields['action'].choices = choices
        if event.all_day:
            self.fields['start'] = forms.DateField(
                label=_('New first day'), required=False, widget=CalendarDatePicker(),
            )
            self.fields['end'] = forms.DateField(
                label=_('New last day'), required=False, widget=CalendarDatePicker(),
            )

        if not self.is_bound:
            # Open with the occurrence as it is now.
            change = self.change
            start = change.start if change is not None and change.is_moved else original_start
            end = change.end if change is not None and change.is_moved else original_start + event.duration
            if event.all_day:
                start, end = timezone.localtime(start).date(), timezone.localtime(end - timedelta(seconds=1)).date()
            else:
                start, end = timezone.localtime(start), timezone.localtime(end)
            self.initial.setdefault('action', self.MOVE if change is not None and change.is_moved else self.SKIP)
            self.initial.setdefault('start', start)
            self.initial.setdefault('end', end)
            self.initial.setdefault('reason', change.reason if change is not None else '')

        # New times only matter when moving.
        if get_field_value(self, 'action') != self.MOVE:
            self.fields.pop('start')
            self.fields.pop('end')

    def _as_time(self, value):
        if isinstance(value, datetime) or value is None:
            return value
        # A day of an all-day event; OccurrenceChange.clean() rounds it to midnights.
        return timezone.make_aware(datetime.combine(value, time(12)), timezone.get_current_timezone())

    def clean(self):
        data = super().clean()
        if data.get('action') == self.MOVE:
            for name in ('start', 'end'):
                if not data.get(name):
                    self.add_error(name, _('Required to move the occurrence.'))
        if self.errors or data.get('action') == self.RESTORE:
            return data
        change = self.change or OccurrenceChange(event=self.event, original_start=self.original_start)
        change.reason = data['reason'].strip()
        if data['action'] == self.MOVE:
            change.start, change.end = self._as_time(data['start']), self._as_time(data['end'])
        else:
            change.start = change.end = None
        try:
            change.full_clean()
        except ValidationError as error:
            fields = {'start', 'end', 'reason'}
            for field, messages in error.message_dict.items():
                self.add_error(field if field in fields else None, messages)
        self.cleaned_change = change
        return data

    def save(self):
        """Store the choice. Returns the action taken."""
        reason = self.cleaned_data['reason'].strip()
        action = self.cleaned_data['action']
        if action == self.RESTORE:
            self.change._changelog_message = reason
            self.change.delete()
            return action
        change = self.cleaned_change
        change._changelog_message = reason
        change.save()
        return action


class CalendarFilterForm(forms.Form):
    """The filters above the month view and the timeline."""
    event_type_id = DynamicModelMultipleChoiceField(
        queryset=EventType.objects.all(), required=False, label=_('Type'),
    )
    status = forms.MultipleChoiceField(choices=EventStatusChoices, required=False, label=_('Status'))
    tag = TagFilterField(Event)
    mine = forms.NullBooleanField(required=False, label=_('Whose'), widget=forms.Select(
        choices=(('', _('Everyone')), ('true', _('Owned by or including me'))),
    ))


#
# Settings page
#

class SettingsForm(forms.Form):
    """One field per setting in plugin_settings.SETTINGS."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for setting in plugin_settings.SETTINGS:
            if setting.kind == plugin_settings.KIND_BOOL:
                field = forms.BooleanField(required=False)
            elif setting.kind == plugin_settings.KIND_CHOICE:
                field = forms.ChoiceField(choices=setting.choices)
            elif setting.kind == plugin_settings.KIND_TEMPLATE:
                field = forms.CharField(
                    required=False, strip=False, validators=[_valid_template],
                    # A subject is one line; a message gets room.
                    widget=forms.Textarea(attrs={
                        'rows': 2 if setting.key == 'email_subject' else 8, 'class': 'font-monospace',
                    }),
                )
            else:
                field = forms.CharField(required=False)
            field.label, field.help_text = setting.label, setting.help_text
            self.fields[setting.key] = field
            if not self.is_bound:
                self.initial[setting.key] = setting.normalize(plugin_settings.get_setting(setting.key))

    def stored_values(self):
        return {
            setting.key: setting.normalize(self.cleaned_data.get(setting.key))
            for setting in plugin_settings.SETTINGS
        }


def _valid_template(value):
    from .message_template import check

    try:
        check(value)
    except ValueError as error:
        raise ValidationError(_('This template has an error: {error}').format(error=error))
