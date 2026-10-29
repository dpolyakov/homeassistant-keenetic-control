"""Helpers for stable router device relationships."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN


def router_device_id(hass: HomeAssistant, entry: ConfigEntry) -> str:
    """Return the registered parent device ID for this router entry."""
    return dr.async_get_device_id_by_identifier(
        hass,
        (DOMAIN, str(entry.unique_id)),
        config_entry_id=entry.entry_id,
    )
