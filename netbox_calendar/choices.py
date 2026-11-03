from django.utils.translation import gettext_lazy as _

from utilities.choices import ChoiceSet


class EventStatusChoices(ChoiceSet):
    PLANNED = 'planned'
    CONFIRMED = 'confirmed'
    IN_PROGRESS = 'in_progress'
    DONE = 'done'
    CANCELLED = 'cancelled'

    CHOICES = (
        (PLANNED, _('Planned'), 'cyan'),
        (CONFIRMED, _('Confirmed'), 'blue'),
        (IN_PROGRESS, _('In progress'), 'orange'),
        (DONE, _('Done'), 'green'),
        (CANCELLED, _('Cancelled'), 'gray'),
    )

    #: Statuses of an event that is still going to happen (or is happening).
    ACTIVE = (PLANNED, CONFIRMED, IN_PROGRESS)


class ImpactChoices(ChoiceSet):
    NONE = 'none'
    DEGRADED = 'degraded'
    OUTAGE = 'outage'

    CHOICES = (
        (NONE, _('No impact'), 'green'),
        (DEGRADED, _('Degraded'), 'yellow'),
        (OUTAGE, _('Outage'), 'red'),
    )


class NoticeChoices(ChoiceSet):
    """What an event type sends notifications about (EventType.notify_on)."""
    SCHEDULED = 'scheduled'
    CHANGED = 'changed'
    CANCELLED = 'cancelled'
    REMINDER = 'reminder'
    STARTED = 'started'
    ENDED = 'ended'

    CHOICES = (
        (SCHEDULED, _('Scheduled')),
        (CHANGED, _('Changed')),
        (CANCELLED, _('Cancelled')),
        (REMINDER, _('Reminder')),
        (STARTED, _('Started')),
        (ENDED, _('Ended')),
    )
