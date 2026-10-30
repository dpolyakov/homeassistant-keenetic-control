"""Administrative controls for supported VPN and modem child devices."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import ChildDeviceInfo, DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import KeeneticCoordinator
from .device_helpers import router_device_id
from .entity_model import InterfaceState, build_interface_entity_model


def _vpn_interfaces(data: Mapping[str, Any] | None) -> tuple[InterfaceState, ...]:
    if data is None:
        return ()
    model = build_interface_entity_model(
        data.get("interface_records"),
        data.get("routes"),
    )
    return tuple(
        interface
        for interface in model.interfaces
        if interface.role == "vpn" and interface.enabled is not None
    )


def _modem_interfaces(data: Mapping[str, Any] | None) -> tuple[InterfaceState, ...]:
    if data is None:
        return ()
    model = build_interface_entity_model(
        data.get("interface_records"), data.get("routes")
    )
    return tuple(
        interface
        for interface in model.interfaces
        if interface.role == "modem" and interface.enabled is not None
    )


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add administrative switches for known VPN and modem interfaces."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator: KeeneticCoordinator = runtime.coordinator
    known: set[str] = set()

    @callback
    def add_current_entities() -> None:
        removed_ids = (coordinator.data or {}).get("removed_entity_ids")
        if isinstance(removed_ids, set):
            known.difference_update(removed_ids)
        new = [
            interface
            for interface in _vpn_interfaces(coordinator.data)
            if f"{entry.unique_id}_vpn_{interface.identifier}_enabled" not in known
        ]
        modems = [
            interface
            for interface in _modem_interfaces(coordinator.data)
            if f"{entry.unique_id}_modem_{interface.identifier}_enabled" not in known
        ]
        if new or modems:
            known.update(
                f"{entry.unique_id}_vpn_{interface.identifier}_enabled"
                for interface in new
            )
            known.update(
                f"{entry.unique_id}_modem_{interface.identifier}_enabled"
                for interface in modems
            )
            async_add_entities(
                [
                    *(
                        KeeneticVpnSwitch(coordinator, entry, interface.identifier, interface.name)
                        for interface in new
                    ),
                    *(
                        KeeneticModemSwitch(coordinator, entry, interface.identifier, interface.name)
                        for interface in modems
                    ),
                ]
            )

    add_current_entities()
    entry.async_on_unload(coordinator.async_add_listener(add_current_entities))


class KeeneticVpnSwitch(CoordinatorEntity[KeeneticCoordinator], SwitchEntity):
    """Enable or disable one VPN interface without conflating link state."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "vpn_enabled"

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
        self._attr_unique_id = f"{entry.unique_id}_vpn_{interface_id}_enabled"

    def _current(self) -> InterfaceState | None:
        return next(
            (
                interface
                for interface in _vpn_interfaces(self.coordinator.data)
                if interface.identifier == self._interface_id
            ),
            None,
        )

    @property
    def is_on(self) -> bool | None:
        current = self._current()
        return current.enabled if current is not None else None

    @property
    def available(self) -> bool:
        return super().available and self.is_on is not None

    @property
    def device_info(self) -> ChildDeviceInfo:
        return ChildDeviceInfo(
            identifiers={
                (DOMAIN, f"vpn:{self._entry.unique_id}|{self._interface_id}")
            },
            name=self._interface_name,
            parent_device_id=router_device_id(self.hass, self._entry),
        )

    async def _async_set_enabled(self, enabled: bool) -> None:
        endpoint = "interface/up" if enabled else "interface/down"
        await self.coordinator.async_call(endpoint, {"name": self._interface_id})
        await self.coordinator.async_request_refresh()
        if not self.coordinator.last_update_success or self.is_on is not enabled:
            raise HomeAssistantError("The router did not confirm the VPN state request")

    async def async_turn_on(self, **_kwargs: Any) -> None:
        await self._async_set_enabled(True)

    async def async_turn_off(self, **_kwargs: Any) -> None:
        await self._async_set_enabled(False)


class KeeneticModemSwitch(CoordinatorEntity[KeeneticCoordinator], SwitchEntity):
    """Allow or forbid router use of one cellular interface."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "modem_enabled"

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
        self._attr_unique_id = f"{entry.unique_id}_modem_{interface_id}_enabled"

    @property
    def is_on(self) -> bool | None:
        current = next(
            (
                interface
                for interface in _modem_interfaces(self.coordinator.data)
                if interface.identifier == self._interface_id
            ),
            None,
        )
        return current.enabled if current is not None else None

    @property
    def available(self) -> bool:
        return super().available and self.is_on is not None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"modem:{self._entry.unique_id}|{self._interface_id}")},
            name=self._interface_name,
            via_device_id=router_device_id(self.hass, self._entry),
        )

    async def _async_set_enabled(self, enabled: bool) -> None:
        endpoint = "interface/up" if enabled else "interface/down"
        await self.coordinator.async_call(endpoint, {"name": self._interface_id})
        await self.coordinator.async_request_refresh()
        if not self.coordinator.last_update_success or self.is_on is not enabled:
            raise HomeAssistantError("The router did not confirm the modem state request")

    async def async_turn_on(self, **_kwargs: Any) -> None:
        await self._async_set_enabled(True)

    async def async_turn_off(self, **_kwargs: Any) -> None:
        await self._async_set_enabled(False)
