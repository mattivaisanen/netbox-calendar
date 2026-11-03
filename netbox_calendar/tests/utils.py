from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from core.models import ObjectType
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from users.models import ObjectPermission, User

from ..models import Event, EventType


def commit():
    """
    Run the commit callbacks waiting so far, as a real commit would. A
    TestCase never commits, and notices about edits are sent on commit.
    """
    connection = transaction.get_connection()
    while connection.run_on_commit:
        _savepoints, callback, _robust = connection.run_on_commit.pop(0)
        callback()


def make_device(name='edge-1'):
    site, _ = Site.objects.get_or_create(name='Site A', slug='site-a')
    manufacturer, _ = Manufacturer.objects.get_or_create(name='Maker', slug='maker')
    device_type, _ = DeviceType.objects.get_or_create(manufacturer=manufacturer, model='Box', slug='box')
    role, _ = DeviceRole.objects.get_or_create(name='Router', slug='router')
    return Device.objects.create(name=name, site=site, device_type=device_type, role=role)


def make_event(owner=None, hours_from_now=24, minutes=60, **fields):
    """An event of the default "Upgrade" type, saved without notices."""
    start = (timezone.now() + timedelta(hours=hours_from_now)).replace(second=0, microsecond=0)
    fields.setdefault('event_type', EventType.objects.get(slug='upgrade'))
    fields.setdefault('title', 'Firmware update')
    event = Event(event_owner=owner, start=start, end=start + timedelta(minutes=minutes), **fields)
    event._calendar_quiet = True
    event.full_clean()
    event.save()
    del event._calendar_quiet
    return event


def grant(user, actions, model=Event):
    """Give `user` NetBox permission for `actions` on `model`."""
    permission = ObjectPermission.objects.create(name=f'{user.username}-{model._meta.model_name}', actions=actions)
    permission.object_types.set([ObjectType.objects.get_for_model(model)])
    permission.users.set([user])
    return permission


def make_user(username, **fields):
    return User.objects.create(username=username, email=f'{username}@example.com', **fields)
