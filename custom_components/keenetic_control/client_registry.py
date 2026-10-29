"""Keep client entity identity scoped to one Keenetic config entry."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import CONF_CLIENTS, CONF_POLICY_CLIENTS, DOMAIN


def normalize_mac(value: str) -> str | None:
    """Normalize a valid hardware MAC address."""
    mac = value.strip().lower().replace("-", ":")
    parts = mac.split(":")
    if len(parts) != 6 or any(
        len(part) != 2 or any(character not in "0123456789abcdef" for character in part)
        for part in parts
    ):
        return None
    return mac


def selected_client_macs(entry: ConfigEntry) -> set[str]:
    """Return the union of clients selected for tracking or policy controls."""
    result: set[str] = set()
    for option in (CONF_CLIENTS, CONF_POLICY_CLIENTS):
        for value in entry.options.get(option, []):
            if isinstance(value, str) and (mac := normalize_mac(value)) is not None:
                result.add(mac)
    return result


def client_display_name(record: Mapping[str, Any] | None, mac: str) -> str:
    """Prefer a router-provided client name, falling back to its stable MAC."""
    if isinstance(record, Mapping):
        for field in ("name", "hostname"):
            value = record.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return mac


def known_client_macs(hass: HomeAssistant, entry: ConfigEntry) -> set[str]:
    """Return client MACs already registered to this router, including offline clients."""
    prefix = f"{entry.unique_id}:client:"
    result: set[str] = set()
    for device in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id):
        for domain, identifier in device.identifiers:
            if domain == DOMAIN and identifier.startswith(prefix):
                mac = normalize_mac(identifier[len(prefix) :])
                if mac is not None:
                    result.add(mac)
    return result


def cleanup_unselected_clients(
    hass: HomeAssistant, entry: ConfigEntry, selected_macs: set[str]
) -> None:
    """Remove only this integration's client records deselected in its options."""
    entity_registry = er.async_get(hass)
    tracker_prefix = f"{entry.unique_id}_"
    client_prefix = f"{entry.unique_id}_client_"

    def client_mac_from_entity(unique_id: str) -> str | None:
        if unique_id.startswith(tracker_prefix):
            candidate = unique_id[len(tracker_prefix) :]
            if (mac := normalize_mac(candidate)) is not None:
                return mac
        if unique_id.startswith(client_prefix):
            candidate = unique_id[len(client_prefix) :].split("_", 1)[0]
            return normalize_mac(candidate)
        return None

    for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if entity.platform != DOMAIN:
            continue
        mac = client_mac_from_entity(entity.unique_id)
        if mac is not None and mac not in selected_macs:
            entity_registry.async_remove(entity.entity_id)

    device_registry = dr.async_get(hass)
    device_prefix = f"{entry.unique_id}:client:"
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        if device.config_entry_id != entry.entry_id:
            continue
        macs = {
            mac
            for domain, identifier in device.identifiers
            if domain == DOMAIN
            and identifier.startswith(device_prefix)
            and (mac := normalize_mac(identifier[len(device_prefix) :])) is not None
        }
        if macs and macs.isdisjoint(selected_macs):
            device_registry.async_remove_device(device.id)
