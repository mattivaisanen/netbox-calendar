import django.db.models.deletion
import netbox.models.deletion
from django.db import migrations, models

#: Reason given to occurrences skipped before reasons were asked for.
OLD_REASON = 'Skipped before reasons were recorded.'


def skipped_to_changes(apps, schema_editor):
    """Each skipped occurrence of an event becomes an OccurrenceChange."""
    Event = apps.get_model('netbox_calendar', 'Event')
    OccurrenceChange = apps.get_model('netbox_calendar', 'OccurrenceChange')
    OccurrenceChange.objects.bulk_create(
        OccurrenceChange(event=event, original_start=moment, reason=OLD_REASON)
        for event in Event.objects.exclude(skipped=[])
        for moment in sorted(set(event.skipped or ()))
    )


def changes_to_skipped(apps, schema_editor):
    """Going back: skipped occurrences return to the list; moved ones are lost."""
    Event = apps.get_model('netbox_calendar', 'Event')
    OccurrenceChange = apps.get_model('netbox_calendar', 'OccurrenceChange')
    skipped = {}
    for change in OccurrenceChange.objects.filter(start__isnull=True):
        skipped.setdefault(change.event_id, []).append(change.original_start)
    for event in Event.objects.filter(pk__in=skipped):
        event.skipped = sorted(skipped[event.pk])
        event.save(update_fields=['skipped'])


class Migration(migrations.Migration):
    """
    Single occurrences can be skipped or moved, always with a reason
    (OccurrenceChange). The event's list of skipped occurrences moves there.
    """

    dependencies = [
        ('netbox_calendar', '0004_eventobject_exclude'),
    ]

    operations = [
        migrations.CreateModel(
            name='OccurrenceChange',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('created', models.DateTimeField(auto_now_add=True, null=True)),
                ('last_updated', models.DateTimeField(auto_now=True, null=True)),
                ('original_start', models.DateTimeField(verbose_name='original start')),
                ('start', models.DateTimeField(blank=True, null=True, verbose_name='new start')),
                ('end', models.DateTimeField(blank=True, null=True, verbose_name='new end')),
                ('reason', models.CharField(max_length=200, verbose_name='reason')),
                (
                    'event',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name='occurrence_changes',
                        to='netbox_calendar.event',
                    ),
                ),
            ],
            options={
                'verbose_name': 'occurrence change',
                'verbose_name_plural': 'occurrence changes',
                'ordering': ('event', 'original_start'),
                'constraints': [
                    models.UniqueConstraint(
                        fields=('event', 'original_start'), name='netbox_calendar_occurrence_unique',
                    ),
                ],
            },
            bases=(netbox.models.deletion.DeleteMixin, models.Model),
        ),
        migrations.RunPython(skipped_to_changes, changes_to_skipped),
        migrations.RemoveField(model_name='event', name='skipped'),
    ]
