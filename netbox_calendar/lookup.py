"""
Which objects an event is about, and which events are about an object.

An event's links (EventObject) are read as targeting rules:

- Links to a site, location, region, site group, rack, cluster, device
  role, device type, manufacturer, platform or tenant are *criteria* that
  pick devices and VMs. Different kinds AND together, several of one kind
  OR together: role "access" or "core", and location "Floor 2".
- Devices, VMs and any other object (circuit, interface, ...) linked one by
  one are always included, whatever the criteria say.
- Excluded links (EventObject.exclude) take devices and VMs back out:
  an excluded location leaves out every device in it, also one picked by hand.
- A filter of devices or VMs is one more criterion; a filter of any other
  type lists the objects it matches.

Criteria cover devices only with the scope_rollup setting. Without it a
link concerns just the linked object. concerns() answers the rule for one
object, affected_devices() for every device at once; the two must agree.
"""
from collections import OrderedDict

from django.contrib.contenttypes.models import ContentType
from django.db.models import Q
from django.http import QueryDict
from django.utils.translation import gettext as _

from netbox.registry import registry

from .models import Event
from .plugin_settings import get_setting

# Query parameters of list views that are not filters.
NOT_FILTERS = {'page', 'per_page', 'sort', 'export', 'embedded', 'ordering', 'brief', 'fields', 'omit'}

#: Objects of these types may be excluded: criteria types, devices and VMs.
DEVICE_LABELS = ('dcim.device', 'virtualization.virtualmachine')


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


#
# The scope roll-up: what an object sits in and what it is
#

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
    `obj` and, with the scope_rollup setting, where it sits (site, region,
    site group, location, rack, cluster, host device) and what it is (role,
    type, manufacturer, platform, tenant). Components (interfaces, ports)
    take their device's.
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
    elif _device_of(obj) is not None:
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


#: Criteria types, and how a link to each picks devices. The reverse of scopes().
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

#: Types whose links can be excluded.
EXCLUDABLE = frozenset(DEVICE_MATCHES) | frozenset(DEVICE_LABELS)


def _device_of(obj):
    """The device of a component (interface, port, ...), or None."""
    from dcim.models import Device  # NetBox's own app: always installed.

    device = getattr(obj, 'device', None) if hasattr(obj, 'device_id') else None
    return device if isinstance(device, Device) and obj._meta.label_lower not in DEVICE_LABELS else None


def _key(obj):
    return ContentType.objects.get_for_model(obj).pk, obj.pk


def _keys(objects):
    return {_key(obj) for obj in objects}


#
# An event's links, read as rules
#

class Targets:
    """
    An event's links sorted into the parts of the rule:

    - criteria: {label: [objects]} of criteria types, included;
    - picked: other included objects (devices, VMs, circuits, ...);
    - excluded: excluded objects;
    - deleted: links to objects deleted since (they count for nothing).
    """

    def __init__(self, event):
        self.event = event
        self.rollup = get_setting('scope_rollup', True)
        self.criteria = OrderedDict()
        self.picked, self.excluded, self.deleted = [], [], []
        for link in event.links.select_related('object_type'):
            obj = link.object
            if obj is None:
                self.deleted.append(link)
            elif link.exclude:
                self.excluded.append(obj)
            elif obj._meta.label_lower in DEVICE_MATCHES and self.rollup:
                self.criteria.setdefault(obj._meta.label_lower, []).append(obj)
            else:
                self.picked.append(obj)
        self.included = [obj for objects in self.criteria.values() for obj in objects] + self.picked
        self.included_keys = _keys(self.included)
        self.excluded_keys = _keys(self.excluded)

        # The filter: a criterion for devices or VMs, a list of objects otherwise.
        self.filter_model = event.filter_object_type.model_class() if event.filter_object_type_id else None
        self.filter_label = self.filter_model._meta.label_lower if self.filter_model else None
        self._filtered = None

    @property
    def filtered(self):
        """What the filter matches (a queryset), worked out once."""
        if self._filtered is None and self.filter_model is not None:
            self._filtered = filtered_objects(self.event)
        return self._filtered

    @property
    def filter_is_criterion(self):
        return self.filter_label in DEVICE_LABELS

    @property
    def has_criteria(self):
        return bool(self.criteria) or self.filter_is_criterion

    def criteria_match(self, target):
        """Whether a device or VM meets every criterion (and the device or VM filter)."""
        if not self.has_criteria:
            return False
        if self.filter_is_criterion:
            if type(target) is not self.filter_model or not self.filtered.filter(pk=target.pk).exists():
                return False
        around = _keys(scopes(target))
        return all(_keys(objects) & around for objects in self.criteria.values())

    def concerns(self, obj):
        """Whether the event concerns `obj` (see the module docstring)."""
        around = _keys(scopes(obj))
        if around & self.excluded_keys:
            return False
        if _key(obj) in self.included_keys:
            return True
        target = _device_of(obj) if self.rollup else None
        target = target or obj
        if target._meta.label_lower in DEVICE_LABELS:
            # A device or VM (or a component of one): picked, or meeting the criteria.
            return _key(target) in self.included_keys or self.criteria_match(target)
        if self.filter_model is type(obj) and not self.filter_is_criterion:
            return self.filtered.filter(pk=obj.pk).exists()
        # A site, rack, ...: the event is about it or about where it sits.
        return bool(around & self.included_keys)


def concerns(event, obj):
    """Whether `event` concerns `obj`."""
    return Targets(event).concerns(obj)


def affected_devices(event, targets=None):
    """
    Every device the event concerns, as a queryset: devices meeting all
    its criteria, devices picked one by one (or through a component), less
    excluded ones.
    """
    from dcim.models import Device  # NetBox's own app: always installed.

    targets = targets or Targets(event)
    condition = Q()

    if targets.has_criteria:
        if targets.filter_label == 'virtualization.virtualmachine':
            criteria = None  # A VM filter: no device can match.
        else:
            criteria = Q()
            for label, objects in targets.criteria.items():
                # Any of one kind ...
                any_of = Q()
                for obj in objects:
                    any_of |= DEVICE_MATCHES[label](obj)
                # ... and each kind.
                criteria &= any_of
            if targets.filter_label == 'dcim.device':
                criteria &= Q(pk__in=targets.filtered.values('pk'))
        if criteria is not None:
            condition |= criteria

    picked = set()
    for obj in targets.picked:
        if isinstance(obj, Device):
            picked.add(obj.pk)
        elif (device := _device_of(obj)) is not None:
            picked.add(device.pk)
    if picked:
        condition |= Q(pk__in=picked)

    if not condition:
        return Device.objects.none()

    # Excluded devices, and devices under an excluded site, role, ...
    excluded = Q()
    for obj in targets.excluded:
        label = obj._meta.label_lower
        if label == 'dcim.device':
            excluded |= Q(pk=obj.pk)
        elif label in DEVICE_MATCHES and targets.rollup:
            excluded |= DEVICE_MATCHES[label](obj)
    devices = Device.objects.filter(condition)
    if excluded:
        devices = devices.exclude(excluded)
    return devices.distinct()


def linked_objects(event, limit=100):
    """
    (objects, total): the included objects, then those a non-device filter
    matches, at most `limit`. Excluded links are left out. A link to a
    deleted object comes back as its EventObject (with object None).
    """
    found = list(event.links.filter(exclude=False).select_related('object_type'))
    seen = {(link.object_type_id, link.object_id) for link in found}
    found = [link.object if link.object is not None else link for link in found]
    total = len(found)
    matched = filtered_objects(event)
    if matched is not None and event.filter_object_type.model_class()._meta.label_lower not in DEVICE_LABELS:
        content_type = event.filter_object_type
        total += matched.count()
        for obj in matched[:max(limit - len(found), 0)]:
            if (content_type.pk, obj.pk) not in seen:
                found.append(obj)
    return found[:limit], total


def _names(objects, limit=10):
    names = [str(obj) for obj in objects[:limit]]
    if len(objects) > limit:
        names.append(_('{count} more').format(count=len(objects) - limit))
    return names


def describe(event, targets=None):
    """
    The rule in words, for messages and calendar entries: "Device role:
    Access or Core, and Location: Floor 2. Also: sw-1. Except: Location: Lab".
    """
    targets = targets or Targets(event)
    parts = []
    criteria = [
        f'{type(objects[0])._meta.verbose_name.capitalize()}: {_(" or ").join(_names(objects))}'
        for objects in targets.criteria.values()
    ]
    if event.filter_object_type_id:
        criteria.append(_('{type} matching {filter}').format(
            type=targets.filter_model._meta.verbose_name_plural.capitalize() if targets.filter_model else '?',
            filter=event.filter_query,
        ))
    if criteria:
        parts.append(_(', and ').join(criteria))
    if targets.picked:
        label = _('Also') if criteria else _('Objects')
        parts.append(f'{label}: {", ".join(_names(targets.picked))}')
    if targets.excluded:
        excluded = ', '.join(
            f'{type(obj)._meta.verbose_name.capitalize()}: {obj}' for obj in targets.excluded[:10]
        )
        parts.append(_('Except: {objects}').format(objects=excluded))
    return '. '.join(parts)


def events_for_object(obj, queryset):
    """The events of `queryset` that concern `obj` (see Targets.concerns())."""
    # Candidates: events that include `obj` or something around it, or whose
    # filter is of its type. Each is then checked against the whole rule.
    condition = Q()
    for scope in scopes(obj):
        content_type = ContentType.objects.get_for_model(scope)
        condition |= Q(links__object_type=content_type, links__object_id=scope.pk, links__exclude=False)
    content_type = ContentType.objects.get_for_model(_device_of(obj) or obj)
    condition |= Q(filter_object_type=content_type)
    candidates = Event.objects.filter(condition).distinct().select_related('filter_object_type')
    matched = [event.pk for event in candidates if Targets(event).concerns(obj)]
    return queryset.filter(pk__in=matched)
