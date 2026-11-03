from django.urls import include, path

from utilities.urls import get_model_urls

from . import views

urlpatterns = [
    path('', views.MonthView.as_view(), name='month'),
    path('timeline/', views.TimelineView.as_view(), name='timeline'),
    path('feed/', views.MyFeedView.as_view(), name='my_feed'),
    path('feed/<str:token>.ics', views.FeedView.as_view(), name='feed'),
    path('settings/', views.SettingsView.as_view(), name='settings'),

    path('events/', include(get_model_urls('netbox_calendar', 'event', detail=False))),
    path('events/<int:pk>/', include(get_model_urls('netbox_calendar', 'event'))),
    path('events/targets-preview/', views.EventTargetsPreviewView.as_view(), name='event_targets_preview'),
    path('events/<int:pk>/link/', views.EventLinkView.as_view(), name='event_link'),
    path('events/<int:pk>/unlink/<int:link_pk>/', views.EventUnlinkView.as_view(), name='event_unlink'),
    path('events/<int:pk>/people/add/', views.EventPersonView.as_view(), name='event_person_add'),
    path('events/<int:pk>/people/<int:person_pk>/', views.EventPersonView.as_view(), name='event_person_edit'),
    path(
        'events/<int:pk>/people/<int:person_pk>/delete/', views.EventPersonDeleteView.as_view(),
        name='event_person_delete',
    ),
    path('events/<int:pk>/occurrence/', views.EventOccurrenceView.as_view(), name='event_occurrence'),
    path('events/<int:pk>/journal/add/', views.EventJournalView.as_view(), name='event_journal_add'),
    path('events/<int:pk>/ics/', views.EventICSView.as_view(), name='event_ics'),

    path('event-types/', include(get_model_urls('netbox_calendar', 'eventtype', detail=False))),
    path('event-types/<int:pk>/', include(get_model_urls('netbox_calendar', 'eventtype'))),
    path('event-roles/', include(get_model_urls('netbox_calendar', 'eventrole', detail=False))),
    path('event-roles/<int:pk>/', include(get_model_urls('netbox_calendar', 'eventrole'))),
]
