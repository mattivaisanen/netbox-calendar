"""Starting event types and roles. Admins can rename, change or delete them."""
from django.db import migrations

EVENT_TYPES = (
    # name, slug, color, icon, default duration (minutes), change freeze
    ('Upgrade', 'upgrade', '2196f3', 'update', 120, False),
    ('Planned downtime', 'planned-downtime', 'f44336', 'power-plug-off', 60, False),
    ('Planned maintenance', 'planned-maintenance', 'ff9800', 'wrench', 120, False),
    ('Change freeze', 'change-freeze', '00bcd4', 'snowflake', 1440, True),
)

ROLES = (
    # name, slug, can edit, notified, weight
    ('Technical contact', 'technical-contact', True, True, 10),
    ('Participant', 'participant', False, True, 20),
    ('Observer', 'observer', False, True, 30),
)


def create(apps, schema_editor):
    EventType = apps.get_model('netbox_calendar', 'EventType')
    EventRole = apps.get_model('netbox_calendar', 'EventRole')
    for name, slug, color, icon, duration, freeze in EVENT_TYPES:
        EventType.objects.get_or_create(slug=slug, defaults={
            'name': name, 'color': color, 'icon': icon, 'default_duration': duration, 'change_freeze': freeze,
        })
    for name, slug, can_edit, notify, weight in ROLES:
        EventRole.objects.get_or_create(slug=slug, defaults={
            'name': name, 'can_edit': can_edit, 'notify': notify, 'weight': weight,
        })


class Migration(migrations.Migration):
    dependencies = [('netbox_calendar', '0001_initial')]

    # Nothing to undo: removing the plugin's tables removes these rows too.
    operations = [migrations.RunPython(create, migrations.RunPython.noop)]
