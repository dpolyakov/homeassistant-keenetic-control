"""Track router clients selected in the integration options."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.device_tracker import ScannerEntity, SourceType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client_registry import cleanup_unselected_clients, normalize_mac
from .const import CONF_CLIENTS, CONF_POLICY_CLIENTS, DOMAIN
from .device_helpers import router_device_id
from .model import client_is_live, normalize_records, parse_hotspot_client


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator = runtime.coordinator
    entities: dict[str, KeeneticClientTracker] = {}
    selected = {
        normalized
        for mac in entry.options.get(CONF_CLIENTS, [])
        if isinstance(mac, str) and (normalized := normalize_mac(mac)) is not None
    }
    policy_selected = {
        normalized
        for mac in entry.options.get(CONF_POLICY_CLIENTS, [])
        if isinstance(mac, str) and (normalized := normalize_mac(mac)) is not None
    }
    cleanup_unselected_clients(hass, entry, selected | policy_selected)

    def add_mac(mac: str, record: Mapping[str, Any] | None = None) -> None:
        normalized = mac.strip().lower().replace("-", ":")
        if not _is_mac(normalized) or normalized in entities:
            return
        entity = KeeneticClientTracker(coordinator, entry, normalized, record)
        entities[normalized] = entity
        async_add_entities([entity])

    for mac in selected:
        add_mac(mac)

    @callback
    def async_add_discovered_clients() -> None:
        records = _hotspot_records(coordinator.data)
        if records is None:
            return
        for record in records:
            mac = record.get("mac")
            if isinstance(mac, str) and normalize_mac(mac) in selected:
                add_mac(mac, record)

    entry.async_on_unload(coordinator.async_add_listener(async_add_discovered_clients))
    async_add_discovered_clients()


def _hotspot_records(data: Mapping[str, Any] | None) -> list[Mapping[str, Any]] | None:
    if data is None:
        return None
    response = data.get("hotspot")
    for key in ("host", "hosts", "client"):
        records = normalize_records(response, key)
        if records is not None:
            return records
    records = normalize_records(response)
    if records is None or any("mac" not in row for row in records):
        return None
    return records


def _is_mac(value: str) -> bool:
    return normalize_mac(value) is not None


class KeeneticClientTracker(CoordinatorEntity, ScannerEntity):
    """Represent a known router client with its hardware address as identity."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry, mac: str, initial: Mapping[str, Any] | None) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._mac = mac
        self._record = dict(initial or {})
        self._attr_unique_id = f"{entry.unique_id}_{mac}"

    @property
    def name(self) -> str:
        return str(self._current().get("name") or self._current().get("hostname") or self._mac)

    @property
    def source_type(self) -> SourceType:
        return SourceType.ROUTER

    @property
    def mac_address(self) -> str:
        return self._mac

    @property
    def is_connected(self) -> bool | None:
        return self._live_current() is not None if _hotspot_records(self.coordinator.data) is not None else None

    @property
    def ip_address(self) -> str | None:
        row = self._live_current()
        value = row.get("ip") if row is not None else None
        return value.strip() if isinstance(value, str) and value.strip() else None

    @property
    def available(self) -> bool:
        return super().available and _hotspot_records(self.coordinator.data) is not None

    @property
    def device_info(self):
        return {
            "identifiers": {
                (DOMAIN, f"{self._entry.unique_id}:client:{self._mac}")
            },
            "name": self.name,
            "via_device_id": router_device_id(self.hass, self._entry),
        }

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        row = self._live_current()
        if row is None:
            return None
        details = parse_hotspot_client(row)
        return {
            key: details[key]
            for key in ("interface", "rssi", "uptime")
            if details.get(key) is not None
        } or None

    def _live_current(self) -> Mapping[str, Any] | None:
        records = _hotspot_records(self.coordinator.data)
        if records is None:
            return None
        for row in records:
            details = parse_hotspot_client(row)
            if details["mac"] == self._mac and client_is_live(details):
                self._record = dict(row)
                return row
        return None

    def _current(self) -> Mapping[str, Any]:
        return self._live_current() or self._record
