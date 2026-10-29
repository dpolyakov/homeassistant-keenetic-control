"""Pure parsers for documented Keenetic command results."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from hashlib import sha256
from math import isfinite
from typing import Any, Literal, Union


Number = Union[int, float]

_KNOWN_VPN_INTERFACE_TYPES = frozenset({"openvpn", "pptp", "sstp", "wireguard", "zerotier"})


def parse_yes_no(value: Any) -> bool | None:
    """Parse Keenetic's documented ``yes``/``no`` and observed JSON booleans."""
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    value = value.strip().casefold()
    if value == "yes":
        return True
    if value == "no":
        return False
    return None


def parse_number(value: Any) -> Number | None:
    """Parse a finite integer or decimal without accepting booleans."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if isfinite(value) else None
    if not isinstance(value, str):
        return None

    value = value.strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        try:
            parsed = float(value)
        except ValueError:
            return None
        return parsed if isfinite(parsed) else None


def is_known_vpn_interface_type(value: Any) -> bool:
    """Match protocol types explicitly named by the reviewed Keenetic CLI manuals."""
    return isinstance(value, str) and value.strip().casefold() in _KNOWN_VPN_INTERFACE_TYPES


def select_modem_signal(
    records: Iterable[Any], field: str
) -> tuple[Number | None, Mapping[str, Any] | None]:
    """Select the strongest antenna reading, preferring SINR over CINR fallback."""
    records = list(records)
    values = [
        (parsed, record)
        for record in records
        if isinstance(record, Mapping)
        if (parsed := parse_number(record.get(field))) is not None
    ]
    if field == "sinr" and not values:
        values = [
            (parsed, record)
            for record in records
            if isinstance(record, Mapping)
            if (parsed := parse_number(record.get("cinr"))) is not None
        ]
    return max(values, key=lambda item: item[0]) if values else (None, None)


def select_modem_signal_reading(
    records: Iterable[Any], interface_info: Mapping[str, Any], field: str
) -> tuple[Number | None, Mapping[str, Any] | None]:
    """Prefer antenna readings, then direct interface fields, preserving SINR priority."""
    records = [record for record in records if isinstance(record, Mapping)]
    if field == "sinr":
        sinr_records = [record for record in records if parse_number(record.get("sinr")) is not None]
        if sinr_records:
            return select_modem_signal(sinr_records, "sinr")
        direct_sinr = parse_number(interface_info.get("sinr"))
        if direct_sinr is not None:
            return direct_sinr, interface_info
        cinr_records = [record for record in records if parse_number(record.get("cinr")) is not None]
        if cinr_records:
            return select_modem_signal(cinr_records, "cinr")
        direct_cinr = parse_number(interface_info.get("cinr"))
        if direct_cinr is not None:
            return direct_cinr, interface_info
        return None, None

    selected = select_modem_signal(records, field) if records else (None, None)
    if selected[0] is not None:
        return selected
    direct = parse_number(interface_info.get(field))
    return (direct, interface_info) if direct is not None else (None, None)


def _parse_integer(value: Any) -> int | None:
    parsed = parse_number(value)
    if isinstance(parsed, int):
        return parsed
    if isinstance(parsed, float) and parsed.is_integer():
        return int(parsed)
    return None


def memory_percent(value: Any) -> float | None:
    """Return the used percentage from the documented ``used/total`` field."""
    if not isinstance(value, str) or value.count("/") != 1:
        return None
    used_text, total_text = value.split("/", 1)
    used = parse_number(used_text)
    total = parse_number(total_text)
    if used is None or total is None or used < 0 or total <= 0 or used > total:
        return None
    return round(used * 100 / total, 1)


def parse_memory_used_percent(value: Any) -> float | None:
    """Alias named for the documented Mesh ``memory`` used/total value."""
    return memory_percent(value)


def parse_version(data: Mapping[str, Any]) -> str | None:
    """Return the firmware release string from ``show version``."""
    release = data.get("release")
    if not isinstance(release, str) or not release.strip():
        return None
    return release.strip()


def parse_system_metrics(data: Mapping[str, Any]) -> dict[str, Number | None]:
    """Parse the documented Mesh member ``system`` fields."""
    return {
        "cpuload": parse_number(data.get("cpuload")),
        "memory_percent": memory_percent(data.get("memory")),
        "uptime": parse_number(data.get("uptime")),
    }


def parse_mesh_member(data: Mapping[str, Any]) -> dict[str, Any]:
    """Parse documented Mesh member telemetry and safe device identifiers."""
    system = data.get("system")
    backhaul = data.get("backhaul")
    metrics = parse_system_metrics(system if isinstance(system, Mapping) else {})
    backhaul = backhaul if isinstance(backhaul, Mapping) else {}
    return {
        **metrics,
        "associations": parse_number(data.get("associations")),
        "backhaul_uplink": backhaul.get("uplink") if isinstance(backhaul.get("uplink"), str) else None,
        "backhaul_speed": parse_number(backhaul.get("speed")),
        "backhaul_ap": backhaul.get("ap") if isinstance(backhaul.get("ap"), str) else None,
        "backhaul_rssi": parse_number(backhaul.get("rssi")),
        "backhaul_txrate": parse_number(backhaul.get("txrate")),
        "model": data.get("model") if isinstance(data.get("model"), str) else None,
        "fw": data.get("fw") if isinstance(data.get("fw"), str) else None,
        "mac": data.get("mac") if isinstance(data.get("mac"), str) else None,
        "ip": data.get("ip") if isinstance(data.get("ip"), str) else None,
    }


def mesh_member_id(data: Mapping[str, Any]) -> str | None:
    """Return a documented stable member identifier, including CID-only records."""
    for key in ("mac", "cid", "id"):
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return None


def wireguard_peer_id(public_key: str) -> str:
    """Return a stable short registry token without placing the key in entity IDs."""
    return sha256(public_key.strip().encode("utf-8")).hexdigest()[:16]


def _parse_wireguard_peers(value: Any) -> list[dict[str, Any]] | None:
    """Parse one confirmed peer collection without retaining public keys."""
    peers = normalize_records(value)
    if peers is None:
        return None

    parsed_peers: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for peer in peers:
        public_key = peer.get("public-key")
        if not isinstance(public_key, str) or not public_key.strip():
            return None
        peer_id = wireguard_peer_id(public_key)
        if peer_id in seen_ids:
            return None
        seen_ids.add(peer_id)
        name = next(
            (
                value.strip()
                for key in ("description", "name")
                if isinstance((value := peer.get(key)), str) and value.strip()
            ),
            None,
        )
        remote_endpoint = peer.get("remote-endpoint-address") or peer.get("remote-endpoint")
        handshake = parse_number(peer.get("last-handshake"))
        parsed_peers.append(
            {
                "id": peer_id,
                "name": name.strip() if isinstance(name, str) and name.strip() else peer_id,
                "online": parse_yes_no(peer.get("online")),
                "rxbytes": parse_number(peer.get("rxbytes")),
                "txbytes": parse_number(peer.get("txbytes")),
                "last_handshake": None
                if handshake is None or handshake < 0 or handshake == 2147483647
                else handshake,
                "remote_endpoint": remote_endpoint if isinstance(remote_endpoint, str) else None,
                "remote_port": parse_number(peer.get("remote-port")),
            }
        )
    return parsed_peers


def parse_wireguard_interface_peers(value: Any) -> dict[str, dict[str, Any]] | None:
    """Parse embedded peer inventories from typed ``show/interface`` records.

    Missing or malformed peer collections are omitted per interface, so they
    remain unknown and cannot be mistaken for an empty peer list.
    """
    interfaces = normalize_interface_records(value)
    if interfaces is None:
        return None

    inventories: dict[str, dict[str, Any]] = {}
    for interface in interfaces:
        interface_type = interface.get("type")
        if not isinstance(interface_type, str) or interface_type.strip().casefold() != "wireguard":
            continue
        interface_id = str(interface.get("id")).strip()
        wireguard = interface.get("wireguard")
        if not isinstance(wireguard, Mapping) or "peer" not in wireguard:
            continue
        peers = _parse_wireguard_peers(wireguard["peer"])
        if peers is None:
            continue
        interface_label = interface.get("description") or interface.get("interface-name")
        inventories[interface_id] = {
            "interface_id": interface_id,
            "interface_name": interface.get("interface-name")
            if isinstance(interface.get("interface-name"), str)
            else interface_id,
            "interface_description": interface_label
            if isinstance(interface_label, str) and interface_label.strip()
            else interface_id,
            "peers": peers,
        }
    return inventories


def merge_wireguard_peer_sources(
    interface_peers: Any, wireguard_server: Any
) -> dict[str, dict[str, Any]]:
    """Prefer a valid server inventory for its interface, retaining other peers."""
    peer_inventories = interface_peers.items() if isinstance(interface_peers, Mapping) else ()
    merged = {
        str(interface_id): dict(inventory)
        for interface_id, inventory in peer_inventories
        if isinstance(interface_id, str) and isinstance(inventory, Mapping)
    }
    if isinstance(wireguard_server, Mapping):
        interface_id = wireguard_server.get("interface_id")
        peers = wireguard_server.get("peers")
        peer_ids = []
        if isinstance(peers, list):
            for peer in peers:
                peer_id = peer.get("id") if isinstance(peer, Mapping) else None
                if not isinstance(peer_id, str) or not peer_id.strip():
                    peer_ids = None
                    break
                peer_ids.append(peer_id)
        if (
            isinstance(interface_id, str)
            and interface_id.strip()
            and isinstance(peers, list)
            and peer_ids is not None
            and len(peer_ids) == len(set(peer_ids))
        ):
            merged[interface_id.strip()] = dict(wireguard_server)
    return merged


def parse_wireguard_server_response(value: Any) -> dict[str, Any] | None:
    """Parse the OS 5 ``show wireguard-server`` command result."""
    batch = normalize_records(value)
    if batch is None:
        return None
    interfaces = [record.get("wireguard-server", record) for record in batch]
    if len(interfaces) != 1 or not isinstance(interfaces[0], Mapping):
        return None
    interface = interfaces[0]
    interface_type = interface.get("type")
    if not isinstance(interface_type, str) or interface_type.casefold() != "wireguard":
        return None
    interface_id = interface.get("id") or interface.get("interface-name")
    if not isinstance(interface_id, str) or not interface_id.strip():
        return None
    wireguard = interface.get("wireguard")
    if not isinstance(wireguard, Mapping):
        return None
    server_owned = wireguard.get("server-owned")
    if not isinstance(server_owned, Mapping) or "peer" not in server_owned:
        return None
    peers = normalize_records(server_owned["peer"])
    if peers is None:
        return None

    parsed_peers: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for peer in peers:
        public_key = peer.get("public-key")
        if not isinstance(public_key, str) or not public_key.strip():
            return None
        public_key = public_key.strip()
        if public_key in seen_keys:
            return None
        seen_keys.add(public_key)
        name = next(
            (
                value.strip()
                for key in ("description", "name")
                if isinstance((value := peer.get(key)), str) and value.strip()
            ),
            None,
        )
        remote_endpoint = peer.get("remote-endpoint-address")
        handshake = parse_number(peer.get("last-handshake"))
        remote_port = parse_number(peer.get("remote-port"))
        parsed_peers.append(
            {
                "id": wireguard_peer_id(public_key),
                "name": name.strip() if isinstance(name, str) and name.strip() else wireguard_peer_id(public_key),
                "online": parse_yes_no(peer.get("online")),
                "rxbytes": parse_number(peer.get("rxbytes")),
                "txbytes": parse_number(peer.get("txbytes")),
                "last_handshake": None
                if handshake is None or handshake < 0 or handshake == 2147483647
                else handshake,
                "remote_endpoint": remote_endpoint if isinstance(remote_endpoint, str) else None,
                "remote_port": remote_port,
            }
        )
    return {
        "interface_id": interface_id.strip(),
        "interface_name": interface.get("interface-name")
        if isinstance(interface.get("interface-name"), str)
        else interface_id.strip(),
        "interface_description": interface.get("description")
        if isinstance(interface.get("description"), str)
        else interface_id.strip(),
        "peers": parsed_peers,
    }


def parse_hotspot_client(data: Mapping[str, Any]) -> dict[str, Any]:
    """Parse documented client inventory fields without inventing telemetry."""
    raw_mac = data.get("mac")
    mac = raw_mac.strip().lower().replace("-", ":") if isinstance(raw_mac, str) else None
    if mac is not None:
        parts = mac.split(":")
        if len(parts) != 6 or any(
            len(part) != 2 or any(character not in "0123456789abcdef" for character in part)
            for part in parts
        ):
            mac = None

    interface = data.get("interface")
    interface_values: list[str] = []
    if isinstance(interface, Mapping):
        interface_values = [
            value.strip()
            for value in interface.values()
            if isinstance(value, str) and value.strip()
        ]
        interface_id = next(
            (
                value.strip()
                for key in ("id", "name")
                if isinstance((value := interface.get(key)), str) and value.strip()
            ),
            None,
        )
        interface_name = next(
            (
                value.strip()
                for key in ("description", "name", "id")
                if isinstance((value := interface.get(key)), str) and value.strip()
            ),
            None,
        )
    else:
        interface_id = interface.strip() if isinstance(interface, str) and interface.strip() else None
        interface_name = interface.strip() if isinstance(interface, str) and interface.strip() else None
        interface_values = [interface_name] if interface_name is not None else []

    raw_link = data.get("link")
    link = raw_link if isinstance(raw_link, Mapping) else {}
    rssi = parse_number(data.get("rssi"))
    if rssi is None:
        rssi = parse_number(link.get("rssi"))
    link_value = raw_link if isinstance(raw_link, str) else link.get("state", link.get("status"))
    link_status = link_value.strip().casefold() if isinstance(link_value, str) else None
    if link_status not in {"up", "down"}:
        link_status = None
    speed = parse_number(data.get("speed"))
    if speed is None:
        speed = parse_number(link.get("speed"))
    txrate = parse_number(data.get("txrate"))
    if txrate is None:
        txrate = parse_number(link.get("txrate"))
    band = data.get("band") if isinstance(data.get("band"), str) else link.get("band")
    mode = data.get("mode") if isinstance(data.get("mode"), str) else link.get("mode")
    lower_interface_values = {value.casefold() for value in interface_values}
    is_wifi = (
        any("wifimaster" in value for value in lower_interface_values)
        or rssi is not None
        or isinstance(data.get("ssid"), str)
        or isinstance(mode, str)
        or isinstance(band, str)
    )
    is_ethernet = any(
        marker in value
        for value in lower_interface_values
        for marker in ("ethernet", "gigabit", "fastethernet", "bridge")
    )
    connection_type = "Wi-Fi" if is_wifi else "Ethernet" if is_ethernet else None

    return {
        "mac": mac,
        "name": next(
            (
                value.strip()
                for key in ("name", "hostname")
                if isinstance((value := data.get(key)), str) and value.strip()
            ),
            mac,
        ),
        "ip": data.get("ip") if isinstance(data.get("ip"), str) and data.get("ip").strip() else None,
        "interface": interface_name,
        "interface_id": interface_id,
        "connection_type": connection_type,
        "active": parse_yes_no(data.get("active")),
        "dhcp_registered": parse_yes_no(data.get("registered")),
        "link_status": link_status,
        "link_speed": speed if speed is not None else txrate,
        "uptime": parse_number(data.get("uptime")),
        "rxbytes": _nonnegative_number(data.get("rxbytes")),
        "txbytes": _nonnegative_number(data.get("txbytes")),
        "rssi": rssi,
        "txrate": txrate,
        "wifi_band": band.strip() if isinstance(band, str) and band.strip() else None,
        "wifi_mode": mode.strip() if isinstance(mode, str) and mode.strip() else None,
    }


def client_is_live(client: Mapping[str, Any]) -> bool:
    """Return whether a parsed hotspot row represents a currently live client."""
    if client.get("active") is False or client.get("link_status") == "down":
        return False
    return True


def _traffic_shape_records(value: Any) -> list[Mapping[str, Any]] | None:
    """Normalize the supported shapes of the per-host traffic-shape resource."""
    if isinstance(value, Mapping):
        for key in ("host", "hosts", "rule", "rules", "traffic-shape", "traffic_shape"):
            if key in value:
                return _traffic_shape_records(value[key])
        if "mac" in value:
            return [value]
        keyed: list[Mapping[str, Any]] = []
        for raw_mac, details in value.items():
            if not isinstance(raw_mac, str):
                continue
            normalized = raw_mac.strip().lower().replace("-", ":")
            if len(normalized.split(":")) != 6:
                continue
            if isinstance(details, Mapping):
                row = dict(details)
                row.setdefault("mac", raw_mac)
                keyed.append(row)
            elif parse_number(details) is not None:
                keyed.append({"mac": raw_mac, "rate": details})
        return keyed if keyed else None
    if isinstance(value, (list, tuple)) and all(isinstance(item, Mapping) for item in value):
        return list(value)
    return None


def parse_traffic_shape(value: Any) -> dict[str, int]:
    """Return confirmed equal per-host bandwidth limits in kbit/s."""
    records = _traffic_shape_records(value)
    if records is None:
        return {}
    result: dict[str, int] = {}
    for record in records:
        client = parse_hotspot_client(record)
        mac = client["mac"]
        if mac is None:
            continue
        rate = parse_number(record.get("rate", record.get("limit")))
        downstream = parse_number(
            record.get("downstream-rate", record.get("download-rate", record.get("rxrate")))
        )
        upstream = parse_number(
            record.get("upstream-rate", record.get("upload-rate", record.get("txrate")))
        )
        if rate is None:
            if downstream is not None and upstream is not None:
                rate = downstream if downstream == upstream else None
            else:
                rate = downstream if downstream is not None else upstream
        if rate is None or rate < 64 or rate > 1_000_000 or not float(rate).is_integer():
            continue
        result[mac] = int(rate)
    return result


def wifi_client_count(response: Any) -> int | None:
    """Count distinct active hosts that can be classified as Wi-Fi clients."""
    records = normalize_records(response, "host")
    if records is None:
        records = normalize_records(response)
    if records is None:
        return None

    addresses: set[str] = set()
    for record in records:
        if parse_yes_no(record.get("active")) is False:
            continue
        client = parse_hotspot_client(record)
        if client["mac"] is None:
            continue
        interface = record.get("interface")
        interface_names: list[str] = []
        if isinstance(interface, Mapping):
            interface_names.extend(
                value
                for key in ("id", "name", "description")
                if isinstance((value := interface.get(key)), str)
            )
        elif isinstance(interface, str):
            interface_names.append(interface)
        if any("wifimaster" in value.strip().casefold() for value in interface_names):
            addresses.add(client["mac"])
            continue
        link = record.get("link")
        has_rssi = parse_number(record.get("rssi")) is not None or (
            isinstance(link, Mapping) and parse_number(link.get("rssi")) is not None
        )
        if has_rssi:
            addresses.add(client["mac"])
    return len(addresses)


def _nonnegative_number(value: Any) -> Number | None:
    parsed = parse_number(value)
    return parsed if parsed is not None and parsed >= 0 else None


INTERNET_STATUS_FIELDS = (
    "reliable",
    "gateway-accessible",
    "dns-accessible",
    "host-accessible",
    "internet",
)


def parse_internet_status(data: Mapping[str, Any]) -> dict[str, bool | None]:
    """Parse supported status fields from ``show internet status``."""
    result = {field: parse_yes_no(data.get(field)) for field in INTERNET_STATUS_FIELDS}
    if result["host-accessible"] is None:
        result["host-accessible"] = parse_yes_no(data.get("captive-accessible"))
    return result


def parse_ping_checks(value: Any) -> list[dict[str, str | None]] | None:
    """Normalize configured Ping Check results without guessing unknown states."""
    if isinstance(value, Mapping) and "pingcheck" in value:
        records = normalize_records(value, "pingcheck")
    else:
        records = normalize_records(value)
    if records is None:
        return None

    result: list[dict[str, str | None]] = []
    for record in records:
        interface = record.get("interface")
        profile = record.get("profile")
        if not isinstance(interface, str) or not interface.strip():
            continue
        if not isinstance(profile, str) or not profile.strip():
            continue
        status = record.get("status")
        normalized_status = status.strip().casefold() if isinstance(status, str) else ""
        if normalized_status not in {"pass", "fail"}:
            normalized_status = ""
        result.append(
            {
                "interface": interface.strip(),
                "profile": profile.strip(),
                "status": normalized_status or None,
            }
        )
    return result


def _normalize_named_records(
    value: Any,
    *,
    collection_key: str | None = None,
    record_field: str,
) -> list[Mapping[str, Any]] | None:
    """Normalize a list, one record, or an RCI mapping keyed by record name."""
    if collection_key is not None and isinstance(value, Mapping) and collection_key in value:
        value = value[collection_key]
    if isinstance(value, (list, tuple)):
        return normalize_records(value)
    if not isinstance(value, Mapping):
        return None
    if not value:
        return []
    if record_field in value:
        return [value]
    records = list(value.values())
    if any(not isinstance(record, Mapping) or record_field not in record for record in records):
        return None
    return records


def parse_usb_storage_devices(value: Any) -> list[Mapping[str, Any]] | None:
    """Return observed USB storage/media rows; None means the list was unknown."""
    devices = _normalize_named_records(
        value,
        collection_key="device",
        record_field="subsystem",
    )
    if devices is None:
        return None
    return [
        device
        for device in devices
        if isinstance(device.get("subsystem"), str)
        and device["subsystem"].strip().casefold() in {"storage", "media"}
    ]


def parse_usb_media_status(value: Any) -> dict[str, Any] | None:
    """Summarize mounted volumes from the documented ``show media`` result.

    The CLI reference documents the ``media`` rows, their state, partitions,
    and partition byte counts. Missing or malformed state is unknown, never an
    empty or unmounted device.
    """
    media = _normalize_named_records(
        value,
        collection_key="media",
        record_field="state",
    )
    if media is None:
        return None

    volume_count = 0
    mounted: list[Mapping[str, Any]] = []
    for device in media:
        if not isinstance(device.get("state"), str) or not device["state"].strip():
            return None
        partitions = _normalize_named_records(
            device.get("partition"),
            record_field="state",
        )
        if partitions is None:
            return None
        volume_count += len(partitions)
        for partition in partitions:
            state = partition.get("state")
            if not isinstance(state, str) or not state.strip():
                return None
            normalized_state = state.strip().casefold()
            if normalized_state == "mounted":
                mounted.append(partition)
            else:
                # The reviewed CLI reference documents MOUNTED but does not
                # define the complete set of other partition states.
                return None

    def total(field: str) -> int | None:
        if not mounted:
            return 0
        values = [_parse_integer(partition.get(field)) for partition in mounted]
        if any(value is None or value < 0 for value in values):
            return None
        return sum(value for value in values if value is not None)

    return {
        "available": bool(mounted),
        "media_count": len(media),
        "volume_count": volume_count,
        "mounted_volume_count": len(mounted),
        "mounted_total_bytes": total("total"),
        "mounted_free_bytes": total("free"),
    }


def parse_dns_proxy_diagnostics(value: Any) -> dict[str, Any] | None:
    """Parse the DNS Proxy diagnostics used by Keenetic-Router-Pro."""
    if not isinstance(value, Mapping):
        return None
    if "proxy" in value:
        raw_proxies = value["proxy"]
    elif "proxies" in value:
        raw_proxies = value["proxies"]
    else:
        return None
    if isinstance(raw_proxies, Mapping):
        proxies = [raw_proxies]
    elif isinstance(raw_proxies, list):
        proxies = raw_proxies
    else:
        return None

    proxy_count = 0
    active_upstream = False
    for proxy in proxies:
        if not isinstance(proxy, Mapping):
            continue
        proxy_count += 1
        is_active = proxy.get("active") in (True, "true", "yes", 1, "1")
        if is_active:
            active_upstream = True

    stats = value.get("statistics") or value.get("stats")
    if not isinstance(stats, Mapping):
        stats = {}
    failed_requests = None
    for key in ("requests-failed", "requests_failed", "failed"):
        if key in stats:
            failed_requests = _parse_integer(stats[key])
            break

    return {
        "status": "down" if proxy_count == 0 else "ok" if active_upstream else "degraded",
        "failed_requests": failed_requests,
    }


def parse_ipsec_vici_diagnostics(value: Any) -> dict[str, Any] | None:
    """Summarize only the VICI out-of-memory marker from the router log."""
    lines: list[str] = []

    def collect(item: Any) -> bool:
        if isinstance(item, str):
            lines.extend(item.splitlines())
            return True
        if isinstance(item, list):
            recognized = True
            for child in item:
                recognized = collect(child) and recognized
            return recognized
        if isinstance(item, Mapping):
            found = False
            for key in ("log", "message", "msg"):
                if key in item:
                    found = collect(item[key]) or found
            return found
        return False

    if not collect(value):
        return None
    marker = "IpSec::Vici::Stats: out of memory"
    count = sum(marker.casefold() in line.casefold() for line in lines[-2000:])
    return {"status": "warning" if count else "ok", "out_of_memory_count": count}


def parse_lte_data_usage(value: Any) -> dict[str, Any] | None:
    """Convert the reference traffic-counter response to validated GB values."""
    if not isinstance(value, Mapping) or value.get("enabled") is not True:
        return None
    unit = value.get("unit")
    if not isinstance(unit, str):
        return None
    multipliers = {
        "B": 1 / (1024**3),
        "BYTES": 1 / (1024**3),
        "KB": 1 / (1024**2),
        "MB": 1 / 1024,
        "GB": 1,
        "TB": 1024,
    }
    multiplier = multipliers.get(unit.strip().upper())
    if multiplier is None:
        return None

    result: dict[str, Any] = {}
    for source, target in (
        ("value", "used_gb"),
        ("remaining", "remaining_gb"),
        ("limit", "limit_gb"),
        ("threshold", "threshold_gb"),
    ):
        number = parse_number(value.get(source))
        if number is not None and number >= 0:
            result[target] = number * multiplier
    days_left = _parse_integer(value.get("days-left"))
    if days_left is not None and days_left >= 0:
        result["days_left"] = days_left

    limit = result.get("limit_gb")
    used = result.get("used_gb")
    if isinstance(limit, (int, float)) and limit > 0 and isinstance(used, (int, float)):
        result["usage_percent"] = min(100.0, used * 100 / limit)

    trigger = value.get("trigger")
    if isinstance(trigger, Mapping):
        for source, target in (("limit", "limit_exceeded"), ("threshold", "threshold_exceeded")):
            flag = trigger.get(source)
            if isinstance(flag, bool):
                result[target] = flag
            elif isinstance(flag, str) and flag.strip().casefold() in {"yes", "no", "true", "false"}:
                result[target] = flag.strip().casefold() in {"yes", "true"}
    return result


def parse_modem_cells(value: Any) -> list[dict[str, Number | str]] | None:
    """Parse documented mobile base-station identity and RSSI fields."""
    records = normalize_records(value, "cells")
    if records is None:
        return None

    parsed: list[dict[str, Number | str]] = []
    for record in records:
        raw_cell_id = record.get("phy-id")
        if isinstance(raw_cell_id, bool) or not isinstance(raw_cell_id, (str, int)):
            return None
        cell_id = str(raw_cell_id).strip()
        rssi = parse_number(record.get("rssi"))
        if not cell_id or rssi is None:
            return None
        parsed.append({"cell_id": cell_id, "rssi": rssi})
    return parsed


@dataclass(frozen=True)
class RouteSelection:
    """Result of selecting the IPv4 default route from a successful route read."""

    state: Literal["unique", "absent", "ambiguous", "unknown"]
    interface: str | None = None
    gateway: str | None = None
    metric: int | None = None


def select_default_route(routes: Iterable[Mapping[str, Any]] | None) -> RouteSelection:
    """Select the lowest-metric IPv4 default route without guessing on ties.

    ``absent`` means a valid route list contained no ``0.0.0.0/0`` row. A
    malformed row that could affect selection returns ``unknown`` so callers
    can distinguish bad data from a confirmed route disappearance.
    """
    if routes is None or isinstance(routes, (str, bytes, Mapping)):
        return RouteSelection("unknown")

    candidates: list[tuple[int, str, str | None]] = []
    try:
        iterator = iter(routes)
    except TypeError:
        return RouteSelection("unknown")

    for row in iterator:
        if not isinstance(row, Mapping):
            return RouteSelection("unknown")
        destination = row.get("destination")
        if not isinstance(destination, str):
            return RouteSelection("unknown")
        if destination != "0.0.0.0/0":
            continue

        metric = _parse_integer(row.get("metric"))
        interface = row.get("interface")
        if metric is None or metric < 0 or not isinstance(interface, str) or not interface.strip():
            return RouteSelection("unknown")
        gateway_value = row.get("gateway")
        gateway = gateway_value if isinstance(gateway_value, str) else None
        candidates.append((metric, interface.strip(), gateway))

    if not candidates:
        return RouteSelection("absent")

    best_metric = min(candidate[0] for candidate in candidates)
    best = [candidate for candidate in candidates if candidate[0] == best_metric]
    interfaces = {candidate[1] for candidate in best}
    if len(interfaces) != 1:
        return RouteSelection("ambiguous", metric=best_metric)

    gateways = {candidate[2] for candidate in best}
    gateway = next(iter(gateways)) if len(gateways) == 1 else None
    return RouteSelection("unique", interfaces.pop(), gateway, best_metric)


def default_route(routes: Iterable[Mapping[str, Any]] | None) -> tuple[str, str | None]:
    """Return route state and interface using the caller-facing state names."""
    selection = select_default_route(routes)
    state = {
        "unique": "known",
        "absent": "missing",
        "ambiguous": "ambiguous",
        "unknown": "unknown",
    }[selection.state]
    return state, selection.interface


def normalize_records(value: Any, collection_key: str | None = None) -> list[Mapping[str, Any]] | None:
    """Normalize a documented record or sequence, preserving malformed as unknown.

    When ``collection_key`` is provided, it is unwrapped from a response object;
    an already-unwrapped sequence is also accepted for RCI ``show`` results.
    ``None`` means the shape was missing or malformed; an empty list means the
    response explicitly contained no records.
    """
    if collection_key is not None:
        if isinstance(value, Mapping) and collection_key in value:
            value = value[collection_key]
        elif not isinstance(value, (list, tuple)):
            return None

    if isinstance(value, Mapping):
        return [value]
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return None

    records = list(value)
    if any(not isinstance(record, Mapping) for record in records):
        return None
    return records


def normalize_interface_records(value: Any) -> list[Mapping[str, Any]] | None:
    """Normalize the keyed mapping returned by ``show interface``.

    Keenetic commonly keys interface records by their interface name rather
    than returning a list. A malformed or ambiguous inventory remains unknown
    so it cannot trigger dynamic registry cleanup.
    """
    if isinstance(value, Mapping) and "interface" in value:
        value = value["interface"]

    if isinstance(value, Mapping):
        if "id" in value:
            records: list[Mapping[str, Any]] = [value]
        else:
            if not value:
                return None
            records = list(value.values())
            if any(not isinstance(record, Mapping) for record in records):
                return None
    elif isinstance(value, (list, tuple)):
        records = list(value)
        if any(not isinstance(record, Mapping) for record in records):
            return None
    else:
        return None

    identifiers: set[str] = set()
    for record in records:
        identifier = record.get("id")
        if isinstance(identifier, bool) or not isinstance(identifier, (str, int)):
            return None
        normalized = str(identifier).strip()
        if not normalized or normalized in identifiers:
            return None
        identifiers.add(normalized)
    return records


_INTERFACE_STRING_FIELDS = (
    "id",
    "type",
    "description",
    "interface-name",
    "link",
    "state",
    "address",
    "mask",
    "security-level",
    "mac",
    "auth-type",
)
_INTERFACE_NUMBER_FIELDS = ("index", "mtu", "tx-queue", "uptime")


def parse_interface_fields(data: Mapping[str, Any]) -> dict[str, Any]:
    """Keep and parse fields shown in the documented RCI interface example."""
    parsed: dict[str, Any] = {}
    for field in _INTERFACE_STRING_FIELDS:
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            parsed[field] = value.strip()
    connected = parse_yes_no(data.get("connected"))
    if connected is not None:
        parsed["connected"] = connected
    for field in _INTERFACE_NUMBER_FIELDS:
        value = parse_number(data.get(field))
        if value is not None:
            parsed[field] = value
    return parsed


_MODEM_TEXT_FIELDS = (
    "manufacturer",
    "model",
    "vendor",
    "fw",
    "serial",
    "phone-number",
    "apn",
    "bssid",
    "enb-id",
    "sector-id",
    "tac",
    "phy-cell-id",
    "band",
    "mobile",
    "connection-state",
    "imsi",
    "iccid",
    "operator",
    "operator-raw",
    "plmn",
    "sim",
    "imei",
    "product",
)
_MODEM_NUMBER_FIELDS = (
    "signal-level",
    "rssi",
    "rsrp",
    "cinr",
    "rsrq",
    "temperature",
    "pin-attempts",
    "earfcn",
    "dl-freq",
    "ul-freq",
    "bandwidth",
)
_CARRIER_TEXT_FIELDS = ("mobile", "phy-cell-id", "band")
_CARRIER_NUMBER_FIELDS = ("earfcn", "dl-freq", "ul-freq", "bandwidth")


def _parse_carriers(value: Any) -> dict[str, dict[str, Any]] | None:
    if isinstance(value, Mapping):
        if not value:
            return {}
        if any(not isinstance(record, Mapping) for record in value.values()):
            return None
        records = value.items()
    elif isinstance(value, (list, tuple)):
        if any(not isinstance(record, Mapping) for record in value):
            return None
        records = ((str(index), record) for index, record in enumerate(value))
    else:
        return None

    parsed: dict[str, dict[str, Any]] = {}
    for key, record in records:
        item: dict[str, Any] = {}
        active = record.get("active")
        if isinstance(active, bool):
            item["active"] = active
        for field in _CARRIER_TEXT_FIELDS:
            text = record.get(field)
            if isinstance(text, str) and text.strip():
                item[field] = text.strip()
        for field in _CARRIER_NUMBER_FIELDS:
            number = parse_number(record.get(field))
            if number is not None:
                item[field] = number
        parsed[str(key)] = item
    return parsed


def parse_modem_interface_fields(data: Mapping[str, Any]) -> dict[str, Any]:
    """Parse observed optional modem fields without assuming undocumented units."""
    parsed: dict[str, Any] = {}
    for field in _MODEM_TEXT_FIELDS:
        value = data.get(field)
        if isinstance(value, str) and value.strip():
            parsed[field] = value.strip()
    for field in _MODEM_NUMBER_FIELDS:
        number = parse_number(data.get(field))
        if number is not None:
            parsed[field] = number
    plugged = parse_yes_no(data.get("plugged"))
    if plugged is not None:
        parsed["plugged"] = plugged
    roaming = data.get("roaming")
    if isinstance(roaming, bool):
        parsed["roaming"] = roaming
    else:
        roaming = parse_yes_no(roaming)
        if roaming is not None:
            parsed["roaming"] = roaming
    carriers = _parse_carriers(data.get("carrier")) if "carrier" in data else None
    if carriers is not None:
        parsed["carrier"] = carriers
    ati = data.get("ati")
    if isinstance(ati, Mapping):
        details = {
            key: value.strip()
            for key in ("manufacturer", "model", "revision")
            if isinstance((value := ati.get(key)), str) and value.strip()
        }
        if details:
            parsed["ati"] = details
    return parsed


STAT_COUNTER_FIELDS = (
    "rxpackets",
    "rx-multicast-packets",
    "rx-broadcast-packets",
    "rxbytes",
    "rxerrors",
    "rxdropped",
    "txpackets",
    "tx-multicast-packets",
    "tx-broadcast-packets",
    "txbytes",
    "txerrors",
    "txdropped",
)


def parse_stat_counters(data: Mapping[str, Any]) -> dict[str, Number]:
    """Parse the packet/byte counters documented under ``statistic``."""
    counters: dict[str, Number] = {}
    for field in STAT_COUNTER_FIELDS:
        value = parse_number(data.get(field))
        if value is not None and value >= 0:
            counters[field] = value
    return counters


def complete_sms(message: Mapping[str, Any]) -> bool | None:
    """Return whether documented SMS part counts prove the message complete."""
    parts = _parse_integer(message.get("parts"))
    total_parts = _parse_integer(message.get("total-parts"))
    if parts is None or total_parts is None or parts <= 0 or total_parts <= 0 or parts > total_parts:
        return None
    return parts == total_parts


def sms_fingerprint(message: Mapping[str, Any]) -> str | None:
    """Hash stable documented SMS identity fields, excluding read state."""
    normalized = parse_sms_message(message)
    if normalized["id"] is None:
        return None
    identity = (
        normalized["id"],
        normalized["from"],
        normalized["timestamp"],
        normalized["parts"],
        normalized["total_parts"],
        normalized["text"] if normalized["complete"] is True else None,
    )
    payload = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def parse_sms_message(data: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize one documented SMS list record and hide partial text."""
    parts = _parse_integer(data.get("parts"))
    total_parts = _parse_integer(data.get("total-parts"))
    complete = complete_sms(data)

    raw_text = data.get("text")
    text = raw_text if complete is True and isinstance(raw_text, str) else None
    message_id = data.get("id")
    sender = data.get("from")
    timestamp = data.get("timestamp")
    return {
        "id": message_id.strip() if isinstance(message_id, str) and message_id.strip() else None,
        "from": sender if isinstance(sender, str) else None,
        "timestamp": timestamp if isinstance(timestamp, str) else None,
        "read": parse_yes_no(data.get("read")),
        "parts": parts if parts is not None and parts > 0 else None,
        "total_parts": total_parts if total_parts is not None and total_parts > 0 else None,
        "complete": complete,
        "text": text,
    }


def parse_sms_messages(messages: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Normalize SMS records; malformed response shapes are not empty lists."""
    if isinstance(messages, (str, bytes, Mapping)):
        raise TypeError("messages must be an iterable of SMS records")
    records = normalize_records(messages)
    if records is None:
        raise TypeError("messages must be an iterable of SMS records")
    return [parse_sms_message(message) for message in records]


def parse_sms_list_response(value: Any) -> list[dict[str, Any]] | None:
    """Accept only the documented repeated ``messages`` collection."""
    if not isinstance(value, Mapping) or "messages" not in value:
        return None
    records = normalize_records(value, "messages")
    return parse_sms_messages(records) if records is not None else None


def has_sms_service(value: Any) -> bool:
    """Return true only when a completed modem poll confirms SMS support."""
    return isinstance(value, Mapping) and value.get("sms_available") is True
