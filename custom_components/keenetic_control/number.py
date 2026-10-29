"""Numeric client controls backed by Keenetic traffic shaping."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client_registry import client_display_name, selected_client_macs
from .const import DOMAIN
from .coordinator import KeeneticCoordinator
from .device_helpers import router_device_id
from .model import normalize_records, parse_hotspot_client, parse_traffic_shape


def _hotspot_client_record(data: Mapping[str, Any] | None, mac: str) -> Mapping[str, Any] | None:
    response = data.get("hotspot") if data is not None else None
    records = normalize_records(response, "host") or normalize_records(response)
    if records is None:
        return None
    return next(
        (record for record in records if parse_hotspot_client(record)["mac"] == mac),
        None,
    )


def _client_limit(data: Mapping[str, Any] | None, mac: str) -> int | None:
    if data is None:
        return None
    return parse_traffic_shape(data.get("traffic_shape")).get(mac)


class KeeneticClientBandwidthLimit(CoordinatorEntity[KeeneticCoordinator], NumberEntity):
    """One equal RX/TX host bandwidth limit, in native kbit/s."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "client_bandwidth_limit"
    _attr_native_unit_of_measurement = "kbit/s"

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry, mac: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._mac = mac
        self._attr_unique_id = f"{entry.unique_id}_client_{mac}_bandwidth_limit"
        record = _hotspot_client_record(coordinator.data, mac)
        label = client_display_name(record, mac)
        self._label = label

    @property
    def native_value(self) -> int | None:
        return _client_limit(self.coordinator.data, self._mac)

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.data is not None
            and self.coordinator.data.get("traffic_shape") is not None
        )

    @property
    def native_min_value(self) -> float:
        return 64

    @property
    def native_max_value(self) -> float:
        return 1_000_000

    @property
    def native_step(self) -> float:
        return 1

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.unique_id}:client:{self._mac}")},
            name=self._label,
            via_device_id=router_device_id(self.hass, self._entry),
        )

    async def async_set_native_value(self, value: float) -> None:
        rate = int(value)
        if value != rate or not self.native_min_value <= rate <= self.native_max_value:
            raise HomeAssistantError("Bandwidth limit must be an integer from 64 to 1000000 kbit/s")
        await self.coordinator.async_call(
            "ip/traffic-shape/host", {"mac": self._mac, "rate": rate}
        )
        await self.coordinator.async_request_refresh()
        if _client_limit(self.coordinator.data, self._mac) != rate:
            raise HomeAssistantError("The router did not confirm the bandwidth limit")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    runtime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        KeeneticClientBandwidthLimit(runtime.coordinator, entry, mac)
        for mac in sorted(selected_client_macs(entry))
    )
