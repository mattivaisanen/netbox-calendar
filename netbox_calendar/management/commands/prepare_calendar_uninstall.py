"""
Remove what would break NetBox pages once this plugin is gone. Run it while
the plugin is still installed, right before taking it out of PLUGINS.

Many NetBox pages load the object behind each row they list. Once the
plugin's models are gone, a row pointing at an event (or an event type,
role, ...) can't be loaded and the whole page fails with a server error:
Notifications, Bookmarks, Subscriptions, Changelog, Journal Entries, and the
REST API lists behind them. A tag used on an event breaks that tag's page,
and a custom field offered on events breaks that field's page. A dashboard
with the Upcoming Events widget can't be loaded either.

So this deletes those rows, takes the plugin's object types off custom
fields, custom links, export templates, saved filters, tags, event rules and
permissions (deleting the ones that were only about this plugin), deletes
custom fields whose values are this plugin's objects, takes the plugin's
notices off event rules, removes the widget from dashboards, deletes the
plugin's search and job entries and cancels its queued background jobs.

Events and the plugin's own tables are left alone; `migrate netbox_calendar
zero` removes those.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Q

from core.choices import JobStatusChoices
from core.models import Job, ObjectChange
from extras.models import (
    Bookmark, CachedValue, CustomField, CustomLink, Dashboard, EventRule, ExportTemplate, ImageAttachment,
    JournalEntry, Notification, SavedFilter, Subscription, TableConfig, Tag, TaggedItem,
)
from netbox.registry import registry
from users.models import ObjectPermission

APP_LABEL = 'netbox_calendar'


def _is_ours(name):
    return str(name).startswith(f'{APP_LABEL}.')


def _widget_ids(dashboard):
    return [
        widget_id for widget_id, widget in dashboard.config.items()
        if isinstance(widget, dict) and _is_ours(widget.get('class', ''))
    ]


def _rows():
    """Rows in NetBox's own tables that point at this plugin's objects."""
    ours = {'object_type__app_label': APP_LABEL}
    return {
        'notifications': Notification.objects.filter(**ours),
        'bookmarks': Bookmark.objects.filter(**ours),
        'subscriptions': Subscription.objects.filter(**ours),
        'changelog entries': ObjectChange.objects.filter(
            Q(changed_object_type__app_label=APP_LABEL) | Q(related_object_type__app_label=APP_LABEL)
        ),
        'journal entries': JournalEntry.objects.filter(assigned_object_type__app_label=APP_LABEL),
        'tag assignments': TaggedItem.objects.filter(content_type__app_label=APP_LABEL),
        'image attachments': ImageAttachment.objects.filter(**ours),
        'saved table configs': TableConfig.objects.filter(**ours),
        'search index entries': CachedValue.objects.filter(**ours),
        # Queued ones are cancelled separately (_queued_jobs).
        'finished background jobs': Job.objects.filter(**ours).exclude(
            status__in=JobStatusChoices.ENQUEUED_STATE_CHOICES,
        ),
    }


def _object_type_users():
    """
    NetBox objects limited to object types, offered on any of this plugin's.
    Each gets this plugin's types removed; the ones left with no object type
    at all were only about this plugin and are deleted.
    """
    found = {}
    for label, model in (
        ('custom fields', CustomField), ('custom links', CustomLink), ('export templates', ExportTemplate),
        ('saved filters', SavedFilter), ('tags', Tag), ('event rules', EventRule), ('permissions', ObjectPermission),
    ):
        objects = list(model.objects.filter(object_types__app_label=APP_LABEL).distinct())
        if objects:
            found[label] = objects
    return found


def _only_ours(obj):
    return not obj.object_types.exclude(app_label=APP_LABEL).exists()


def _object_fields():
    """Custom fields whose values are this plugin's objects."""
    return list(CustomField.objects.filter(related_object_type__app_label=APP_LABEL))


def _event_rules_with_our_notices():
    """Event rules that fire on this plugin's notices ("Event reminder", ...)."""
    return [rule for rule in EventRule.objects.all() if any(_is_ours(name) for name in rule.event_types)]


def _queued_jobs():
    """This plugin's background jobs still waiting to run. Left queued, the worker would run code that is gone."""
    names = {
        getattr(job_class, 'name', None) for job_class in registry.get('system_jobs', {})
        if job_class.__module__.startswith(f'{APP_LABEL}.')
    } - {None}
    return list(Job.objects.filter(
        Q(object_type__app_label=APP_LABEL) | Q(name__in=names),
        status__in=JobStatusChoices.ENQUEUED_STATE_CHOICES,
    ))


class Command(BaseCommand):
    help = (
        "Delete NetBox rows that point at this plugin's objects (notifications, bookmarks, subscriptions, "
        'changelog and journal entries, tag assignments), take its object types off custom fields, tags, '
        'event rules, permissions and the like, and remove the Upcoming Events dashboard widget, so NetBox '
        'keeps working after the plugin is removed.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Only show what would be removed.')
        parser.add_argument('--noinput', '--no-input', action='store_false', dest='interactive',
                            help='Do not ask for confirmation.')

    def handle(self, *args, dry_run=False, interactive=True, **options):
        rows = _rows()
        users = _object_type_users()
        object_fields = _object_fields()
        event_rules = _event_rules_with_our_notices()
        jobs = _queued_jobs()
        dashboards = [dashboard for dashboard in Dashboard.objects.all() if _widget_ids(dashboard)]

        for name, queryset in rows.items():
            if count := queryset.count():
                self.stdout.write(f'{count} {name} for calendar objects')
        for label, objects in users.items():
            deleted = [str(obj) for obj in objects if _only_ours(obj)]
            kept = [str(obj) for obj in objects if not _only_ours(obj)]
            if kept:
                self.stdout.write(f'{label} to take calendar object types off: {", ".join(kept)}')
            if deleted:
                self.stdout.write(f'{label} only for calendar objects, to delete: {", ".join(deleted)}')
        if object_fields:
            names = ', '.join(str(field) for field in object_fields)
            self.stdout.write(f'custom fields pointing at calendar objects, to delete: {names}')
        if event_rules:
            names = ', '.join(str(rule) for rule in event_rules)
            self.stdout.write(f'event rules to take calendar notices off: {names}')
        if jobs:
            self.stdout.write(f'{len(jobs)} queued calendar background jobs')
        self.stdout.write(f'{len(dashboards)} dashboards with the Upcoming Events widget')

        if dry_run:
            return
        if interactive and input('Remove these? [y/N] ').strip().lower() not in ('y', 'yes'):
            self.stdout.write('Nothing removed.')
            return

        with transaction.atomic():
            for queryset in rows.values():
                queryset.delete()
            for objects in users.values():
                for obj in objects:
                    if _only_ours(obj):
                        obj.delete()
                    else:
                        obj.object_types.remove(*obj.object_types.filter(app_label=APP_LABEL))
            for field in object_fields:
                field.delete()
            for rule in event_rules:
                rule = EventRule.objects.filter(pk=rule.pk).first()  # May be deleted above
                if rule is None:
                    continue
                rule.event_types = [name for name in rule.event_types if not _is_ours(name)]
                if rule.event_types:
                    rule.save()
                else:
                    rule.delete()
            for dashboard in dashboards:
                for widget_id in _widget_ids(dashboard):
                    dashboard.delete_widget(widget_id)
                dashboard.save()
        for job in jobs:
            job.delete()  # Also takes it off the worker's queue; not undone by a rollback, so last.
        self.stdout.write(self.style.SUCCESS(
            'Done. You can now take netbox_calendar out of PLUGINS and restart NetBox.'
        ))
