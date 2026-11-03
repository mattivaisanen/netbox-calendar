"""
The notification texts, from templates superusers can change on
Calendar -> Settings:

- "Notification message": {{ data.message }} for webhooks (DEFAULT)
- "E-mail subject" and "E-mail body": direct e-mail (EMAIL_SUBJECT,
  EMAIL_BODY)

Each template is Jinja2, like NetBox's own webhook body templates, rendered
in a sandbox. Variables:

- notice: what happened ("Event reminder", "Event cancelled")
- title, event_type, status, impact: of the event
- start, end: the occurrence's start and end, as local text
- when: start and end in one line ("Tue 4 Nov 2026 22:00 - 23:30")
- objects: what the event is about, comma separated
- owner, by: the event owner's and the acting user's names (by is empty
  for notices sent by the background job)
- link: the event's address (full when netbox_url is set)
- comment: the event's description
- occurrence: for a skipped, moved or brought back occurrence, what
  happened ("Occurrence of 4 Nov 2026 22:00 skipped"); empty otherwise
- reason: why that occurrence was skipped, moved or brought back

Teams shows a single line break as a space, so the default puts a blank
line between lines in the webhook message. E-mail keeps single breaks.

No Django models are imported here: the plugin config reads DEFAULT at
startup.
"""
import logging

from jinja2 import TemplateError
from jinja2.sandbox import SandboxedEnvironment

logger = logging.getLogger('netbox_calendar.notifications')

DEFAULT = (
    '{{ notice }}: {{ title }}\n\n'
    '{{ event_type }}, {{ when }}\n\n'
    '{% if occurrence %}{{ occurrence }}\n\n{% endif %}'
    '{% if reason %}Reason: {{ reason }}\n\n{% endif %}'
    '{% if objects %}Concerns: {{ objects }}\n\n{% endif %}'
    '{% if impact %}Impact: {{ impact }}\n\n{% endif %}'
    '{% if by %}By: {{ by }}\n\n{% endif %}'
    '{{ link }}'
)

EMAIL_SUBJECT = '{{ notice }}: {{ title }} ({{ when }})'

EMAIL_BODY = (
    '{{ notice }}: {{ title }}\n'
    '\n'
    'When: {{ when }}\n'
    'Type: {{ event_type }}\n'
    'Status: {{ status }}\n'
    '{% if impact %}Impact: {{ impact }}\n{% endif %}'
    '{% if objects %}Concerns: {{ objects }}\n{% endif %}'
    '{% if owner %}Owner: {{ owner }}\n{% endif %}'
    '{% if by %}By: {{ by }}\n{% endif %}'
    '{% if occurrence %}\n{{ occurrence }}\n{% endif %}'
    '{% if reason %}Reason: {{ reason }}\n{% endif %}'
    '{% if comment %}\n{{ comment }}\n{% endif %}'
    '\n'
    '{{ link }}'
)

SAMPLE = {
    'notice': 'Event reminder', 'title': 'Core switch upgrade', 'event_type': 'Upgrade', 'status': 'Planned',
    'impact': 'Outage', 'start': '4 Nov 2026 22:00', 'end': '4 Nov 2026 23:30',
    'when': '4 Nov 2026 22:00 - 23:30', 'objects': 'core1, core2', 'owner': 'Alex Admin', 'by': '',
    'link': 'https://netbox.example.com/plugins/calendar/events/7/', 'comment': '',
    'occurrence': '', 'reason': '',
}


def _environment():
    return SandboxedEnvironment(autoescape=False)


def clean(text):
    """The template as stored: browser line breaks (\\r\\n) made plain."""
    return (text or '').replace('\r\n', '\n').replace('\r', '\n')


def check(text):
    """Raise ValueError with a readable reason if `text` can't be rendered."""
    try:
        _environment().from_string(clean(text)).render(**SAMPLE)
    except TemplateError as error:
        raise ValueError(str(error)) from error


def render(text, context, default=DEFAULT):
    """`text` rendered with `context`; the `default` template when `text`
    is empty or broken (a broken one is logged)."""
    if text:
        try:
            return _environment().from_string(clean(text)).render(**context).strip()
        except TemplateError:
            logger.exception('Notification template failed; using the default')
    return _environment().from_string(default).render(**context).strip()
