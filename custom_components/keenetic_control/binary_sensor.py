"""Binary sensors backed by documented Keenetic status fields."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import ChildDeviceInfo, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client_registry import client_display_name, selected_client_macs
from .const import DOMAIN
from .coordinator import KeeneticCoordinator
from .device_helpers import router_device_id
from .entity_model import build_interface_entity_model
from .model import (
    client_is_live,
    normalize_records,
    parse_hotspot_client,
    parse_internet_status,
    parse_interface_fields,
    parse_lte_data_usage,
    parse_modem_interface_fields,
    parse_usb_media_status,
    parse_usb_storage_devices,
)


@dataclass(frozen=True)
class _BinarySensorSpec:
    """Definition for a binary sensor read from coordinator data."""

    unique_id: str
    name: str
    value: Callable[[Mapping[str, Any]], bool | None]
    device_class: BinarySensorDeviceClass | None = None
    device_id: str | None = None
    device_name: str | None = None
    attributes: Callable[[Mapping[str, Any]], dict[str, Any]] | None = None
    translation_key: str | None = None
    translation_placeholders: dict[str, str] | None = None
    entity_category: EntityCategory | None = None


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _device_identifier(entry: ConfigEntry, device_id: str) -> str:
    """Return the stable modem device identifier shared with device actions."""
    if device_id.startswith("modem:"):
        return f"modem:{entry.unique_id}|{device_id.removeprefix('modem:')}"
    if device_id.startswith("vpn:"):
        return f"vpn:{entry.unique_id}|{device_id.removeprefix('vpn:')}"
    if device_id.startswith("client:"):
        return f"{entry.unique_id}:client:{device_id.removeprefix('client:')}"
    return f"{entry.unique_id}:{device_id}"


def _hotspot_client_record(data: Mapping[str, Any], mac: str) -> Mapping[str, Any] | None:
    response = data.get("hotspot")
    records = normalize_records(response, "host") or normalize_records(response)
    if records is None:
        return None
    for record in records:
        details = parse_hotspot_client(record)
        if details["mac"] == mac and client_is_live(details):
            return record
    return None


def _client_status(data: Mapping[str, Any], mac: str, field: str) -> bool | None:
    record = _hotspot_client_record(data, mac)
    if record is None:
        return None
    value = parse_hotspot_client(record).get(field)
    if isinstance(value, bool):
        return value
    if field == "link_status" and isinstance(value, str):
        return {"up": True, "down": False}.get(value.casefold())
    return None


def _client_specs(entry: ConfigEntry, data: Mapping[str, Any]) -> list[_BinarySensorSpec]:
    specs: list[_BinarySensorSpec] = []
    prefix = str(entry.unique_id)
    for mac in sorted(selected_client_macs(entry)):
        label = client_display_name(_hotspot_client_record(data, mac), mac)
        specs.extend(
            (
                _BinarySensorSpec(
                    f"{prefix}_client_{mac}_dhcp_registered",
                    "DHCP registered",
                    lambda values, ident=mac: _client_status(values, ident, "dhcp_registered"),
                    device_id=f"client:{mac}",
                    device_name=label,
                    translation_key="client_dhcp_registered",
                    entity_category=EntityCategory.DIAGNOSTIC,
                ),
                _BinarySensorSpec(
                    f"{prefix}_client_{mac}_link_status",
                    "Link status",
                    lambda values, ident=mac: _client_status(values, ident, "link_status"),
                    BinarySensorDeviceClass.CONNECTIVITY,
                    device_id=f"client:{mac}",
                    device_name=label,
                    translation_key="client_link_status",
                    entity_category=EntityCategory.DIAGNOSTIC,
                ),
            )
        )
    return specs


def _internet_status(data: Mapping[str, Any], field: str) -> bool | None:
    internet = _mapping(data.get("internet"))
    if internet is None:
        return None
    status = parse_internet_status(internet)
    if status.get("reliable") is not True:
        return None
    return status.get(field)


def _usb_storage_records(data: Mapping[str, Any]) -> list[Mapping[str, Any]] | None:
    """Return only storage devices from the documented ``show usb`` list."""
    return parse_usb_storage_devices(data.get("usb"))


def _usb_storage_detected(data: Mapping[str, Any]) -> bool | None:
    """Report whether at least one USB volume is mounted."""
    storage_devices = _usb_storage_records(data)
    if storage_devices is None:
        return None
    if not storage_devices:
        return False
    status = parse_usb_media_status(data.get("media"))
    return status["available"] if status is not None else None


def _usb_storage_attributes(data: Mapping[str, Any]) -> dict[str, Any]:
    status = parse_usb_media_status(data.get("media"))
    if status is None:
        return {}
    storage_devices = _usb_storage_records(data)
    return {
        "storage_device_count": len(storage_devices) if storage_devices is not None else None,
        "media_count": status["media_count"],
        "volume_count": status["volume_count"],
        "mounted_volume_count": status["mounted_volume_count"],
        "mounted_total_bytes": status["mounted_total_bytes"],
        "mounted_free_bytes": status["mounted_free_bytes"],
    }


def _wireguard_peer_value(
    data: Mapping[str, Any], interface_id: str, peer_id: str
) -> bool | None:
    inventory = _wireguard_peer_inventory(data, interface_id)
    if inventory is None:
        return None
    peers = inventory.get("peers")
    if not isinstance(peers, list):
        return None
    peer = next(
        (item for item in peers if isinstance(item, Mapping) and item.get("id") == peer_id),
        None,
    )
    return peer.get("online") if peer is not None else None


def _wireguard_peer_inventory(data: Mapping[str, Any], interface_id: str) -> Mapping[str, Any] | None:
    inventories = _mapping(data.get("wireguard_peer_inventories"))
    if inventories is not None:
        return _mapping(inventories.get(interface_id))
    server = _mapping(data.get("wireguard_server"))
    return server if server is not None and server.get("interface_id") == interface_id else None


def _wireguard_peer_specs(
    entry: ConfigEntry, data: Mapping[str, Any]
) -> list[_BinarySensorSpec]:
    inventories = _mapping(data.get("wireguard_peer_inventories"))
    if inventories is None:
        server = _mapping(data.get("wireguard_server"))
        inventories = (
            {server.get("interface_id"): server}
            if server is not None and isinstance(server.get("interface_id"), str)
            else {}
        )
    specs: list[_BinarySensorSpec] = []
    for raw_interface_id, raw_inventory in inventories.items():
        inventory = _mapping(raw_inventory)
        if inventory is None:
            continue
        interface_id = inventory.get("interface_id", raw_interface_id)
        peers = inventory.get("peers")
        if not isinstance(interface_id, str) or not isinstance(peers, list):
            continue
        interface_name = inventory.get("interface_description")
        interface_name = interface_name if isinstance(interface_name, str) else interface_id
        specs.extend(
            _BinarySensorSpec(
                f"{entry.unique_id}_wireguard_{interface_id}_{peer['id']}_online",
                "WireGuard peer online",
                lambda values, if_id=interface_id, key=peer["id"]: _wireguard_peer_value(
                    values, if_id, key
                ),
                BinarySensorDeviceClass.CONNECTIVITY,
                device_id=f"vpn:{interface_id}",
                device_name=interface_name,
                translation_key="wireguard_peer_online",
                translation_placeholders={"peer": str(peer.get("name") or peer["id"])},
            )
            for peer in peers
            if isinstance(peer, Mapping)
            and isinstance(peer.get("id"), str)
            and isinstance(peer.get("online"), bool)
        )
    return specs


def _router_specs(entry: ConfigEntry) -> list[_BinarySensorSpec]:
    prefix = str(entry.unique_id)
    return [
        _BinarySensorSpec(
            f"{prefix}_router_available",
            "Router reachable",
            lambda _data: True,
            BinarySensorDeviceClass.CONNECTIVITY,
            translation_key="router_available",
        ),
        _BinarySensorSpec(
            f"{prefix}_internet",
            "Internet available",
            lambda data: _internet_status(data, "internet"),
            BinarySensorDeviceClass.CONNECTIVITY,
            translation_key="internet_available",
        ),
        _BinarySensorSpec(
            f"{prefix}_gateway_accessible",
            "Internet gateway accessible",
            lambda data: _internet_status(data, "gateway-accessible"),
            BinarySensorDeviceClass.CONNECTIVITY,
            translation_key="gateway_accessible",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        _BinarySensorSpec(
            f"{prefix}_dns_accessible",
            "Internet DNS accessible",
            lambda data: _internet_status(data, "dns-accessible"),
            BinarySensorDeviceClass.CONNECTIVITY,
            translation_key="dns_accessible",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        _BinarySensorSpec(
            f"{prefix}_internet_hosts_accessible",
            "Internet check hosts accessible",
            lambda data: _internet_status(data, "host-accessible"),
            BinarySensorDeviceClass.CONNECTIVITY,
            translation_key="internet_hosts_accessible",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        _BinarySensorSpec(
            f"{prefix}_usb_storage_detected",
            "",
            _usb_storage_detected,
            BinarySensorDeviceClass.CONNECTIVITY,
            attributes=_usb_storage_attributes,
            translation_key="usb_storage_detected",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
    ]


def _interface_id(record: Mapping[str, Any]) -> str | None:
    value = record.get("id")
    return str(value).strip() if isinstance(value, (str, int)) and str(value).strip() else None


def _interface_label(record: Mapping[str, Any], interface_id: str) -> str:
    for key in ("description", "interface-name"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return interface_id


def _interface_record(data: Mapping[str, Any], interface_id: str) -> Mapping[str, Any] | None:
    records = data.get("interface_records")
    if not isinstance(records, list):
        return None
    for record in records:
        if isinstance(record, Mapping) and _interface_id(record) == interface_id:
            return record
    return None


def _interface_specs(entry: ConfigEntry, data: Mapping[str, Any]) -> list[_BinarySensorSpec]:
    """Expose connectivity only for known VPN child devices."""
    prefix = str(entry.unique_id)
    specs: list[_BinarySensorSpec] = []
    for interface in build_interface_entity_model(
        data.get("interface_records"), data.get("routes")
    ).interfaces:
        if interface.role != "vpn" or interface.connected is None:
            continue
        specs.append(
            _BinarySensorSpec(
                f"{prefix}_vpn_{interface.identifier}_connected",
                "Connected",
                lambda values, ident=interface.identifier: _interface_connected(values, ident),
                BinarySensorDeviceClass.CONNECTIVITY,
                device_id=f"vpn:{interface.identifier}",
                device_name=interface.name,
                translation_key="vpn_connected",
            )
        )
    return specs


def _interface_connected(data: Mapping[str, Any], interface_id: str) -> bool | None:
    record = _interface_record(data, interface_id)
    if record is None:
        return None
    return parse_interface_fields(record).get("connected")


def _modem_specs(entry: ConfigEntry, data: Mapping[str, Any]) -> list[_BinarySensorSpec]:
    prefix = str(entry.unique_id)
    modems = _mapping(data.get("modems")) or {}
    specs: list[_BinarySensorSpec] = []
    for interface, modem_value in modems.items():
        modem = _mapping(modem_value)
        if modem is None:
            continue
        interface_id = str(interface)
        interface_info = _mapping(modem.get("interface_info")) or {}
        label = _interface_label(interface_info, interface_id)
        specs.append(
            _BinarySensorSpec(
                f"{prefix}_modem_{interface_id}_connected",
                "Connected",
                lambda values, ident=interface_id: _modem_connected(values, ident),
                BinarySensorDeviceClass.CONNECTIVITY,
                f"modem:{interface_id}",
                label,
                translation_key="modem_connected",
            )
        )
        if "plugged" in parse_modem_interface_fields(interface_info):
            specs.append(
                _BinarySensorSpec(
                    f"{prefix}_modem_{interface_id}_plugged",
                    "Physically connected",
                    lambda values, ident=interface_id: _modem_plugged(values, ident),
                    BinarySensorDeviceClass.CONNECTIVITY,
                    f"modem:{interface_id}",
                    label,
                    translation_key="modem_plugged",
                )
            )
        if "roaming" in parse_modem_interface_fields(interface_info):
            specs.append(
                _BinarySensorSpec(
                    f"{prefix}_modem_{interface_id}_roaming",
                    "Roaming",
                    lambda values, ident=interface_id: _modem_roaming(values, ident),
                    device_id=f"modem:{interface_id}",
                    device_name=label,
                    translation_key="modem_roaming",
                    entity_category=EntityCategory.DIAGNOSTIC,
                )
            )
        quota = parse_lte_data_usage(modem.get("traffic_counter"))
        if quota is not None:
            for field, suffix, translation_key in (
                ("limit_exceeded", "data_limit_exceeded", "modem_data_limit_exceeded"),
                ("threshold_exceeded", "data_threshold_exceeded", "modem_data_threshold_exceeded"),
            ):
                if field not in quota:
                    continue

                def quota_flag(
                    values: Mapping[str, Any],
                    ident: str = interface_id,
                    key: str = field,
                ) -> bool | None:
                    current_modems = _mapping(values.get("modems")) or {}
                    current_modem = _mapping(current_modems.get(ident)) or {}
                    parsed = parse_lte_data_usage(current_modem.get("traffic_counter"))
                    flag = parsed.get(key) if parsed is not None else None
                    return flag if isinstance(flag, bool) else None

                specs.append(
                    _BinarySensorSpec(
                        f"{prefix}_modem_{interface_id}_{suffix}",
                        suffix.replace("_", " ").title(),
                        quota_flag,
                        BinarySensorDeviceClass.PROBLEM,
                        f"modem:{interface_id}",
                        label,
                        translation_key=translation_key,
                        entity_category=EntityCategory.DIAGNOSTIC,
                    )
                )
    return specs


def _modem_connected(data: Mapping[str, Any], interface_id: str) -> bool | None:
    modems = _mapping(data.get("modems")) or {}
    modem = _mapping(modems.get(interface_id))
    if modem is None:
        return None
    interface_info = _mapping(modem.get("interface_info")) or {}
    return parse_interface_fields(interface_info).get("connected")


def _modem_plugged(data: Mapping[str, Any], interface_id: str) -> bool | None:
    modems = _mapping(data.get("modems")) or {}
    modem = _mapping(modems.get(interface_id))
    if modem is None:
        return None
    interface_info = _mapping(modem.get("interface_info")) or {}
    return parse_modem_interface_fields(interface_info).get("plugged")


def _modem_roaming(data: Mapping[str, Any], interface_id: str) -> bool | None:
    modems = _mapping(data.get("modems")) or {}
    modem = _mapping(modems.get(interface_id))
    if modem is None:
        return None
    interface_info = _mapping(modem.get("interface_info")) or {}
    return parse_modem_interface_fields(interface_info).get("roaming")


class KeeneticBinarySensor(CoordinatorEntity[KeeneticCoordinator], BinarySensorEntity):
    """A binary sensor read from one coordinator snapshot."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        spec: _BinarySensorSpec,
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._spec = spec
        self._attr_unique_id = spec.unique_id
        self._attr_entity_category = spec.entity_category
        if spec.translation_key is not None:
            self._attr_translation_key = spec.translation_key
            self._attr_translation_placeholders = spec.translation_placeholders or {}
        else:
            self._attr_name = spec.name

    @property
    def device_info(self) -> DeviceInfo | ChildDeviceInfo:
        if self._spec.device_id is not None:
            identifier = _device_identifier(self._entry, self._spec.device_id)
            if self._spec.device_id.startswith("vpn:"):
                return ChildDeviceInfo(
                    identifiers={(DOMAIN, identifier)},
                    name=self._spec.device_name,
                    parent_device_id=router_device_id(self.hass, self._entry),
                )
            return DeviceInfo(
                identifiers={(DOMAIN, identifier)},
                via_device_id=router_device_id(self.hass, self._entry),
                name=self._spec.device_name,
            )
        return DeviceInfo(identifiers={(DOMAIN, str(self._entry.unique_id))})

    @property
    def is_on(self) -> bool | None:
        if self._spec.name == "Router reachable":
            return self.coordinator.last_update_success
        if not self.coordinator.data:
            return None
        return self._spec.value(self.coordinator.data)

    @property
    def available(self) -> bool:
        if self._spec.name == "Router reachable":
            return True
        if not super().available or not self.coordinator.data:
            return False
        return self._spec.value(self.coordinator.data) is not None

    @property
    def device_class(self) -> BinarySensorDeviceClass | None:
        return self._spec.device_class

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._spec.attributes is None or not self.coordinator.data:
            return None
        return self._spec.attributes(self.coordinator.data)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up router status entities and add discovered interface sensors."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator: KeeneticCoordinator = runtime.coordinator
    known: set[str] = set()

    @callback
    def add_current_specs() -> None:
        data = coordinator.data or {}
        removed_ids = data.get("removed_entity_ids")
        if isinstance(removed_ids, set):
            known.difference_update(removed_ids)
        specs = _router_specs(entry)
        specs.extend(_interface_specs(entry, data))
        specs.extend(_client_specs(entry, data))
        specs.extend(_modem_specs(entry, data))
        specs.extend(_wireguard_peer_specs(entry, data))
        new_specs = [spec for spec in specs if spec.unique_id not in known]
        if not new_specs:
            return
        known.update(spec.unique_id for spec in new_specs)
        async_add_entities([KeeneticBinarySensor(coordinator, entry, spec) for spec in new_specs])

    add_current_specs()
    entry.async_on_unload(coordinator.async_add_listener(add_current_specs))
