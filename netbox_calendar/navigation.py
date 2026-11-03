from django.utils.translation import gettext_lazy as _

from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem

# Its own tab in the navigation bar, like "Organization" or "IPAM".
menu = PluginMenu(
    label=_('Calendar'),
    icon_class='mdi mdi-calendar-month-outline',
    groups=(
        (_('Calendar'), (
            PluginMenuItem(
                link='plugins:netbox_calendar:month',
                link_text=_('Month'),
                permissions=['netbox_calendar.view_event'],
            ),
            PluginMenuItem(
                link='plugins:netbox_calendar:timeline',
                link_text=_('Timeline'),
                permissions=['netbox_calendar.view_event'],
            ),
            PluginMenuItem(
                link='plugins:netbox_calendar:event_list',
                link_text=_('Events'),
                permissions=['netbox_calendar.view_event'],
                buttons=(
                    PluginMenuButton(
                        link='plugins:netbox_calendar:event_add',
                        title=_('Add'),
                        icon_class='mdi mdi-plus-thick',
                        permissions=['netbox_calendar.add_event'],
                    ),
                ),
            ),
            PluginMenuItem(
                link='plugins:netbox_calendar:my_feed',
                link_text=_('Calendar Feed'),
                permissions=['netbox_calendar.view_event'],
            ),
        )),
        (_('Setup'), (
            PluginMenuItem(
                link='plugins:netbox_calendar:eventtype_list',
                link_text=_('Event Types'),
                permissions=['netbox_calendar.view_eventtype'],
            ),
            PluginMenuItem(
                link='plugins:netbox_calendar:eventrole_list',
                link_text=_('Event Roles'),
                permissions=['netbox_calendar.view_eventrole'],
            ),
            PluginMenuItem(
                link='plugins:netbox_calendar:settings',
                link_text=_('Settings'),
                staff_only=True,  # superusers only
            ),
        )),
    ),
)
