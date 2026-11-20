import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    """Outside calendar connections (Outlook, ...) and the events pushed to them."""

    dependencies = [
        ('netbox_calendar', '0005_occurrencechange'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CalendarConnection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('provider', models.CharField(max_length=30, verbose_name='provider')),
                ('account', models.CharField(blank=True, max_length=255, verbose_name='account')),
                ('calendar_id', models.CharField(blank=True, max_length=500)),
                (
                    'scope',
                    models.CharField(
                        choices=[('mine', 'Events I own or am on'), ('all', 'All events I can see')],
                        default='mine', max_length=10, verbose_name='events',
                    ),
                ),
                ('refresh_token', models.TextField(blank=True)),
                ('access_token', models.TextField(blank=True)),
                ('access_expires', models.DateTimeField(blank=True, null=True)),
                (
                    'status',
                    models.CharField(
                        choices=[('active', 'Active'), ('expired', 'Sign-in expired'), ('error', 'Error')],
                        default='active', max_length=10, verbose_name='status',
                    ),
                ),
                ('last_sync', models.DateTimeField(blank=True, null=True, verbose_name='last sync')),
                ('last_error', models.TextField(blank=True, verbose_name='last error')),
                ('created', models.DateTimeField(auto_now_add=True)),
                (
                    'user',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name='+', to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'ordering': ('user__username', 'provider'),
                'constraints': [
                    models.UniqueConstraint(fields=('user', 'provider'), name='netbox_calendar_connection_unique'),
                ],
            },
        ),
        migrations.CreateModel(
            name='SyncedEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('event_id', models.PositiveBigIntegerField()),
                ('remote_id', models.CharField(blank=True, max_length=500)),
                ('extra', models.JSONField(blank=True, default=dict)),
                ('fingerprint', models.CharField(blank=True, max_length=64)),
                ('synced', models.DateTimeField(auto_now=True)),
                (
                    'connection',
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE, related_name='synced',
                        to='netbox_calendar.calendarconnection',
                    ),
                ),
            ],
            options={
                'ordering': ('connection', 'event_id'),
                'constraints': [
                    models.UniqueConstraint(fields=('connection', 'event_id'), name='netbox_calendar_synced_unique'),
                ],
            },
        ),
    ]
