from netbox.api.routers import NetBoxRouter

from . import views

router = NetBoxRouter()
router.register('event-types', views.EventTypeViewSet)
router.register('event-roles', views.EventRoleViewSet)
router.register('events', views.EventViewSet)
router.register('event-objects', views.EventObjectViewSet)
router.register('event-people', views.EventPersonViewSet)

urlpatterns = router.urls
