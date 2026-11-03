from django.db import migrations, models


class Migration(migrations.Migration):
    """
    A link can exclude its object. Existing links stay included; how links
    of different types combine changed in code, not in the data.
    """

    dependencies = [
        ('netbox_calendar', '0003_eventperson_group'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='eventobject',
            options={
                'ordering': ('event', 'exclude', 'object_type', 'object_repr'),
                'verbose_name': 'event object', 'verbose_name_plural': 'event objects',
            },
        ),
        migrations.AddField(
            model_name='eventobject',
            name='exclude',
            field=models.BooleanField(default=False, verbose_name='exclude'),
        ),
    ]
