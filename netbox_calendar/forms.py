import re
from datetime import datetime, time, timedelta

from django import forms
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.utils import timezone
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
from .models import Event, EventObject, EventPerson, EventRole, EventType

User = get_user_model()

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
        label=_('Before the start'), widget=forms.CheckboxSelectMultiple,
    )


def reminder_other_field():
    """A text box for any reminder time not among the tick boxes."""
    return forms.CharField(
        required=False, label=_('Other times'),
        help_text=_('Any other times before the start, such as "3d, 45m" (w, d, h, m).'),
    )


def linkable_types():
    """Object types events can be linked to: NetBox objects, this plugin's own left out."""
    return ContentType.objects.filter(
        pk__in=ObjectType.objects.with_feature('bookmarks').exclude(app_label='netbox_calendar').values('pk'),
    ).order_by('app_label', 'model')


class LinkFieldsMixin:
    """
    Two fields to link objects of one type: the type, and the objects. A
    new type re-renders the form (HTMX), so the object list searches that
    type.
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
        """Link the picked objects to `event` (already saved)."""
        content_type = self.cleaned_data.get('link_type')
        for obj in self.cleaned_data.get('link_objects') or ():
            EventObject.objects.get_or_create(
                event=event, object_type=content_type, object_id=obj.pk, defaults={'object_repr': str(obj)[:200]},
            )


#
# Event types and roles
#

class EventTypeForm(ReminderFieldsMixin, NetBoxModelForm):
    slug = SlugField()
    color = ColorField(label=_('Color'))
    notify_on = forms.MultipleChoiceField(
        choices=NoticeChoices, required=False, label=_('Notify on'), widget=forms.CheckboxSelectMultiple,
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

class EventForm(ReminderFieldsMixin, LinkFieldsMixin, NetBoxModelForm):
    """
    Add or edit an event. Besides the event's own fields it links objects
    of one type and adds people with one role on save; the event page
    manages the rest. Repeat fields build the iCal rule (recurrence.build()).
    """
    event_type = DynamicModelChoiceField(queryset=EventType.objects.all(), label=_('Type'))
    event_owner = DynamicModelChoiceField(
        queryset=User.objects.all(), required=False, label=_('Owner'),
        help_text=_('Empty on a new event: you.'),
    )
    start = forms.DateTimeField(label=_('Start'), widget=DateTimePicker())
    end = forms.DateTimeField(
        label=_('End'), required=False, widget=DateTimePicker(),
        help_text=_("Empty: the type's default duration. All-day events: the last day."),
    )

    repeat = forms.ChoiceField(
        choices=REPEAT_CHOICES, required=False, label=_('Repeat'),
        widget=HTMXSelect(hx_target_id='repeat_fields'),
    )
    repeat_interval = forms.IntegerField(
        min_value=1, required=False, initial=1, label=_('Every'),
        help_text=_('1 every time, 2 every other time, and so on.'),
    )
    repeat_weekdays = forms.MultipleChoiceField(
        choices=WEEKDAY_CHOICES, required=False, label=_('On'), widget=forms.CheckboxSelectMultiple,
        help_text=_("None picked: the start's weekday."),
    )
    repeat_until = forms.DateField(required=False, label=_('Until'), widget=DatePicker())
    repeat_count = forms.IntegerField(
        min_value=1, required=False, label=_('Times'), help_text=_('Instead of an end date: how many times.'),
    )
    reminder_source = forms.ChoiceField(
        choices=REMINDER_SOURCE_CHOICES, required=False, initial=REMINDERS_FROM_TYPE, label=_('Reminders'),
        widget=HTMXSelect(hx_target_id='reminder_fields'),
    )
    reminder_presets = reminder_preset_field()
    reminder_other = reminder_other_field()

    link_type = ContentTypeChoiceField(
        queryset=ContentType.objects.none(), required=False, label=_('Link objects of type'),
        widget=HTMXSelect(hx_target_id='link_fields'),
    )
    link_objects = DynamicModelMultipleChoiceField(queryset=ContentType.objects.none(), required=False)
    filter_object_type = ContentTypeChoiceField(
        queryset=ContentType.objects.none(), required=False, label=_('Filter object type'),
    )

    people_role = DynamicModelChoiceField(queryset=EventRole.objects.all(), required=False, label=_('Role'))
    people_users = DynamicModelMultipleChoiceField(queryset=User.objects.all(), required=False, label=_('Users'))
    people_contacts = DynamicModelMultipleChoiceField(
        queryset=Contact.objects.all(), required=False, label=_('Contacts'),
        help_text=_('People without a NetBox account; notified by e-mail.'),
    )
    comments = CommentField()

    fieldsets = (
        FieldSet('title', 'event_type', 'status', 'impact', 'event_owner', 'description', 'tags', name=_('Event')),
        FieldSet('start', 'end', 'all_day', name=_('When')),
        FieldSet('repeat', 'repeat_interval', 'repeat_weekdays', 'repeat_until', 'repeat_count', 'rrule',
                 name=_('Repeat'), html_id='repeat_fields'),
        FieldSet('link_type', 'link_objects', 'filter_object_type', 'filter_query', name=_('Objects'),
                 html_id='link_fields'),
        FieldSet('people_role', 'people_users', 'people_contacts', name=_('Add people')),
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
        self.fields['link_type'].queryset = linkable_types()
        self.fields['filter_object_type'].queryset = linkable_types()
        self.setup_link_fields()
        instance = self.instance

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

        # Picking users needs permission to view them; without it the
        # owner stays as it is (yourself on a new event).
        editor = getattr(instance, '_calendar_editor', None)
        if editor is not None and not editor.has_perm('users.view_user'):
            for name in ('event_owner', 'people_users'):
                self.fields.pop(name, None)

    def clean(self):
        super().clean()
        data = self.cleaned_data
        start, end, all_day = data.get('start'), data.get('end'), data.get('all_day')
        event_type = data.get('event_type')
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
            data['rrule'] = recurrence.build(
                repeat, start, data.get('repeat_interval') or 1, data.get('repeat_weekdays') or (),
                data.get('repeat_until'), data.get('repeat_count'),
            )
            self.instance.rrule = data['rrule']
            if data.get('repeat_until') and data.get('repeat_count'):
                self.add_error('repeat_count', _('Give an end date or a number of times, not both.'))

        source = data.get('reminder_source') or REMINDERS_FROM_TYPE
        if source == REMINDERS_OWN:
            self.instance.reminders = self.picked_reminders()
            if not self.instance.reminders and 'reminder_presets' in self.fields:
                self.add_error('reminder_presets', _('Tick or type at least one time, or choose "No reminders".'))
        else:
            self.instance.reminders = None if source == REMINDERS_FROM_TYPE else []

        if data.get('link_type') and not data.get('link_objects') and 'link_objects' in self.fields:
            self.add_error('link_objects', _('Pick the objects to link, or clear the type.'))
        if (data.get('people_users') or data.get('people_contacts')) and not data.get('people_role'):
            self.add_error('people_role', _('Pick the role of the people added.'))
        return data

    def save(self, *args, **kwargs):
        event = super().save(*args, **kwargs)
        self.save_links(event)
        role = self.cleaned_data.get('people_role')
        if role is not None:
            for user in self.cleaned_data.get('people_users') or ():
                EventPerson.objects.get_or_create(event=event, role=role, user=user)
            for contact in self.cleaned_data.get('people_contacts') or ():
                EventPerson.objects.get_or_create(event=event, role=role, contact=contact)
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
    after = forms.DateTimeField(required=False, label=_('Ends after'), widget=DateTimePicker())
    before = forms.DateTimeField(required=False, label=_('Starts before'), widget=DateTimePicker())
    owner_id = DynamicModelMultipleChoiceField(queryset=User.objects.all(), required=False, label=_('Owner'))
    person_id = DynamicModelMultipleChoiceField(queryset=User.objects.all(), required=False, label=_('Person'))
    mine = forms.NullBooleanField(required=False, label=_('Mine'), widget=forms.Select(
        choices=(('', '---------'), ('true', _('Owned by or including me'))),
    ))
    tag = TagFilterField(model)


class EventBulkEditForm(NetBoxModelBulkEditForm):
    model = Event
    event_type = DynamicModelChoiceField(queryset=EventType.objects.all(), required=False, label=_('Type'))
    status = forms.ChoiceField(choices=add_blank_choice(EventStatusChoices), required=False, label=_('Status'))
    impact = forms.ChoiceField(choices=add_blank_choice(ImpactChoices), required=False, label=_('Impact'))
    event_owner = DynamicModelChoiceField(queryset=User.objects.all(), required=False, label=_('Owner'))
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['link_type'].queryset = linkable_types()
        self.setup_link_fields()


class EventPersonForm(forms.ModelForm):
    role = DynamicModelChoiceField(queryset=EventRole.objects.all(), label=_('Role'))
    user = DynamicModelChoiceField(queryset=User.objects.all(), required=False, label=_('User'))
    contact = DynamicModelChoiceField(queryset=Contact.objects.all(), required=False, label=_('Contact'))

    fieldsets = (FieldSet('role', 'user', 'contact'),)

    class Meta:
        model = EventPerson
        fields = ('role', 'user', 'contact')


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
                    widget=forms.Textarea(attrs={'rows': 8, 'class': 'font-monospace'}),
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
