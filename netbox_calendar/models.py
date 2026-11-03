"""
Event types, roles, events, and what an event is linked to.

EventType, EventRole and Event are NetBoxModels: change log, tags, custom
fields, REST API and Event Rules come with that. EventObject (an event's
link to any NetBox object) and EventPerson (a user or contact on an event,
with a role) are change-logged, their entries shown under the event.
FeedToken, SentNotice and PluginSetting are plain bookkeeping.
"""
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
from django.db import models
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from netbox.models import ChangeLoggedModel, NetBoxModel
from utilities.fields import ColorField

from . import recurrence
from .choices import EventStatusChoices, ImpactChoices, NoticeChoices


def default_notices():
    return [value for value, _label in NoticeChoices.CHOICES]


def default_reminders():
    # One day and one hour before the start, in minutes.
    return [1440, 60]


class EventType(NetBoxModel):
    """
    A kind of event (upgrade, planned downtime, ...): its color on the
    calendar, its default length, and who hears about its events and when.
    """
    name = models.CharField(verbose_name=_('name'), max_length=100, unique=True)
    slug = models.SlugField(verbose_name=_('slug'), max_length=100, unique=True)
    color = ColorField(verbose_name=_('color'), default='2196f3')
    icon = models.CharField(
        verbose_name=_('icon'), max_length=50, blank=True,
        help_text=_('A Material Design Icons name without the "mdi-" prefix, such as "wrench" or "update".'),
    )
    description = models.CharField(verbose_name=_('description'), max_length=200, blank=True)
    default_duration = models.PositiveIntegerField(
        verbose_name=_('default duration'), default=60,
        help_text=_('Minutes. A new event without an end lasts this long.'),
    )
    change_freeze = models.BooleanField(
        verbose_name=_('change freeze'), default=False,
        help_text=_('While an event of this type runs, every NetBox page shows a change freeze banner.'),
    )

    # Notifications. See notifications.py.
    notify_on = ArrayField(
        base_field=models.CharField(max_length=20, choices=NoticeChoices),
        verbose_name=_('notify on'), default=default_notices, blank=True,
        help_text=_('What people are told about. Empty: nothing.'),
    )
    reminders = ArrayField(
        base_field=models.PositiveIntegerField(),
        verbose_name=_('reminders'), default=default_reminders, blank=True,
        help_text=_('Minutes before the start. An event can set its own.'),
    )
    notify_owner = models.BooleanField(verbose_name=_('notify owner'), default=True)
    notify_people = models.BooleanField(
        verbose_name=_('notify people'), default=True,
        help_text=_('People on the event whose role is notified.'),
    )
    notify_group = models.ForeignKey(
        to='users.Group', verbose_name=_('notify group'), on_delete=models.SET_NULL,
        related_name='+', blank=True, null=True,
        help_text=_('Every member of this group, for every event of this type.'),
    )
    notify_subscribers = models.BooleanField(
        verbose_name=_('notify subscribers'), default=False,
        help_text=_('Users subscribed to a linked object (NetBox subscriptions).'),
    )
    notify_contacts = models.BooleanField(
        verbose_name=_('notify contacts'), default=False,
        help_text=_('Contacts assigned to a linked object, by e-mail only.'),
    )
    send_email = models.BooleanField(
        verbose_name=_('send e-mail'), default=False,
        help_text=_('Also e-mail the people notified, with a calendar invite. Needs direct e-mail turned on '
                    'in the plugin settings.'),
    )

    clone_fields = (
        'color', 'icon', 'default_duration', 'change_freeze', 'notify_on', 'reminders', 'notify_owner',
        'notify_people', 'notify_group', 'notify_subscribers', 'notify_contacts', 'send_email',
    )

    class Meta:
        ordering = ('name',)
        verbose_name = _('event type')
        verbose_name_plural = _('event types')

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('plugins:netbox_calendar:eventtype', args=[self.pk])


class EventRole(NetBoxModel):
    """
    What a person does on an event (technical contact, participant, ...),
    and whether that lets them edit it and get its notifications.
    """
    name = models.CharField(verbose_name=_('name'), max_length=100, unique=True)
    slug = models.SlugField(verbose_name=_('slug'), max_length=100, unique=True)
    can_edit = models.BooleanField(
        verbose_name=_('can edit'), default=False,
        help_text=_('Users with this role may edit the event, like its owner.'),
    )
    notify = models.BooleanField(
        verbose_name=_('notified'), default=True,
        help_text=_("People with this role get the event's notifications."),
    )
    weight = models.PositiveSmallIntegerField(
        verbose_name=_('weight'), default=100, help_text=_('Lower first when listed on an event.'),
    )
    description = models.CharField(verbose_name=_('description'), max_length=200, blank=True)

    class Meta:
        ordering = ('weight', 'name')
        verbose_name = _('event role')
        verbose_name_plural = _('event roles')

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('plugins:netbox_calendar:eventrole', args=[self.pk])


class Event(NetBoxModel):
    """
    One scheduled event, or a series when `rrule` is set. A series is one
    row: occurrences are worked out when shown (recurrence.py), and
    `skipped` holds the start times of occurrences that were called off.

    All-day events start at local midnight and end at the midnight after
    their last day (exclusive), so overlaps and the iCal feed need no
    special cases.
    """
    title = models.CharField(verbose_name=_('title'), max_length=200)
    event_type = models.ForeignKey(
        to=EventType, verbose_name=_('type'), on_delete=models.PROTECT, related_name='events',
    )
    status = models.CharField(
        verbose_name=_('status'), max_length=20, choices=EventStatusChoices, default=EventStatusChoices.PLANNED,
    )
    impact = models.CharField(
        verbose_name=_('impact'), max_length=20, choices=ImpactChoices, default=ImpactChoices.NONE,
    )
    start = models.DateTimeField(verbose_name=_('start'))
    end = models.DateTimeField(verbose_name=_('end'))
    all_day = models.BooleanField(verbose_name=_('all day'), default=False)
    event_owner = models.ForeignKey(
        to=settings.AUTH_USER_MODEL, verbose_name=_('event_owner'), on_delete=models.SET_NULL,
        related_name='+', blank=True, null=True,
    )
    description = models.CharField(verbose_name=_('description'), max_length=200, blank=True)
    comments = models.TextField(verbose_name=_('comments'), blank=True)

    rrule = models.CharField(
        verbose_name=_('recurrence'), max_length=500, blank=True,
        help_text=_('iCal RRULE, such as FREQ=MONTHLY;BYDAY=2TU. Empty: happens once.'),
    )
    skipped = ArrayField(
        base_field=models.DateTimeField(), verbose_name=_('skipped occurrences'), default=list, blank=True,
    )
    reminders = ArrayField(
        base_field=models.PositiveIntegerField(), verbose_name=_('reminders'), blank=True, null=True,
        help_text=_("Minutes before the start. Empty: the event type's reminders."),
    )

    # Objects picked by a filter instead of one by one ("every access
    # switch"), worked out when used, so new matching objects join in.
    filter_object_type = models.ForeignKey(
        to='contenttypes.ContentType', verbose_name=_('filter object type'), on_delete=models.SET_NULL,
        related_name='+', blank=True, null=True,
    )
    filter_query = models.CharField(
        verbose_name=_('filter'), max_length=1000, blank=True,
        help_text=_('Filter of an object list, as in its address after "?", such as role=core&status=active.'),
    )

    clone_fields = ('event_type', 'status', 'impact', 'all_day', 'event_owner', 'rrule', 'reminders')

    class Meta:
        ordering = ('start', 'pk')
        verbose_name = _('event')
        verbose_name_plural = _('events')
        indexes = (models.Index(fields=('start', 'end')),)

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        return reverse('plugins:netbox_calendar:event', args=[self.pk])

    def get_status_color(self):
        return EventStatusChoices.colors.get(self.status)

    def get_impact_color(self):
        return ImpactChoices.colors.get(self.impact)

    def clean(self):
        super().clean()
        if self.start and self.all_day:
            self.start, self.end = all_day_bounds(self.start, self.end or self.start)
        if self.start and self.end and self.end <= self.start:
            raise ValidationError({'end': _('The end must be after the start.')})
        if self.rrule:
            try:
                self.rrule = recurrence.normalize(self.rrule, self.start)
            except ValueError as error:
                raise ValidationError({'rrule': str(error)})
        if bool(self.filter_object_type_id) != bool(self.filter_query.strip()):
            raise ValidationError({'filter_query': _('Give both the object type and the filter, or neither.')})
        if self.filter_object_type_id:
            from .lookup import filter_error  # Not at the top: lookup imports this module.
            if error := filter_error(self.filter_object_type.model_class(), self.filter_query):
                raise ValidationError({'filter_query': error})

    @property
    def duration(self):
        return self.end - self.start

    @property
    def duration_text(self):
        """The length as "2 d 1 h 30 min"."""
        minutes = int(self.duration.total_seconds() // 60)
        days, minutes = divmod(minutes, 1440)
        hours, minutes = divmod(minutes, 60)
        parts = [f'{days} d' if days else '', f'{hours} h' if hours else '', f'{minutes} min' if minutes else '']
        return ' '.join(part for part in parts if part) or '0 min'

    @property
    def is_recurring(self):
        return bool(self.rrule)

    @property
    def last_day(self):
        """The last day of an all-day event (its end is the midnight after)."""
        return timezone.localtime(self.end - timedelta(seconds=1)).date()

    def effective_reminders(self):
        """Minutes before each start that reminders go out."""
        return self.reminders if self.reminders is not None else self.event_type.reminders

    def recurrence_text(self):
        """The recurrence in words ("Every month on the 2nd Tuesday")."""
        return recurrence.describe(self.rrule, self.start) if self.rrule else ''

    def occurrences(self, after, before):
        """recurrence.Occurrence objects of this event that overlap [after, before)."""
        return recurrence.occurrences(self, after, before)

    def next_occurrence(self, now=None):
        """The occurrence running now or next, or None."""
        now = now or timezone.now()
        found = recurrence.occurrences(self, now, now + timedelta(days=3660), limit=1)
        return found[0] if found else None


def all_day_bounds(start, end):
    """
    (start, end) of an all-day event: local midnight of the first day, and
    the midnight after the last day. An end already at midnight after the
    start is kept, so saving an event twice doesn't move it.
    """
    zone = timezone.get_current_timezone()
    first = timezone.localtime(start, zone).date()
    end_local = timezone.localtime(end, zone)
    last = end_local.date()
    if end_local.time() == time(0) and last > first:
        last -= timedelta(days=1)
    return (
        timezone.make_aware(datetime.combine(first, time(0)), zone),
        timezone.make_aware(datetime.combine(last + timedelta(days=1), time(0)), zone),
    )


class EventObject(ChangeLoggedModel):
    """An event's link to any NetBox object (a device, site, circuit, ...)."""
    event = models.ForeignKey(to=Event, on_delete=models.CASCADE, related_name='links')
    object_type = models.ForeignKey(to='contenttypes.ContentType', on_delete=models.CASCADE, related_name='+')
    object_id = models.PositiveBigIntegerField()
    object = GenericForeignKey(ct_field='object_type', fk_field='object_id')
    # Kept so a link to an object deleted since still says what it was.
    object_repr = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ('event', 'object_type', 'object_repr')
        verbose_name = _('event object')
        verbose_name_plural = _('event objects')
        constraints = (
            models.UniqueConstraint(fields=('event', 'object_type', 'object_id'), name='netbox_calendar_link_unique'),
        )
        indexes = (models.Index(fields=('object_type', 'object_id')),)

    def __str__(self):
        return f'{self.event}: {self.object_repr or self.object_id}'

    def get_absolute_url(self):
        return self.event.get_absolute_url()

    def save(self, *args, **kwargs):
        if self.object is not None:
            self.object_repr = str(self.object)[:200]
        super().save(*args, **kwargs)

    def to_objectchange(self, action):
        # Listed in the event's change log.
        objectchange = super().to_objectchange(action)
        objectchange.related_object = self.event
        return objectchange


class EventPerson(ChangeLoggedModel):
    """A NetBox user or contact on an event, with a role."""
    event = models.ForeignKey(to=Event, on_delete=models.CASCADE, related_name='people')
    role = models.ForeignKey(to=EventRole, verbose_name=_('role'), on_delete=models.PROTECT, related_name='+')
    user = models.ForeignKey(
        to=settings.AUTH_USER_MODEL, verbose_name=_('user'), on_delete=models.CASCADE, related_name='+',
        blank=True, null=True,
    )
    contact = models.ForeignKey(
        to='tenancy.Contact', verbose_name=_('contact'), on_delete=models.CASCADE, related_name='+',
        blank=True, null=True, help_text=_('Someone without a NetBox account. Notified by e-mail only.'),
    )

    class Meta:
        ordering = ('event', 'role__weight', 'role__name', 'pk')
        verbose_name = _('event person')
        verbose_name_plural = _('event people')
        constraints = (
            models.CheckConstraint(
                condition=models.Q(user__isnull=False, contact__isnull=True)
                | models.Q(user__isnull=True, contact__isnull=False),
                name='netbox_calendar_person_user_or_contact',
            ),
        )

    def __str__(self):
        return f'{self.person_name} ({self.role})'

    def get_absolute_url(self):
        return self.event.get_absolute_url()

    @property
    def person_name(self):
        if self.user_id:
            return self.user.get_full_name() or self.user.username
        return str(self.contact)

    @property
    def email(self):
        return (self.user.email if self.user_id else self.contact.email) or ''

    def clean(self):
        super().clean()
        if bool(self.user_id) == bool(self.contact_id):
            raise ValidationError(_('Pick a user or a contact.'))

    def to_objectchange(self, action):
        objectchange = super().to_objectchange(action)
        objectchange.related_object = self.event
        return objectchange


class FeedToken(models.Model):
    """The secret in a user's iCal feed URL. Resetting it makes a new one."""
    user = models.OneToOneField(to=settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='+')
    token = models.CharField(max_length=64, unique=True)
    created = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'Feed of {self.user}'


class SentNotice(models.Model):
    """
    A reminder, start or end notice the background job has sent, so it is
    sent once (jobs.py). Pruned after a month.
    """
    event = models.ForeignKey(to=Event, on_delete=models.CASCADE, related_name='+')
    occurrence = models.DateTimeField()
    kind = models.CharField(max_length=20, choices=NoticeChoices)
    # Minutes before the start, for reminders. 0 otherwise.
    offset = models.PositiveIntegerField(default=0)
    sent = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = (
            models.UniqueConstraint(
                fields=('event', 'occurrence', 'kind', 'offset'), name='netbox_calendar_notice_unique',
            ),
        )

    def __str__(self):
        return f'{self.kind} {self.event_id} {self.occurrence}'


class PluginSetting(models.Model):
    """A setting saved on the Settings page, overriding configuration.py
    until reset. See plugin_settings.py."""
    key = models.CharField(max_length=100, unique=True)
    value = models.JSONField(null=True, blank=True)
    updated = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        to=settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name='+',
    )

    class Meta:
        ordering = ('key',)

    def __str__(self):
        return self.key
