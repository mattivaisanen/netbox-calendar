from django.db import migrations, models


class Migration(migrations.Migration):
    """CalDAV connections: the server address, and whether NetBox made the calendar."""

    dependencies = [
        ('netbox_calendar', '0006_calendar_sync'),
    ]

    operations = [
        migrations.AddField(
            model_name='calendarconnection',
            name='server',
            field=models.CharField(blank=True, max_length=500, verbose_name='server'),
        ),
        migrations.AddField(
            model_name='calendarconnection',
            name='own_calendar',
            field=models.BooleanField(default=True),
        ),
    ]
