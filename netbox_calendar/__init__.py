"""
NetBox plugin for scheduled events: upgrades, planned downtime, planned
maintenance and any other event type, linked to the NetBox objects they
touch. See README.md and docs/.
"""
from netbox.plugins import PluginConfig

from . import event_types, message_template


class NetBoxCalendarConfig(PluginConfig):
    name = 'netbox_calendar'
    verbose_name = 'Calendar'
    description = (
        'Schedule upgrades, downtime, maintenance and other events on NetBox objects, '
        'with calendar views, reminders and an iCal feed.'
    )
    version = '0.1.1'
    author = 'Matti'
    base_url = 'calendar'
    # The oldest NetBox release the tests run on.
    min_version = '4.7.0'

    # Every key can also be changed on Calendar -> Settings (superusers),
    # except reminder_grace_minutes. See plugin_settings.py.
    default_settings = {
        # NetBox's address as users reach it (e.g. 'https://netbox.example.com'),
        # for full links in notifications, e-mails and the iCal feed. Empty:
        # links are paths only.
        'netbox_url': '',
        # NetBox notifications (the bell). Event Rules run either way.
        'notifications': True,
        # Send e-mail directly through NetBox's EMAIL settings, with the event
        # attached as a calendar invite. Each event type also has to allow it.
        'direct_email': False,
        # Personal iCal feed URLs (for Outlook, Google Calendar, phones).
        'ical_feeds': True,
        # Only the owner, people whose role may edit, superusers and users
        # with the extra "manage" action on events may change an event.
        # False: plain NetBox object permissions only.
        'restrict_editing': True,
        # Warn on an event page when another event overlaps on the same object.
        'conflict_warnings': True,
        # Show a banner on every page while a change freeze event is running.
        'freeze_banner': True,
        # Events on a site, location, rack or cluster also show on the
        # devices and virtual machines inside it.
        'scope_rollup': True,
        # Show scheduled change requests of netbox-change-approval on the
        # calendar, when that plugin is installed.
        'change_requests': True,
        # Move single events to "in progress" at their start and "done" at
        # their end.
        'auto_status': True,
        # First day of the week in the month view: 0 Monday ... 6 Sunday.
        'first_weekday': 0,
        # Template (Jinja2) of the notification text, {{ data.message }} for
        # webhooks. See message_template.py.
        'message': message_template.DEFAULT,
        # Templates (Jinja2) of the subject and body of direct e-mail. Same
        # variables as `message`.
        'email_subject': message_template.EMAIL_SUBJECT,
        'email_body': message_template.EMAIL_BODY,
        # A reminder or start notice the background job missed (worker
        # stopped, say) is still sent this many minutes late, never later.
        'reminder_grace_minutes': 60,
    }

    template_extensions = 'template_content.template_extensions'
    menu = 'navigation.menu'

    def ready(self):
        super().ready()

        from . import signals, widgets  # noqa: F401 (connects receivers, registers the widget)
        from . import jobs  # noqa: F401 (registers the reminder job)


# Before ready(): see event_types.py.
event_types.register()

config = NetBoxCalendarConfig
