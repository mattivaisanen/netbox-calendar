import base64
import hashlib

from django.conf import settings
from django.db import migrations, models


def _fernet():
    # The same key as sync/tokens.py, written out so this migration never changes with it.
    from cryptography.fernet import Fernet, MultiFernet

    keys = [settings.SECRET_KEY, *getattr(settings, 'SECRET_KEY_FALLBACKS', ())]
    return MultiFernet([
        Fernet(base64.urlsafe_b64encode(hashlib.sha256(b'netbox-calendar-sync-tokens' + key.encode()).digest()))
        for key in keys if key
    ])


def hide_tokens(apps, schema_editor):
    """Each clear text token becomes its hash (to find it) and its encrypted copy (to show it)."""
    FeedToken = apps.get_model('netbox_calendar', 'FeedToken')
    fernet = _fernet()
    for row in FeedToken.objects.all():
        row.token_hash = hashlib.sha256(row.token.encode()).hexdigest()
        row.token_encrypted = fernet.encrypt(row.token.encode()).decode()
        row.save(update_fields=('token_hash', 'token_encrypted'))


class Migration(migrations.Migration):
    """iCal feed tokens: stored as a hash and an encrypted copy, no longer in clear text."""

    dependencies = [
        ('netbox_calendar', '0007_calendarconnection_server'),
    ]

    operations = [
        migrations.AddField(
            model_name='feedtoken',
            name='token_hash',
            field=models.CharField(max_length=64, null=True),
        ),
        migrations.AddField(
            model_name='feedtoken',
            name='token_encrypted',
            field=models.TextField(default=''),
            preserve_default=False,
        ),
        # Existing feed addresses keep working. Going back would need the clear text: not supported.
        migrations.RunPython(hide_tokens, migrations.RunPython.noop),
        migrations.RemoveField(model_name='feedtoken', name='token'),
        migrations.AlterField(
            model_name='feedtoken',
            name='token_hash',
            field=models.CharField(max_length=64, unique=True),
        ),
    ]
