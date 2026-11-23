"""
Remove journal entries left pointing at deleted events.

Deleting an event that had a skipped or moved occurrence used to write a
"back on" journal entry while the event was being deleted. The entry
outlived the event and its page failed to open. The signal is fixed; this
removes the entries it already left behind.
"""
from django.db import migrations


def remove_orphans(apps, schema_editor):
    ContentType = apps.get_model('contenttypes', 'ContentType')
    JournalEntry = apps.get_model('extras', 'JournalEntry')
    Event = apps.get_model('netbox_calendar', 'Event')

    event_type = ContentType.objects.filter(app_label='netbox_calendar', model='event').first()
    if event_type is None:
        return
    JournalEntry.objects.filter(assigned_object_type=event_type).exclude(
        assigned_object_id__in=Event.objects.values('pk'),
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ('netbox_calendar', '0007_calendarconnection_server'),
        ('extras', '0144_customfield_status'),
    ]

    # Nothing to undo: the removed entries belonged to events that are gone.
    operations = [migrations.RunPython(remove_orphans, migrations.RunPython.noop)]
