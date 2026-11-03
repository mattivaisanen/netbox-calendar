import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    """A user group can be on an event, like a user or a contact."""

    dependencies = [
        ('netbox_calendar', '0002_default_types_and_roles'),
        ('users', '0016_default_ordering_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='eventperson',
            name='group',
            field=models.ForeignKey(
                blank=True, null=True, help_text='Everyone in the group is on the event.',
                on_delete=django.db.models.deletion.CASCADE, related_name='+', to='users.group',
                verbose_name='group',
            ),
        ),
        migrations.RemoveConstraint(
            model_name='eventperson',
            name='netbox_calendar_person_user_or_contact',
        ),
        migrations.AddConstraint(
            model_name='eventperson',
            constraint=models.CheckConstraint(
                condition=models.Q(
                    models.Q(('contact__isnull', True), ('group__isnull', True), ('user__isnull', False)),
                    models.Q(('contact__isnull', True), ('group__isnull', False), ('user__isnull', True)),
                    models.Q(('contact__isnull', False), ('group__isnull', True), ('user__isnull', True)),
                    _connector='OR',
                ),
                name='netbox_calendar_person_one_of',
            ),
        ),
    ]
