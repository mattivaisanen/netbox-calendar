from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from dcim.models import DeviceRole, Location

from ..lookup import affected_devices, concerns, describe, events_for_object
from ..models import Event, EventObject
from .utils import make_device, make_event, make_user


class TargetRuleTests(TestCase):
    """Kinds AND together, objects of one kind OR together, exclusions take out, picked devices add."""

    def setUp(self):
        self.access = DeviceRole.objects.create(name='Access', slug='access')
        self.core = DeviceRole.objects.create(name='Core', slug='core')
        site = make_device('spare').site
        self.here = Location.objects.create(name='Here', slug='here', site=site)
        self.lab = Location.objects.create(name='Lab', slug='lab', site=site, parent=self.here)
        self.elsewhere = Location.objects.create(name='Elsewhere', slug='elsewhere', site=site)

        def device(name, role, location):
            obj = make_device(name)
            obj.role, obj.location = role, location
            obj.save()
            return obj

        self.acc_here = device('acc-here', self.access, self.here)
        self.acc_lab = device('acc-lab', self.access, self.lab)
        self.acc_away = device('acc-away', self.access, self.elsewhere)
        self.core_here = device('core-here', self.core, self.here)
        self.router_here = device('router-here', DeviceRole.objects.get(slug='router'), self.here)
        self.event = make_event()

    def link(self, obj, exclude=False):
        EventObject.objects.create(event=self.event, object=obj, exclude=exclude)

    def assertAffected(self, *devices):
        self.assertEqual(set(affected_devices(self.event)), set(devices))
        # The per-object answer agrees with the list.
        for obj in (self.acc_here, self.acc_lab, self.acc_away, self.core_here, self.router_here):
            with self.subTest(device=obj.name):
                self.assertEqual(concerns(self.event, obj), obj in devices)

    def test_kinds_and_values_or(self):
        self.link(self.access)
        self.link(self.core)
        self.link(self.here)
        # Access or core devices, in Here (and its child location Lab).
        self.assertAffected(self.acc_here, self.acc_lab, self.core_here)
        self.assertIn('Access or Core', describe(self.event))

    def test_exclusion(self):
        self.link(self.access)
        self.link(self.lab, exclude=True)
        self.assertAffected(self.acc_here, self.acc_away)

    def test_picked_devices_add_but_exclusions_win(self):
        self.link(self.access)
        self.link(self.here)
        self.link(self.router_here)  # Picked by hand: in, though not an access device.
        self.link(self.acc_away)  # In, though not in Here.
        self.link(self.lab, exclude=True)
        self.assertAffected(self.acc_here, self.router_here, self.acc_away)

    def test_events_on_object_pages(self):
        self.link(self.access)
        self.link(self.here)
        events = Event.objects.all()
        self.assertIn(self.event, events_for_object(self.acc_here, events))
        self.assertNotIn(self.event, events_for_object(self.acc_away, events))
        self.assertNotIn(self.event, events_for_object(self.core_here, events))
        # The location and the role pages list it too.
        self.assertIn(self.event, events_for_object(self.here, events))
        self.assertIn(self.event, events_for_object(self.access, events))


class TargetFormTests(TestCase):
    def setUp(self):
        self.admin = make_user('admin', is_superuser=True)
        self.client.force_login(self.admin)
        self.device = make_device()
        self.event = make_event(owner=self.admin)
        self.event.links.create(object=self.device)
        self.edit_url = reverse('plugins:netbox_calendar:event_edit', args=[self.event.pk])

    def test_add_menu_shows_a_picker(self):
        # Opening shows only the kinds in use.
        form = self.client.get(self.edit_url).context['form']
        self.assertIn('include__dcim__device', form.fields)
        self.assertNotIn('exclude__dcim__location', form.fields)

        # Choosing "Exclude > Location" re-renders the section with that picker, keeping the device.
        response = self.client.get(self.edit_url, {
            'add_target': 'exclude__dcim__location', 'include__dcim__device': self.device.pk,
        }, HTTP_HX_REQUEST='true')
        form = response.context['form']
        self.assertIn('exclude__dcim__location', form.fields)
        self.assertEqual(form.initial['include__dcim__device'], [str(self.device.pk)])
        self.assertEqual(form.initial['add_target'], '')

    def test_save_switches_and_removes_links(self):
        location = Location.objects.create(name='Hall', slug='hall', site=self.device.site)
        response = self.client.post(self.edit_url, {
            'title': self.event.title, 'event_type': self.event.event_type.pk, 'status': 'planned',
            'impact': 'none', 'start': timezone.localtime(self.event.start).strftime('%Y-%m-%d %H:%M:%S'), 'repeat': '',
            'add_target': '', 'include__dcim__devicerole': [self.device.role.pk],
            'exclude__dcim__location': [location.pk],
        })
        self.assertEqual(response.status_code, 302)
        links = {(link.object, link.exclude) for link in self.event.links.all()}
        self.assertEqual(links, {(self.device.role, False), (location, True)})
