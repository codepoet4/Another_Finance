"""iCloud Garage Automation — Home Assistant custom integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .tracker import GarageCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up iCloud Garage Automation from a config entry."""
    coordinator = GarageCoordinator(hass, {**entry.data, **entry.options})

    if not await coordinator.async_setup():
        return False

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Reload integration when options are updated
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry and clean up all callbacks."""
    coordinator: GarageCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
    await coordinator.async_shutdown()
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the integration when configuration options change."""
    await hass.config_entries.async_reload(entry.entry_id)
