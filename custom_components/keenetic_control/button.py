"""Router-level actions."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from collections.abc import Mapping
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client_registry import client_display_name, selected_client_macs
from .const import CONF_ADDRESS, CONF_PORT, DOMAIN
from .coordinator import KeeneticCoordinator
from .device_helpers import router_device_id
from .model import normalize_records, parse_hotspot_client, parse_traffic_shape

_MODEM_POWER_CYCLE_PAUSE_MS = 3000


def _modem_rows(data: Mapping[str, Any] | None) -> list[tuple[str, str]]:
    if data is None or not isinstance(data.get("modems"), Mapping):
        return []
    rows: list[tuple[str, str]] = []
    for raw_id, raw_modem in data["modems"].items():
        interface_id = str(raw_id).strip()
        if not interface_id or not isinstance(raw_modem, Mapping):
            continue
        info = raw_modem.get("interface_info")
        info = info if isinstance(info, Mapping) else {}
        label = next(
            (
                value.strip()
                for key in ("description", "interface-name")
                if isinstance((value := info.get(key)), str) and value.strip()
            ),
            interface_id,
        )
        rows.append((interface_id, label))
    return rows


def _client_rows(data: Mapping[str, Any] | None, entry: ConfigEntry) -> list[tuple[str, str]]:
    response = data.get("hotspot") if data is not None else None
    records = normalize_records(response, "host") or normalize_records(response) or []
    by_mac = {
        details["mac"]: record
        for record in records
        if (details := parse_hotspot_client(record))["mac"] is not None
    }
    return [
        (mac, client_display_name(by_mac.get(mac), mac))
        for mac in sorted(selected_client_macs(entry))
    ]


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    runtime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([KeeneticRebootButton(runtime.coordinator, runtime.api, entry)])
    coordinator: KeeneticCoordinator = runtime.coordinator
    async_add_entities(
        [
            client_button
            for mac, label in _client_rows(coordinator.data, entry)
            for client_button in (
                KeeneticClientClearBandwidthButton(coordinator, entry, mac, label),
                KeeneticClientWakeButton(coordinator, entry, mac, label),
            )
        ]
    )
    known: set[str] = set()

    @callback
    def add_current_entities() -> None:
        removed_ids = (coordinator.data or {}).get("removed_entity_ids")
        if isinstance(removed_ids, set):
            known.difference_update(removed_ids)
        new = [
            (interface_id, label)
            for interface_id, label in _modem_rows(coordinator.data)
            if f"{entry.unique_id}_modem_{interface_id}_restart" not in known
        ]
        if not new:
            return
        known.update(
            f"{entry.unique_id}_modem_{interface_id}_restart"
            for interface_id, _label in new
        )
        async_add_entities(
            KeeneticModemRestartButton(coordinator, entry, interface_id, label)
            for interface_id, label in new
        )

    add_current_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_current_entities))


class KeeneticRebootButton(CoordinatorEntity, ButtonEntity):
    """Request an immediate router reboot through the documented RCI action."""

    _attr_has_entity_name = True
    _attr_translation_key = "reboot"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, api, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._api = api
        self._entry = entry
        self._attr_unique_id = f"{entry.unique_id}_reboot"
        version = coordinator.data.get("version", {}) if coordinator.data else {}
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.unique_id)},
            "name": entry.title,
            "manufacturer": version.get("manufacturer", "Keenetic"),
            "model": version.get("model"),
            "hw_version": version.get("hw_version"),
            "sw_version": version.get("release"),
            "configuration_url": f"{entry.data[CONF_ADDRESS]}:{entry.data[CONF_PORT]}",
        }

    async def async_press(self) -> None:
        await self._api.async_post("system/reboot", {})


class KeeneticModemRestartButton(
    CoordinatorEntity[KeeneticCoordinator], ButtonEntity
):
    """Power-cycle one USB modem through the documented interface command."""

    _attr_has_entity_name = True
    _attr_translation_key = "restart_modem"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        interface_id: str,
        interface_name: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._interface_id = interface_id
        self._interface_name = interface_name
        self._attr_unique_id = f"{entry.unique_id}_modem_{interface_id}_restart"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={
                (DOMAIN, f"modem:{self._entry.unique_id}|{self._interface_id}")
            },
            name=self._interface_name,
            via_device_id=router_device_id(self.hass, self._entry),
        )

    async def async_press(self) -> None:
        await self.coordinator.async_call(
            "interface/usb/power-cycle",
            {"name": self._interface_id, "pause": _MODEM_POWER_CYCLE_PAUSE_MS},
        )


class _KeeneticClientButton(CoordinatorEntity[KeeneticCoordinator], ButtonEntity):
    """Base for actions that remain addressable by a selected client MAC."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        mac: str,
        label: str,
        suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._mac = mac
        self._label = label
        self._attr_unique_id = f"{entry.unique_id}_client_{mac}_{suffix}"

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.unique_id}:client:{self._mac}")},
            name=self._label,
            via_device_id=router_device_id(self.hass, self._entry),
        )


class KeeneticClientClearBandwidthButton(_KeeneticClientButton):
    """Remove a host bandwidth limit with an RCI DELETE reset."""

    _attr_translation_key = "client_clear_bandwidth_limit"

    def __init__(self, coordinator, entry, mac: str, label: str) -> None:
        super().__init__(coordinator, entry, mac, label, "clear_bandwidth_limit")

    async def async_press(self) -> None:
        await self.coordinator.async_delete("ip/traffic-shape/host", {"mac": self._mac})
        await self.coordinator.async_request_refresh()
        traffic_shape = (self.coordinator.data or {}).get("traffic_shape")
        if traffic_shape is None:
            raise HomeAssistantError("The router did not return the bandwidth configuration")
        limits = parse_traffic_shape(traffic_shape)
        if self._mac in limits:
            raise HomeAssistantError("The router did not confirm clearing the bandwidth limit")


class KeeneticClientWakeButton(_KeeneticClientButton):
    """Send the documented Wake on LAN command for a selected client."""

    _attr_translation_key = "client_wake_on_lan"

    def __init__(self, coordinator, entry, mac: str, label: str) -> None:
        super().__init__(coordinator, entry, mac, label, "wake_on_lan")

    async def async_press(self) -> None:
        await self.coordinator.async_call("ip/hotspot/wake", {"mac": self._mac})
