"""
Plugin settings superusers can change in NetBox (Calendar -> Settings).

configuration.py (PLUGINS_CONFIG['netbox_calendar']) gives every setting
its value. A value saved on the Settings page overrides it until someone
presses "Use configuration.py" next to it. Saving a value equal to the
configuration.py value removes the override, so the page only stores what
differs.

Saved values are PluginSetting rows, read at most once per web request and
never cached at startup, so a change applies at once in every NetBox
process and in the background worker, without a restart.
"""
from dataclasses import dataclass, field

from django.apps import apps
from django.db import connection
from django.utils.translation import gettext_lazy as _

from netbox.context import current_request
from netbox.plugins.utils import get_plugin_config

from . import message_template
from .message_template import clean
from .models import PluginSetting

APP = 'netbox_calendar'

#: Attribute on the current request that holds this request's saved values.
_MEMO_ATTR = '_netbox_calendar_settings'

KIND_BOOL = 'bool'
KIND_CHOICE = 'choice'
KIND_TEXT = 'text'
KIND_TEMPLATE = 'template'


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    help_text: str
    kind: str
    choices: tuple = field(default=())
    #: For a template: the text used when it is left empty.
    default: str = message_template.DEFAULT

    def normalize(self, value):
        """The value as the plugin uses it, so equal meanings compare equal."""
        if self.kind == KIND_BOOL:
            return value is not False
        if self.kind == KIND_CHOICE:
            keys = [key for key, _label in self.choices]
            value = str(value) if value is not None else None
            return value if value in keys else keys[0]
        if self.kind == KIND_TEMPLATE:
            return clean(value).strip() or self.default
        return (value or '').strip()


WEEKDAY_CHOICES = (
    ('0', _('Monday')), ('1', _('Tuesday')), ('2', _('Wednesday')), ('3', _('Thursday')),
    ('4', _('Friday')), ('5', _('Saturday')), ('6', _('Sunday')),
)

#: The variables every message template can use (message_template.py).
VARIABLES = ('notice, title, event_type, status, impact, start, end, when, objects, owner, by, link, comment, '
             'occurrence, reason')

SETTINGS = (
    Setting(
        'notifications', _('Notifications'),
        _('NetBox notifications (the bell) about events. Off: none; Event Rules still run.'),
        KIND_BOOL,
    ),
    Setting(
        'direct_email', _('Direct e-mail'),
        _("E-mail the people notified through NetBox's EMAIL settings, with a calendar invite attached, "
          'Each event type must also have "Send e-mail" on (Calendar -> Event types).'),
        KIND_BOOL,
    ),
    Setting(
        'ical_feeds', _('iCal feeds'),
        _('Personal calendar feed addresses for Outlook, Google Calendar and phones. Off: the feeds stop working.'),
        KIND_BOOL,
    ),
    Setting(
        'restrict_editing', _('Restrict editing'),
        _('Only the owner, people whose role can edit, superusers and users with the "manage" action may change '
          'an event. Off: NetBox permissions only.'),
        KIND_BOOL,
    ),
    Setting(
        'conflict_warnings', _('Conflict warnings'),
        _('Warn on an event page when another event overlaps on the same object.'),
        KIND_BOOL,
    ),
    Setting(
        'freeze_banner', _('Change freeze banner'),
        _('Show a banner on every page while an event of a change freeze type runs.'),
        KIND_BOOL,
    ),
    Setting(
        'scope_rollup', _('Scope roll-up'),
        _('Events on a site, region, location, rack, cluster, device role, device type, platform or tenant also '
          'concern its devices and virtual machines.'),
        KIND_BOOL,
    ),
    Setting(
        'change_requests', _('Change requests on the calendar'),
        _('Show scheduled change requests of netbox-change-approval, when it is installed.'),
        KIND_BOOL,
    ),
    Setting(
        'auto_status', _('Automatic status'),
        _('Single events move to "In progress" at their start and "Done" at their end.'),
        KIND_BOOL,
    ),
    Setting(
        'first_weekday', _('First day of the week'),
        _('The first column of the month view.'),
        KIND_CHOICE, choices=WEEKDAY_CHOICES,
    ),
    Setting(
        'netbox_url', _('NetBox address'),
        _("NetBox's address as users reach it, such as https://netbox.example.com, for full links in "
          'notifications, e-mails and the iCal feed. Empty: links are paths only.'),
        KIND_TEXT,
    ),
    Setting(
        'message', _('Notification message'),
        _('The text webhooks get as {{ data.message }}, in Jinja2. Variables: ' + VARIABLES + '. '
          'Empty: the default text.'),
        KIND_TEMPLATE,
    ),
    Setting(
        'email_subject', _('E-mail subject'),
        _('The subject of direct e-mail, in Jinja2, on one line. Same variables as the notification message. '
          'Empty: the default subject.'),
        KIND_TEMPLATE, default=message_template.EMAIL_SUBJECT,
    ),
    Setting(
        'email_body', _('E-mail body'),
        _('The plain text body of direct e-mail, in Jinja2. Same variables as the notification message. '
          'Empty: the default text.'),
        KIND_TEMPLATE, default=message_template.EMAIL_BODY,
    ),
)

BY_KEY = {setting.key: setting for setting in SETTINGS}

#: Whether the PluginSetting table exists yet (code can run before `migrate`).
_table_ready = False


def config_value(key, default=None):
    """The configuration.py value (or the plugin default)."""
    return get_plugin_config(APP, key, default)


def _load():
    global _table_ready
    if not _table_ready:
        if PluginSetting._meta.db_table not in connection.introspection.table_names():
            return {}
        _table_ready = True
    return dict(PluginSetting.objects.filter(key__in=BY_KEY).values_list('key', 'value'))


def saved_values():
    """{key: value} saved on the Settings page, read once per request."""
    request = current_request.get()
    memo = getattr(request, _MEMO_ATTR, None) if request is not None else None
    if memo is not None:
        return memo
    values = _load()
    if request is not None:
        try:
            setattr(request, _MEMO_ATTR, values)
        except AttributeError:
            pass
    return values


def forget_request_values():
    request = current_request.get()
    if request is not None and hasattr(request, _MEMO_ATTR):
        delattr(request, _MEMO_ATTR)


def get_setting(key, default=None):
    """The value in effect: saved on the Settings page, else configuration.py,
    else the plugin default."""
    if key in BY_KEY:
        saved = saved_values()
        if key in saved:
            return saved[key]
    return config_value(key, default)


def on(key):
    """Whether the on/off setting `key` is on."""
    return get_setting(key, True) is not False


def plugin_default(key):
    """The plugin's own default (default_settings in __init__.py)."""
    return apps.get_app_config(APP).default_settings.get(key)


def source(key):
    """Where the value in effect comes from: 'saved', 'config' or 'default'."""
    if key in saved_values():
        return 'saved'
    setting = BY_KEY[key]
    if setting.normalize(config_value(key)) == setting.normalize(plugin_default(key)):
        return 'default'
    return 'config'


def save(key, value, user=None):
    """Store `value`, or drop the saved value when it means the same as
    configuration.py's. Returns True if a value is now saved."""
    setting = BY_KEY[key]
    if setting.normalize(value) == setting.normalize(config_value(key)):
        reset(key)
        return False
    PluginSetting.objects.update_or_create(key=key, defaults={'value': value, 'updated_by': user})
    forget_request_values()
    return True


def reset(key):
    """Go back to the configuration.py value."""
    deleted, _rows = PluginSetting.objects.filter(key=key).delete()
    forget_request_values()
    return bool(deleted)
