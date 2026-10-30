"""Remove dynamic registry entries only after complete Keenetic inventories."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import (
    CONF_ALL_CLIENTS,
    CONF_ALL_POLICIES,
    CONF_ALLOW_MUTATING_RCI,
    CONF_BACKUP_TYPES,
    CONF_CLIENTS,
    CONF_POLICY_CLIENTS,
    CONF_PORT_FORWARD_SWITCHES,
    DOMAIN,
)
from .model import (
    mesh_member_id,
    normalize_interface_records,
    normalize_records,
    parse_mesh_member,
)

_INTERFACE_ENTITY_SUFFIXES = (
    "uptime",
    "rxbytes",
    "txbytes",
    "received_rate",
    "sent_rate",
    "connected",
    "idle_timeout",
)
_MESH_ENTITY_SUFFIXES = (
    "firmware_version",
    "cpu_load",
    "memory_used",
    "uptime",
    "associations",
    "backhaul_speed",
    "backhaul_uplink",
    "backhaul_ap",
    "backhaul_rssi",
    "backhaul_txrate",
)
_MODEM_ENTITY_SUFFIXES = (
    "uptime",
    "rxbytes",
    "txbytes",
    "received_rate",
    "sent_rate",
    "operator",
    "technology",
    "signal_level",
    "band",
    "connection_state",
    "apn",
    "rssi",
    "rsrp",
    "rsrq",
    "sinr",
    "temperature",
    "data_used",
    "data_remaining",
    "data_limit",
    "data_threshold",
    "days_until_reset",
    "quota_usage",
    "unread_sms",
    "enabled",
    "connected",
    "plugged",
    "roaming",
    "data_limit_exceeded",
    "data_threshold_exceeded",
    "restart",
)
_VPN_ENTITY_SUFFIXES = (
    "uptime",
    "rxbytes",
    "txbytes",
    "received_rate",
    "sent_rate",
    "connected",
    "enabled",
)
_WIREGUARD_ENTITY_SUFFIXES = (
    "handshake",
    "received_bytes",
    "sent_bytes",
    "remote_endpoint",
    "online",
)

_RETIRED_CLIENT_SENSOR_SUFFIXES = (
    "interface",
    "uptime",
    "received_bytes",
    "sent_bytes",
    "rssi",
)


def _client_entity_mac(unique_id: str, entry: ConfigEntry) -> str | None:
    """Return the MAC portion of a client entity unique ID."""
    prefix = f"{entry.unique_id}_client_"
    if not unique_id.startswith(prefix):
        return None
    mac = unique_id[len(prefix) :].split("_", 1)[0]
    parts = mac.split(":")
    if len(parts) != 6 or any(len(part) != 2 for part in parts):
        return None
    return mac


def _remove_repeated_object_prefix(object_id: str) -> str | None:
    """Remove one repeated device-name prefix from an entity object ID."""
    parts = object_id.split("_")
    for prefix_length in range(1, len(parts) // 2 + 1):
        if parts[:prefix_length] != parts[prefix_length : prefix_length * 2]:
            continue
        suffix = parts[prefix_length * 2 :]
        if suffix:
            return "_".join(parts[:prefix_length] + suffix)
    return None


def migrate_duplicate_client_entity_ids(
    hass: HomeAssistant, entry: ConfigEntry
) -> set[tuple[str, str]]:
    """Rename beta client entities whose device prefix was added twice."""
    registry = er.async_get(hass)
    renamed: set[tuple[str, str]] = set()
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.platform != DOMAIN or _client_entity_mac(entity.unique_id, entry) is None:
            continue
        domain, separator, object_id = entity.entity_id.partition(".")
        if not separator:
            continue
        new_object_id = _remove_repeated_object_prefix(object_id)
        if new_object_id is None:
            continue
        new_entity_id = f"{domain}.{new_object_id}"
        if new_entity_id == entity.entity_id or registry.async_get(new_entity_id) is not None:
            continue
        old_entity_id = entity.entity_id
        registry.async_update_entity(old_entity_id, new_entity_id=new_entity_id)
        renamed.add((old_entity_id, new_entity_id))
    return renamed


def migrated_options_v2(
    current: Mapping[str, Any], known_clients: set[str]
) -> dict[str, Any]:
    """Convert broad version-1 toggles to explicit version-2 selections."""
    options = dict(current)
    if options.get(CONF_ALL_CLIENTS, False):
        options[CONF_CLIENTS] = sorted(
            known_clients
            | {
                value
                for value in options.get(CONF_CLIENTS, [])
                if isinstance(value, str)
            }
        )
    if options.get(CONF_ALL_POLICIES, False):
        options[CONF_POLICY_CLIENTS] = sorted(
            known_clients
            | {
                value
                for value in options.get(CONF_POLICY_CLIENTS, [])
                if isinstance(value, str)
            }
        )
    for retired in (
        CONF_ALL_CLIENTS,
        CONF_ALL_POLICIES,
        CONF_PORT_FORWARD_SWITCHES,
        CONF_BACKUP_TYPES,
    ):
        options.pop(retired, None)
    options.setdefault(CONF_ALLOW_MUTATING_RCI, False)
    return options


def cleanup_retired_registry(hass: HomeAssistant, entry: ConfigEntry) -> set[str]:
    """Remove version-1 entities and client devices retired by the product model."""
    prefix = str(entry.unique_id)
    interface_prefix = f"{prefix}_interface_"
    ping_prefix = f"{prefix}_ping_check_"
    client_prefix = f"{prefix}_client_"
    retired_exact = {f"{prefix}_last_boot"}
    registry = er.async_get(hass)
    removed: set[str] = set()
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.platform != DOMAIN:
            continue
        unique_id = entity.unique_id
        is_client_sensor = unique_id.startswith(client_prefix) and unique_id.endswith(
            tuple(f"_{suffix}" for suffix in _RETIRED_CLIENT_SENSOR_SUFFIXES)
        )
        if (
            unique_id in retired_exact
            or unique_id.startswith(interface_prefix)
            or unique_id.startswith(ping_prefix)
            or is_client_sensor
        ):
            registry.async_remove(entity.entity_id)
            removed.add(unique_id)

    return removed


def _stable_id(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    result = str(value).strip()
    return result or None


def _only_used_by_entry(device: Any, entry_id: str) -> bool:
    """Confirm that the registry device belongs to this config entry."""
    return getattr(device, "config_entry_id", None) == entry_id


def _complete_interface_ids(response: Any) -> set[str] | None:
    """Return IDs only when show/interface contains a complete, valid list."""
    keyed_response = None
    if isinstance(response, Mapping):
        keyed_response = response.get("interface", response)
    if isinstance(keyed_response, Mapping) and "id" not in keyed_response:
        for key, record in keyed_response.items():
            # Numeric keys are positional (for example switch ports); named
            # interface records should agree with their explicit stable ID.
            if str(key).isdigit():
                continue
            if not isinstance(record, Mapping) or _stable_id(record.get("id")) != str(key):
                return None

    records = normalize_interface_records(response)
    if records is None:
        return None
    identifiers: set[str] = set()
    for record in records:
        identifier = _stable_id(record.get("id"))
        if identifier is None or identifier in identifiers:
            return None
        interface_type = record.get("type")
        if not isinstance(interface_type, str) or not interface_type.strip():
            return None
        identifiers.add(identifier)
    return identifiers


def _complete_mesh_ids(response: Any) -> set[str] | None:
    """Return member IDs only for a recognized, complete show/mws/member list."""
    member_collections: list[Any] = []

    def find_member_collections(value: Any) -> None:
        if isinstance(value, Mapping):
            if "member" in value:
                member_collections.append(value["member"])
            for key, child in value.items():
                if key != "member":
                    find_member_collections(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                find_member_collections(child)

    find_member_collections(response)
    if len(member_collections) > 1:
        return None
    if member_collections:
        records = normalize_records(member_collections[0])
    elif isinstance(response, Mapping):
        identifier = mesh_member_id(response)
        records = [response] if identifier is not None else None
    else:
        records = normalize_records(response)
    if records is None:
        return None

    identifiers: set[str] = set()
    for record in records:
        identifier = mesh_member_id(record)
        if identifier is None or identifier in identifiers:
            return None
        identifiers.add(identifier)
    return identifiers


def _complete_mesh_backhaul_speed_ids(response: Any) -> set[str] | None:
    """Return only members whose complete record has a numeric backhaul speed."""
    mesh_ids = _complete_mesh_ids(response)
    if mesh_ids is None:
        return None
    records: list[Mapping[str, Any]] = []

    def collect(value: Any) -> None:
        if isinstance(value, Mapping):
            if mesh_member_id(value) is not None:
                records.append(value)
            for child in value.values():
                collect(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                collect(child)

    collect(response)
    return {
        identifier
        for record in records
        if (identifier := mesh_member_id(record)) in mesh_ids
        and parse_mesh_member(record).get("backhaul_speed") is not None
    }


def _complete_ping_check_ids(response: Any, router_id: str) -> set[str] | None:
    """Return entity IDs only when every Ping Check row identifies its interface/profile."""
    records = normalize_records(response, "pingcheck")
    if records is None:
        records = normalize_records(response)
    if records is None:
        return None
    identifiers: set[str] = set()
    for record in records:
        interface = record.get("interface")
        profile = record.get("profile")
        if not isinstance(interface, str) or not interface.strip():
            return None
        if not isinstance(profile, str) or not profile.strip():
            return None
        unique_id = f"{router_id}_ping_check_{interface.strip()}_{profile.strip()}"
        if unique_id in identifiers:
            return None
        identifiers.add(unique_id)
    return identifiers


def _complete_wireguard_peer_ids(response: Any) -> tuple[str, set[str]] | None:
    """Return a peer inventory only when the server response is complete."""
    if not isinstance(response, Mapping):
        return None
    interface_id = _stable_id(response.get("interface_id"))
    peers = response.get("peers")
    if interface_id is None or not isinstance(peers, list):
        return None
    identifiers: set[str] = set()
    for peer in peers:
        if not isinstance(peer, Mapping):
            return None
        identifier = _stable_id(peer.get("id"))
        if identifier is None or identifier in identifiers:
            return None
        identifiers.add(identifier)
    return interface_id, identifiers


def _dynamic_id(unique_id: str, prefix: str, suffixes: tuple[str, ...]) -> str | None:
    if not unique_id.startswith(prefix):
        return None
    remainder = unique_id[len(prefix) :]
    for suffix in suffixes:
        ending = f"_{suffix}"
        if remainder.endswith(ending):
            identifier = remainder[: -len(ending)]
            if identifier:
                return identifier
    return None


def _wireguard_entity_parts(unique_id: str, prefix: str) -> tuple[str, str] | None:
    if not unique_id.startswith(prefix):
        return None
    remainder = unique_id[len(prefix) :]
    for suffix in _WIREGUARD_ENTITY_SUFFIXES:
        ending = f"_{suffix}"
        if not remainder.endswith(ending):
            continue
        identity = remainder[: -len(ending)]
        try:
            interface_id, peer_id = identity.rsplit("_", 1)
        except ValueError:
            return None
        if interface_id and peer_id:
            return interface_id, peer_id
    return None


def cleanup_dynamic_registry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    interface_response: Any,
    mesh_response: Any,
    ping_check_response: Any,
    wireguard_peer_response: Any = None,
) -> set[str]:
    """Delete stale dynamic entities/devices only after complete inventories."""
    interface_ids = _complete_interface_ids(interface_response)
    mesh_ids = _complete_mesh_ids(mesh_response)
    mesh_speed_ids = _complete_mesh_backhaul_speed_ids(mesh_response)
    ping_check_ids = _complete_ping_check_ids(ping_check_response, str(entry.unique_id))
    wireguard_peer_ids = _complete_wireguard_peer_ids(wireguard_peer_response)
    if (
        interface_ids is None
        and mesh_ids is None
        and ping_check_ids is None
        and wireguard_peer_ids is None
    ):
        return set()

    entity_registry = er.async_get(hass)
    removed_entity_ids: set[str] = set()

    if (
        interface_ids is not None
        or mesh_ids is not None
        or ping_check_ids is not None
        or wireguard_peer_ids is not None
    ):
        interface_prefix = f"{entry.unique_id}_interface_"
        mesh_entity_prefix = f"{entry.unique_id}_mesh_"
        ping_check_prefix = f"{entry.unique_id}_ping_check_"
        wireguard_prefix = f"{entry.unique_id}_wireguard_"
        retired_modem_prefix = f"{entry.unique_id}_modem_"
        vpn_entity_prefix = f"{entry.unique_id}_vpn_"
        for entity in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
            if entity.platform != DOMAIN:
                continue
            if (
                interface_ids is not None
                and entity.unique_id.startswith(retired_modem_prefix)
                and entity.unique_id.endswith(("_information", "_detected_cells"))
            ):
                entity_registry.async_remove(entity.entity_id)
                removed_entity_ids.add(entity.unique_id)
                continue
            if interface_ids is not None:
                if entity.unique_id.startswith(interface_prefix) and entity.unique_id.endswith(
                    "_admin_up"
                ):
                    entity_registry.async_remove(entity.entity_id)
                    removed_entity_ids.add(entity.unique_id)
                    continue
                interface_id = _dynamic_id(
                    entity.unique_id, interface_prefix, _INTERFACE_ENTITY_SUFFIXES
                )
                if interface_id is not None and interface_id not in interface_ids:
                    entity_registry.async_remove(entity.entity_id)
                    removed_entity_ids.add(entity.unique_id)
                    continue
            if mesh_ids is not None:
                member_id = _dynamic_id(
                    entity.unique_id, mesh_entity_prefix, _MESH_ENTITY_SUFFIXES
                )
                if member_id is None and entity.unique_id.startswith(mesh_entity_prefix):
                    remainder = entity.unique_id[len(mesh_entity_prefix) :]
                    candidate, marker, port_index = remainder.rpartition("_port_")
                    if marker and candidate and port_index.isdecimal():
                        member_id = candidate
                if (
                    member_id is not None
                    and entity.unique_id.endswith("_backhaul_speed")
                    and mesh_speed_ids is not None
                    and member_id not in mesh_speed_ids
                ):
                    entity_registry.async_remove(entity.entity_id)
                    removed_entity_ids.add(entity.unique_id)
                    continue
                if member_id is not None and member_id not in mesh_ids:
                    entity_registry.async_remove(entity.entity_id)
                    removed_entity_ids.add(entity.unique_id)
                    continue
            if interface_ids is not None:
                modem_id = _dynamic_id(
                    entity.unique_id, retired_modem_prefix, _MODEM_ENTITY_SUFFIXES
                )
                if modem_id is not None and modem_id not in interface_ids:
                    entity_registry.async_remove(entity.entity_id)
                    removed_entity_ids.add(entity.unique_id)
                    continue
            if interface_ids is not None:
                vpn_id = _dynamic_id(
                    entity.unique_id, vpn_entity_prefix, _VPN_ENTITY_SUFFIXES
                )
                if vpn_id is not None and vpn_id not in interface_ids:
                    entity_registry.async_remove(entity.entity_id)
                    removed_entity_ids.add(entity.unique_id)
                    continue
            if ping_check_ids is not None and entity.unique_id.startswith(ping_check_prefix):
                if entity.unique_id not in ping_check_ids:
                    entity_registry.async_remove(entity.entity_id)
                    removed_entity_ids.add(entity.unique_id)
                    continue
            if entity.unique_id.startswith(wireguard_prefix):
                identity = _wireguard_entity_parts(entity.unique_id, wireguard_prefix)
                if identity is not None:
                    interface_id, peer_id = identity
                    if interface_ids is not None and interface_id not in interface_ids:
                        entity_registry.async_remove(entity.entity_id)
                        removed_entity_ids.add(entity.unique_id)
                        continue
                    if (
                        wireguard_peer_ids is not None
                        and interface_id == wireguard_peer_ids[0]
                        and peer_id not in wireguard_peer_ids[1]
                    ):
                        entity_registry.async_remove(entity.entity_id)
                        removed_entity_ids.add(entity.unique_id)
    mesh_device_prefix = f"mesh:{entry.unique_id}|"
    vpn_device_prefix = f"vpn:{entry.unique_id}|"
    modem_device_prefix = f"modem:{entry.unique_id}|"
    device_registry = dr.async_get(hass)
    if mesh_ids is not None or interface_ids is not None:
        for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
            if not _only_used_by_entry(device, entry.entry_id):
                continue
            member_ids = {
                identifier[len(mesh_device_prefix) :]
                for domain, identifier in device.identifiers
                if domain == DOMAIN and identifier.startswith(mesh_device_prefix)
            }
            if mesh_ids is not None and member_ids and member_ids.isdisjoint(mesh_ids):
                device_registry.async_remove_device(device.id)
                continue
            vpn_identifiers = {
                identifier[len(vpn_device_prefix) :]
                for domain, identifier in device.identifiers
                if domain == DOMAIN and identifier.startswith(vpn_device_prefix)
            }
            if (
                interface_ids is not None
                and vpn_identifiers
                and vpn_identifiers.isdisjoint(interface_ids)
            ):
                device_registry.async_remove_device(device.id)
                continue
            modem_identifiers = {
                identifier[len(modem_device_prefix) :]
                for domain, identifier in device.identifiers
                if domain == DOMAIN and identifier.startswith(modem_device_prefix)
            }
            if (
                interface_ids is not None
                and modem_identifiers
                and modem_identifiers.isdisjoint(interface_ids)
            ):
                device_registry.async_remove_device(device.id)

    return removed_entity_ids
