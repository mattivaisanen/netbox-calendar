"""
Which objects an event is about, and which events are about an object.

An event is about the objects linked to it (EventObject), the objects its
filter matches ("every device with role core"), and, with the scope_rollup
setting, everything a linked site, region, location, rack, cluster, device
role, device type, platform or tenant covers: an event on a site or on a
device role concerns its devices. scopes() and affected_devices() are the
two directions of the same rule.
"""
from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.http import QueryDict
from django.utils.translation import gettext as _

from netbox.registry import registry

from .models import Event
from .plugin_settings import get_setting

# Query parameters of list views that are not filters.
NOT_FILTERS = {'page', 'per_page', 'sort', 'export', 'embedded', 'ordering', 'brief', 'fields', 'omit'}


def filterset_for(model):
    """NetBox's filter set of `model`, or None."""
    if model is None:
        return None
    return registry['filtersets'].get(model._meta.label_lower)


def _query(text):
    query = QueryDict((text or '').strip().lstrip('?'), mutable=True)
    for key in NOT_FILTERS:
        query.pop(key, None)
    return query


def filter_error(model, text):
    """Why `text` is not a usable filter of `model`, or None."""
    filterset_class = filterset_for(model)
    if filterset_class is None:
        return _('Objects of this type cannot be filtered.')
    query = _query(text)
    if not query:
        return _('The filter is empty.')
    # A misspelled name would be ignored and match every object.
    unknown = [key for key in query if key not in filterset_class.base_filters]
    if unknown:
        return _('Unknown filter: {names}.').format(names=', '.join(unknown))
    filterset = filterset_class(query, model.objects.all())
    if not filterset.is_valid():
        return '; '.join(f'{name}: {" ".join(errors)}' for name, errors in filterset.errors.items())
    return None


def filtered_objects(event):
    """The objects the event's filter matches (a queryset), or None."""
    if not event.filter_object_type_id:
        return None
    model = event.filter_object_type.model_class()
    filterset_class = filterset_for(model)
    if filterset_class is None:
        return None
    filterset = filterset_class(_query(event.filter_query), model.objects.all())
    return filterset.qs if filterset.is_valid() else model.objects.none()


def linked_objects(event, limit=100):
    """
    (objects, total): what the event is about, linked ones first, then those
    its filter matches, at most `limit`. A link to a deleted object comes
    back as its EventObject (with object None).
    """
    found, seen = [], set()
    for link in event.links.select_related('object_type'):
        obj = link.object
        key = (link.object_type_id, link.object_id)
        seen.add(key)
        found.append(obj if obj is not None else link)
    total = len(found)
    matched = filtered_objects(event)
    if matched is not None:
        content_type = event.filter_object_type
        total += matched.count()
        for obj in matched[:max(limit - len(found), 0)]:
            if (content_type.pk, obj.pk) not in seen:
                found.append(obj)
    return found[:limit], total


def _with_ancestors(node):
    if node is None:
        return []
    try:
        return list(node.get_ancestors(include_self=True))
    except AttributeError:
        return [node]


def _site_scopes(site):
    if site is None:
        return []
    return [site] + _with_ancestors(site.region) + _with_ancestors(site.group)


def _kind_scopes(obj):
    """What kind of device or VM `obj` is: role, platform (with their parents) and tenant."""
    return _with_ancestors(obj.role) + _with_ancestors(obj.platform) + [obj.tenant]


def scopes(obj):
    """
    The objects whose events also concern `obj`: itself and, with the
    scope_rollup setting, where it sits (site, region, site group,
    location, rack, cluster, host device) and what it is (role, type,
    manufacturer, platform, tenant). Components (interfaces, ports) take
    their device's.
    """
    if not get_setting('scope_rollup', True):
        return [obj]
    label = obj._meta.label_lower
    if label == 'dcim.device':
        around = (
            _site_scopes(obj.site) + _with_ancestors(obj.location) + [obj.rack, obj.cluster]
            + _kind_scopes(obj) + [obj.device_type, obj.device_type.manufacturer]
        )
    elif label == 'virtualization.virtualmachine':
        around = _site_scopes(obj.site) + [obj.cluster, obj.device] + _kind_scopes(obj)
    elif label == 'dcim.rack':
        around = _site_scopes(obj.site) + _with_ancestors(obj.location)
    elif label == 'dcim.location':
        around = _site_scopes(obj.site) + _with_ancestors(obj.parent)
    elif label == 'dcim.site':
        around = _site_scopes(obj)[1:]
    elif label in ('dcim.region', 'dcim.sitegroup', 'tenancy.tenantgroup'):
        around = _with_ancestors(obj)[:-1]
    elif getattr(obj, 'device', None) is not None and hasattr(obj, 'device_id'):
        # A component (interface, port, ...): its device and what is around it.
        return [obj] + scopes(obj.device)
    else:
        around = []
    return [obj] + [scope for scope in around if scope is not None]


def _tree(obj):
    """`obj` and everything under it (sub-regions, child locations, ...)."""
    try:
        return obj.get_descendants(include_self=True)
    except AttributeError:
        return [obj]


#: How a link to each kind of object picks devices, with the scope_rollup
#: setting. The reverse of scopes().
DEVICE_MATCHES = {
    'dcim.devicerole': lambda obj: Q(role__in=_tree(obj)),
    'dcim.devicetype': lambda obj: Q(device_type=obj),
    'dcim.manufacturer': lambda obj: Q(device_type__manufacturer=obj),
    'dcim.platform': lambda obj: Q(platform__in=_tree(obj)),
    'dcim.site': lambda obj: Q(site=obj),
    'dcim.region': lambda obj: Q(site__region__in=_tree(obj)),
    'dcim.sitegroup': lambda obj: Q(site__group__in=_tree(obj)),
    'dcim.location': lambda obj: Q(location__in=_tree(obj)),
    'dcim.rack': lambda obj: Q(rack=obj),
    'virtualization.cluster': lambda obj: Q(cluster=obj),
    'tenancy.tenant': lambda obj: Q(tenant=obj),
}


def affected_devices(event):
    """
    Every device the event concerns, as a queryset: devices linked
    directly or through a component (an interface), devices its filter
    matches, and, with the scope_rollup setting, the devices of a linked
    site, role, type and so on (DEVICE_MATCHES).
    """
    from dcim.models import Device  # NetBox's own app: always installed.

    rollup = get_setting('scope_rollup', True)
    condition = Q()
    for link in event.links.select_related('object_type'):
        obj = link.object
        if obj is None:
            continue
        label = obj._meta.label_lower
        if label == 'dcim.device':
            condition |= Q(pk=obj.pk)
        elif label in DEVICE_MATCHES:
            if rollup:
                condition |= DEVICE_MATCHES[label](obj)
        elif isinstance(getattr(obj, 'device', None), Device):
            # A component (interface, port, ...): its device.
            condition |= Q(pk=obj.device.pk)
    if event.filter_object_type_id and event.filter_object_type.model_class() is Device:
        condition |= Q(pk__in=filtered_objects(event).values('pk'))
    if not condition:
        return Device.objects.none()
    return Device.objects.filter(condition).distinct()


def events_for_object(obj, queryset):
    """The events of `queryset` that concern `obj` (see scopes())."""
    condition = Q()
    for scope in scopes(obj):
        content_type = ContentType.objects.get_for_model(scope)
        condition |= Q(links__object_type=content_type, links__object_id=scope.pk)
    # Events whose filter matches it.
    content_type = ContentType.objects.get_for_model(obj)
    matched = [
        event.pk for event in Event.objects.filter(filter_object_type=content_type).select_related('filter_object_type')
        if (objects := filtered_objects(event)) is not None and objects.filter(pk=obj.pk).exists()
    ]
    if matched:
        condition |= Q(pk__in=matched)
    return queryset.filter(condition).distinct()
