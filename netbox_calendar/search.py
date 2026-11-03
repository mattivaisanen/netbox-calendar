from netbox.search import SearchIndex

from .models import Event, EventType


class EventIndex(SearchIndex):
    model = Event
    fields = (
        ('title', 100),
        ('description', 500),
        ('comments', 5000),
    )
    display_attrs = ('event_type', 'status', 'start')


class EventTypeIndex(SearchIndex):
    model = EventType
    fields = (
        ('name', 100),
        ('description', 500),
    )


indexes = (EventIndex, EventTypeIndex)
