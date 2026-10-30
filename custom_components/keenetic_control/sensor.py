"""Sensors backed by the router's last successful RCI poll."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from ipaddress import ip_interface, ip_address
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    UnitOfDataRate,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import ChildDeviceInfo, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client_registry import client_display_name, normalize_mac, selected_client_macs
from .const import CONF_ADDRESS, CONF_PORT, DOMAIN
from .coordinator import KeeneticCoordinator
from .device_helpers import router_device_id
from .entity_model import CounterSample, InterfaceEntityModel, build_interface_entity_model
from .model import (
    client_is_live,
    mesh_member_id,
    normalize_records,
    parse_lte_data_usage,
    parse_hotspot_client,
    parse_mesh_member,
    parse_modem_interface_fields,
    parse_number,
    parse_stat_counters,
    parse_system_metrics,
    parse_sms_list_response,
    select_modem_signal_reading,
    wifi_client_count,
)


@dataclass(frozen=True)
class _SensorSpec:
    """Definition for a sensor whose value is read from coordinator data."""

    unique_id: str
    name: str
    value: Callable[[Mapping[str, Any]], Any]
    unit: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    attributes: Callable[[Mapping[str, Any]], dict[str, Any]] | None = None
    device_id: str | None = None
    device_name: str | None = None
    rate_counter: Callable[[Mapping[str, Any]], CounterSample | None] | None = None
    rate_only: bool = False
    translation_key: str | None = None
    translation_placeholders: dict[str, str] | None = None
    entity_category: EntityCategory | None = None
    suggested_unit: str | None = None
    suggested_precision: int | None = None


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _device_identifier(entry: ConfigEntry, device_id: str) -> str:
    """Return the stable registry identifier shared with device actions."""
    if device_id.startswith("modem:"):
        return f"modem:{entry.unique_id}|{device_id.removeprefix('modem:')}"
    if device_id.startswith("mesh:"):
        return f"mesh:{entry.unique_id}|{device_id.removeprefix('mesh:')}"
    if device_id.startswith("vpn:"):
        return f"vpn:{entry.unique_id}|{device_id.removeprefix('vpn:')}"
    if device_id.startswith("client:"):
        return f"{entry.unique_id}:client:{device_id.removeprefix('client:')}"
    return f"{entry.unique_id}:{device_id}"


def _system_metrics(data: Mapping[str, Any]) -> dict[str, Any]:
    system = _mapping(data.get("system"))
    parsed = parse_system_metrics(system or {})
    cpu = _mapping(data.get("cpu"))
    if cpu is not None:
        fallback = parse_system_metrics(cpu)
        for key, value in fallback.items():
            if parsed.get(key) is None:
                parsed[key] = value
    return parsed


def _router_uptime(data: Mapping[str, Any]) -> int | float | None:
    uptime = parse_number(_system_metrics(data).get("uptime"))
    if uptime is None or uptime < 0:
        return None
    return uptime


def _router_uptime_attributes(data: Mapping[str, Any]) -> dict[str, Any]:
    uptime = _router_uptime(data)
    if uptime is None:
        return {}
    observed_at = data.get("observed_at")
    if not isinstance(observed_at, datetime):
        observed_at = datetime.now(UTC)
    return {"last_boot": observed_at - timedelta(seconds=float(uptime))}


def _wifi_client_count(data: Mapping[str, Any]) -> int | None:
    """Count distinct Wi-Fi clients only when each hotspot row is classifiable."""
    return wifi_client_count(data.get("hotspot"))


def _interface_model(data: Mapping[str, Any]) -> InterfaceEntityModel:
    return build_interface_entity_model(
        data.get("interface_records"),
        data.get("routes"),
    )


def _default_route_interface(data: Mapping[str, Any]) -> str | None:
    interface = _interface_model(data).primary_wan
    return interface.name if interface is not None else None


def _default_route_address(data: Mapping[str, Any]) -> str | None:
    interface = _interface_model(data).primary_wan
    if interface is None or interface.address is None:
        return None
    try:
        return str(ip_interface(interface.address).ip)
    except ValueError:
        try:
            return str(ip_address(interface.address))
        except ValueError:
            return None


def _router_temperature(data: Mapping[str, Any]) -> int | float | None:
    reading = _interface_model(data).router_temperature
    return reading.value if reading is not None else None


def _router_temperature_attributes(data: Mapping[str, Any]) -> dict[str, Any]:
    reading = _interface_model(data).router_temperature
    return {"source": reading.source} if reading is not None else {}


def _primary_wan_counter(
    data: Mapping[str, Any], field: str
) -> CounterSample | None:
    if field not in {"rxbytes", "txbytes"}:
        return None
    return _interface_model(data).primary_wan_counter(field)


def _rate_from_samples(
    previous: CounterSample | None,
    current: CounterSample | None,
    elapsed: float,
) -> float | None:
    """Convert one stable cumulative counter delta to megabits per second."""
    if (
        previous is None
        or current is None
        or previous.source != current.source
        or elapsed <= 0
        or current.value < previous.value
    ):
        return None
    return (current.value - previous.value) * 8 / elapsed / 1_000_000


def _root_specs(entry: ConfigEntry, data: Mapping[str, Any]) -> list[_SensorSpec]:
    prefix = str(entry.unique_id)
    metrics = _system_metrics
    specs = [
        _SensorSpec(
            f"{prefix}_firmware_version",
            "Firmware version",
            lambda data: _firmware_version(data),
            attributes=_firmware_attributes,
            translation_key="firmware_version",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        _SensorSpec(
            f"{prefix}_cpu_load",
            "CPU load",
            lambda data: metrics(data).get("cpuload"),
            "%",
            state_class=SensorStateClass.MEASUREMENT,
            translation_key="cpu_load",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        _SensorSpec(
            f"{prefix}_memory_used",
            "Memory used",
            lambda data: metrics(data).get("memory_percent"),
            "%",
            state_class=SensorStateClass.MEASUREMENT,
            translation_key="memory_used",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        _SensorSpec(
            f"{prefix}_uptime",
            "Uptime",
            _router_uptime,
            UnitOfTime.SECONDS,
            device_class=SensorDeviceClass.DURATION,
            state_class=SensorStateClass.MEASUREMENT,
            attributes=_router_uptime_attributes,
            translation_key="uptime",
            entity_category=EntityCategory.DIAGNOSTIC,
            suggested_unit=UnitOfTime.DAYS,
        ),
        _SensorSpec(
            f"{prefix}_wifi_clients",
            "Connected Wi-Fi clients",
            _wifi_client_count,
            state_class=SensorStateClass.MEASUREMENT,
            translation_key="wifi_clients",
        ),
        _SensorSpec(
            f"{prefix}_default_route_interface",
            "Default route interface",
            _default_route_interface,
            translation_key="default_route_interface",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
        _SensorSpec(
            f"{prefix}_default_route_ip",
            "Default route interface IP",
            _default_route_address,
            translation_key="default_route_ip",
            entity_category=EntityCategory.DIAGNOSTIC,
        ),
    ]
    specs.extend(_router_port_specs(entry, data))
    if _interface_model(data).router_temperature is not None:
        specs.append(
            _SensorSpec(
                f"{prefix}_temperature",
                "Temperature",
                _router_temperature,
                UnitOfTemperature.CELSIUS,
                SensorDeviceClass.TEMPERATURE,
                SensorStateClass.MEASUREMENT,
                attributes=_router_temperature_attributes,
                translation_key="router_temperature",
                entity_category=EntityCategory.DIAGNOSTIC,
            )
        )
    for field, direction, name in (
        ("rxbytes", "received", "Download rate"),
        ("txbytes", "sent", "Upload rate"),
    ):
        if _primary_wan_counter(data, field) is None:
            continue
        specs.append(
            _SensorSpec(
                f"{prefix}_wan_{direction}_rate",
                name,
                lambda _values: None,
                UnitOfDataRate.MEGABITS_PER_SECOND,
                SensorDeviceClass.DATA_RATE,
                SensorStateClass.MEASUREMENT,
                rate_counter=lambda values, key=field: _primary_wan_counter(values, key),
                rate_only=True,
                translation_key=f"wan_{direction}_rate",
                entity_category=EntityCategory.DIAGNOSTIC,
            )
        )
    return specs


def _diagnostic_specs(entry: ConfigEntry, data: Mapping[str, Any]) -> list[_SensorSpec]:
    """Create the optional router diagnostic entities from the reference set."""
    prefix = str(entry.unique_id)
    specs: list[_SensorSpec] = []
    dns = _mapping(data.get("dns_proxy"))
    if dns is not None:
        specs.append(
            _SensorSpec(
                f"{prefix}_dns_proxy_status",
                "DNS Proxy status",
                lambda values: (_mapping(values.get("dns_proxy")) or {}).get("status"),
                translation_key="dns_proxy_status",
                entity_category=EntityCategory.DIAGNOSTIC,
            )
        )
        if dns.get("failed_requests") is not None:
            specs.append(
                _SensorSpec(
                    f"{prefix}_dns_proxy_failed_requests",
                    "DNS Proxy failed requests",
                    lambda values: (_mapping(values.get("dns_proxy")) or {}).get("failed_requests"),
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    translation_key="dns_proxy_failed_requests",
                    entity_category=EntityCategory.DIAGNOSTIC,
                )
            )

    ipsec = _mapping(data.get("ipsec_diagnostics"))
    if ipsec is not None:
        specs.extend(
            (
                _SensorSpec(
                    f"{prefix}_ipsec_vici_status",
                    "IPsec VICI status",
                    lambda values: (_mapping(values.get("ipsec_diagnostics")) or {}).get("status"),
                    translation_key="ipsec_vici_status",
                    entity_category=EntityCategory.DIAGNOSTIC,
                ),
                _SensorSpec(
                    f"{prefix}_ipsec_vici_out_of_memory",
                    "IPsec VICI out-of-memory events",
                    lambda values: (_mapping(values.get("ipsec_diagnostics")) or {}).get("out_of_memory_count"),
                    state_class=SensorStateClass.TOTAL_INCREASING,
                    translation_key="ipsec_vici_out_of_memory",
                    entity_category=EntityCategory.DIAGNOSTIC,
                ),
            )
        )
    return specs


def _firmware_version(data: Mapping[str, Any]) -> str | None:
    version = _mapping(data.get("version")) or {}
    release = version.get("release")
    title = version.get("title")
    for value in (release, title):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _firmware_attributes(data: Mapping[str, Any]) -> dict[str, Any]:
    version = _mapping(data.get("version")) or {}
    fields = ("release", "title", "arch", "model", "hw_id", "region")
    return {key: version[key] for key in fields if isinstance(version.get(key), str)}


def _interface_id(record: Mapping[str, Any]) -> str | None:
    value = record.get("id")
    return str(value).strip() if isinstance(value, (str, int)) and str(value).strip() else None


def _interface_label(record: Mapping[str, Any], interface_id: str) -> str:
    for key in ("description", "interface-name"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return interface_id


def _interface_records(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    records = data.get("interface_records")
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, Mapping) and _interface_id(record)]


def _interface_counters(data: Mapping[str, Any], interface_id: str) -> dict[str, int | float]:
    record = next(
        (item for item in _interface_records(data) if _interface_id(item) == interface_id),
        None,
    )
    if record is None:
        return {}
    statistics = _mapping(record.get("statistic"))
    return parse_stat_counters(statistics or record)


def _modem_antenna_records(data: Mapping[str, Any], interface_id: str) -> list[Mapping[str, Any]] | None:
    modems = _mapping(data.get("modems")) or {}
    modem = _mapping(modems.get(interface_id))
    if modem is None:
        return None
    response = modem.get("antennas")
    records = normalize_records(response, "antenna")
    if records is not None:
        return records
    # Some router builds return one antenna record without a collection wrapper.
    if _mapping(response) is not None and any(field in response for field in ("rssi", "rsrp", "rsrq", "sinr", "cinr")):
        return [response]
    return normalize_records(response)


def _modem_interface_info(data: Mapping[str, Any], interface_id: str) -> Mapping[str, Any] | None:
    modems = _mapping(data.get("modems")) or {}
    modem = _mapping(modems.get(interface_id))
    return _mapping(modem.get("interface_info")) if modem is not None else None


def _modem_signal_reading(
    data: Mapping[str, Any], interface_id: str, field: str
) -> tuple[int | float | None, Mapping[str, Any] | None]:
    """Select antenna readings, then fall back to values on the interface record."""
    records = _modem_antenna_records(data, interface_id)
    interface_info = _modem_interface_info(data, interface_id) or {}
    return select_modem_signal_reading(records or [], interface_info, field)


def _interface_uptime(data: Mapping[str, Any], interface_id: str) -> int | float | None:
    record = next(
        (item for item in _interface_records(data) if _interface_id(item) == interface_id),
        None,
    )
    return parse_number(record.get("uptime")) if record is not None else None


def _hotspot_client_records(data: Mapping[str, Any]) -> list[Mapping[str, Any]] | None:
    hotspot = data.get("hotspot")
    if hotspot is None:
        return None
    records = normalize_records(hotspot, "host")
    if records is not None:
        return records
    records = normalize_records(hotspot)
    if records is None or any("mac" not in record for record in records):
        return None
    return records


def _client_record(data: Mapping[str, Any], mac: str) -> Mapping[str, Any] | None:
    records = _hotspot_client_records(data)
    if records is None:
        return None
    for record in records:
        details = parse_hotspot_client(record)
        if details["mac"] == mac and client_is_live(details):
            return record
    return None


def _client_details(data: Mapping[str, Any], mac: str) -> dict[str, Any] | None:
    record = _client_record(data, mac)
    return parse_hotspot_client(record) if record is not None else None


def _client_value(data: Mapping[str, Any], mac: str, field: str) -> Any:
    details = _client_details(data, mac)
    return details.get(field) if details is not None else None


def _client_timestamp(data: Mapping[str, Any], mac: str, field: str) -> datetime | None:
    seen = _mapping(data.get("client_seen")) or {}
    row = _mapping(seen.get(mac))
    value = row.get(field) if row is not None else None
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=UTC)


def _client_counter(
    data: Mapping[str, Any], mac: str, field: str
) -> CounterSample | None:
    value = _client_value(data, mac, field)
    return CounterSample(f"client:{mac}", value) if isinstance(value, (int, float)) else None


def _client_specs(entry: ConfigEntry, data: Mapping[str, Any]) -> list[_SensorSpec]:
    """Create the expanded card for clients explicitly selected in options."""
    prefix = str(entry.unique_id)
    specs: list[_SensorSpec] = []
    for mac in sorted(selected_client_macs(entry)):
        label = client_display_name(_client_record(data, mac), mac)
        device_id = f"client:{mac}"
        diagnostic = EntityCategory.DIAGNOSTIC
        text_fields = (
            ("connection_type", "Connection type", "client_connection_type"),
            ("ip", "IP", "client_ip"),
            ("port", "Port", "client_port"),
            ("wifi_band", "Wi-Fi band", "client_wifi_band"),
            ("wifi_mode", "Wi-Fi mode", "client_wifi_mode"),
        )
        for field, name, translation_key in text_fields:
            source_field = "interface_id" if field == "port" else field
            specs.append(
                _SensorSpec(
                    f"{prefix}_client_{mac}_{field}",
                    name,
                    lambda values, ident=mac, key=source_field: _client_value(values, ident, key),
                    device_id=device_id,
                    device_name=label,
                    translation_key=translation_key,
                    entity_category=diagnostic,
                )
            )
        specs.extend(
            (
                _SensorSpec(
                    f"{prefix}_client_{mac}_first_seen",
                    "First seen",
                    lambda values, ident=mac: _client_timestamp(values, ident, "first_seen"),
                    device_class=SensorDeviceClass.TIMESTAMP,
                    device_id=device_id,
                    device_name=label,
                    translation_key="client_first_seen",
                    entity_category=diagnostic,
                ),
                _SensorSpec(
                    f"{prefix}_client_{mac}_last_seen",
                    "Last seen",
                    lambda values, ident=mac: _client_timestamp(values, ident, "last_seen"),
                    device_class=SensorDeviceClass.TIMESTAMP,
                    device_id=device_id,
                    device_name=label,
                    translation_key="client_last_seen",
                    entity_category=diagnostic,
                ),
                _SensorSpec(
                    f"{prefix}_client_{mac}_link_speed",
                    "Link speed",
                    lambda values, ident=mac: _client_value(values, ident, "link_speed"),
                    UnitOfDataRate.MEGABITS_PER_SECOND,
                    SensorDeviceClass.DATA_RATE,
                    SensorStateClass.MEASUREMENT,
                    device_id=device_id,
                    device_name=label,
                    translation_key="client_link_speed",
                    entity_category=diagnostic,
                ),
                _SensorSpec(
                    f"{prefix}_client_{mac}_rssi",
                    "RSSI",
                    lambda values, ident=mac: _client_value(values, ident, "rssi"),
                    "dBm",
                    SensorDeviceClass.SIGNAL_STRENGTH,
                    SensorStateClass.MEASUREMENT,
                    device_id=device_id,
                    device_name=label,
                    translation_key="client_rssi",
                    entity_category=diagnostic,
                ),
                _SensorSpec(
                    f"{prefix}_client_{mac}_uptime",
                    "Uptime",
                    lambda values, ident=mac: _client_value(values, ident, "uptime"),
                    UnitOfTime.SECONDS,
                    SensorDeviceClass.DURATION,
                    SensorStateClass.MEASUREMENT,
                    device_id=device_id,
                    device_name=label,
                    translation_key="client_uptime",
                    entity_category=diagnostic,
                    suggested_unit=UnitOfTime.DAYS,
                ),
            )
        )
        for field, direction, name in (
            ("rxbytes", "received", "Received"),
            ("txbytes", "sent", "Sent"),
        ):
            specs.append(
                _SensorSpec(
                    f"{prefix}_client_{mac}_{field}",
                    name,
                    lambda values, ident=mac, key=field: _client_value(values, ident, key),
                    UnitOfInformation.BYTES,
                    SensorDeviceClass.DATA_SIZE,
                    SensorStateClass.TOTAL_INCREASING,
                    device_id=device_id,
                    device_name=label,
                    translation_key=f"client_{direction}_data",
                    entity_category=diagnostic,
                    suggested_unit=UnitOfInformation.MEGABYTES,
                )
            )
        specs.append(
            _SensorSpec(
                f"{prefix}_client_{mac}_tx_rate",
                "Transmit rate",
                lambda _values: None,
                UnitOfDataRate.MEGABITS_PER_SECOND,
                SensorDeviceClass.DATA_RATE,
                SensorStateClass.MEASUREMENT,
                device_id=device_id,
                device_name=label,
                rate_counter=lambda values, ident=mac: _client_counter(values, ident, "txbytes"),
                rate_only=True,
                translation_key="client_tx_rate",
                entity_category=diagnostic,
            )
        )
    return specs


def _interface_specs(entry: ConfigEntry, data: Mapping[str, Any]) -> list[_SensorSpec]:
    """Expose only modem and VPN interface telemetry as child diagnostics."""
    specs: list[_SensorSpec] = []
    prefix = str(entry.unique_id)
    for interface in _interface_model(data).interfaces:
        if interface.role not in {"modem", "vpn"}:
            continue
        interface_id = interface.identifier
        kind = interface.role
        device_id = f"{kind}:{interface_id}"
        translation_prefix = "modem" if kind == "modem" else "vpn"
        if interface.uptime is not None:
            specs.append(
                _SensorSpec(
                    f"{prefix}_{kind}_{interface_id}_uptime",
                    "Uptime",
                    lambda values, ident=interface_id: _interface_uptime(values, ident),
                    UnitOfTime.SECONDS,
                    device_class=SensorDeviceClass.DURATION,
                    state_class=SensorStateClass.MEASUREMENT,
                    translation_key=f"{translation_prefix}_uptime",
                    device_id=device_id,
                    device_name=interface.name,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    suggested_unit=UnitOfTime.DAYS,
                )
            )
        for field, direction, name in (
            ("rxbytes", "received", "Received"),
            ("txbytes", "sent", "Sent"),
        ):
            if getattr(interface, field) is None:
                continue
            getter = lambda values, ident=interface_id, key=field: _interface_counters(
                values, ident
            ).get(key)

            def rate_getter(
                values: Mapping[str, Any],
                ident: str = interface_id,
                key: str = field,
            ) -> CounterSample | None:
                value = _interface_counters(values, ident).get(key)
                return CounterSample(ident, value) if value is not None else None

            specs.append(
                _SensorSpec(
                    f"{prefix}_{kind}_{interface_id}_{field}",
                    f"{name} data",
                    getter,
                    UnitOfInformation.BYTES,
                    SensorDeviceClass.DATA_SIZE,
                    SensorStateClass.TOTAL_INCREASING,
                    translation_key=f"{translation_prefix}_{direction}_data",
                    device_id=device_id,
                    device_name=interface.name,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    suggested_unit=UnitOfInformation.MEGABYTES,
                )
            )
            specs.append(
                _SensorSpec(
                    f"{prefix}_{kind}_{interface_id}_{direction}_rate",
                    f"{name} rate",
                    lambda _values: None,
                    UnitOfDataRate.MEGABITS_PER_SECOND,
                    SensorDeviceClass.DATA_RATE,
                    SensorStateClass.MEASUREMENT,
                    translation_key=f"{translation_prefix}_{direction}_rate",
                    device_id=device_id,
                    device_name=interface.name,
                    rate_counter=rate_getter,
                    rate_only=True,
                    entity_category=EntityCategory.DIAGNOSTIC,
                )
            )
    return specs


def _walk_mappings(value: Any):
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk_mappings(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _walk_mappings(child)


def _router_port_records(data: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Return unique physical GigabitEthernet port records from show/interface."""
    records = data.get("interface_records")
    result: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for record in _walk_mappings(records):
        raw_id = record.get("id")
        raw_type = record.get("type")
        if (
            isinstance(raw_id, bool)
            or not isinstance(raw_id, (str, int))
            or not isinstance(raw_type, str)
        ):
            continue
        port_id = str(raw_id).strip()
        if (
            not port_id.startswith("GigabitEthernet")
            or "/" not in port_id
            or raw_type.strip().casefold() != "port"
            or port_id in seen
        ):
            continue
        seen.add(port_id)
        result.append(record)
    return result


def _router_port_record(data: Mapping[str, Any], port_id: str) -> Mapping[str, Any] | None:
    return next(
        (record for record in _router_port_records(data) if record.get("id") == port_id),
        None,
    )


def _router_port_value(data: Mapping[str, Any], port_id: str) -> str | None:
    record = _router_port_record(data, port_id)
    link = record.get("link") if record is not None else None
    return link if link in {"up", "down"} else None


def _router_port_attributes(data: Mapping[str, Any], port_id: str) -> dict[str, Any]:
    record = _router_port_record(data, port_id)
    if record is None:
        return {}
    attributes: dict[str, Any] = {}
    speed = parse_number(record.get("speed"))
    if speed is not None:
        attributes["speed"] = speed
    for field in ("duplex", "appearance"):
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            attributes[field] = value.strip()
    return attributes


def _router_port_label(record: Mapping[str, Any], port_id: str) -> str:
    for field in ("label", "interface-name"):
        value = record.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return port_id


def _router_port_specs(entry: ConfigEntry, data: Mapping[str, Any]) -> list[_SensorSpec]:
    """Create root diagnostics for physical GigabitEthernet ports."""
    prefix = str(entry.unique_id)
    specs: list[_SensorSpec] = []
    for record in _router_port_records(data):
        port_id = str(record["id"]).strip()
        if _router_port_value(data, port_id) is None:
            continue
        port_label = _router_port_label(record, port_id)
        specs.append(
            _SensorSpec(
                f"{prefix}_port_{port_id}",
                f"Port {port_label}",
                lambda values, ident=port_id: _router_port_value(values, ident),
                attributes=lambda values, ident=port_id: _router_port_attributes(values, ident),
                translation_key="port_status",
                translation_placeholders={"port": port_label},
                entity_category=EntityCategory.DIAGNOSTIC,
            )
        )
    return specs


def _modem_specs(entry: ConfigEntry, data: Mapping[str, Any]) -> list[_SensorSpec]:
    modems = _mapping(data.get("modems")) or {}
    specs: list[_SensorSpec] = []
    prefix = str(entry.unique_id)
    for interface, modem_value in modems.items():
        modem = _mapping(modem_value)
        if modem is None:
            continue
        interface_id = str(interface)
        interface_info = _mapping(modem.get("interface_info")) or {}
        label = _interface_label(interface_info, interface_id)
        modem_device_id = f"modem:{interface_id}"
        modem_fields = parse_modem_interface_fields(interface_info)
        for field, suffix, translation_key in (
            ("operator", "operator", "modem_operator"),
            ("mobile", "technology", "modem_technology"),
            ("signal-level", "signal_level", "modem_signal_level"),
            ("band", "band", "modem_band"),
            ("connection-state", "connection_state", "modem_connection_state"),
            ("apn", "apn", "modem_apn"),
        ):
            if field not in modem_fields:
                continue
            specs.append(
                _SensorSpec(
                    f"{prefix}_modem_{interface_id}_{suffix}",
                    suffix.replace("_", " ").title(),
                    lambda values, ident=interface_id, key=field: parse_modem_interface_fields(
                        _modem_interface_info(values, ident) or {}
                    ).get(key),
                    state_class=(
                        SensorStateClass.MEASUREMENT if field == "signal-level" else None
                    ),
                    device_id=modem_device_id,
                    device_name=label,
                    translation_key=translation_key,
                    entity_category=EntityCategory.DIAGNOSTIC,
                )
            )
        signal_keys = {
            "rssi": "modem_rssi",
            "rsrp": "modem_rsrp",
            "rsrq": "modem_rsrq",
            "sinr": "modem_sinr",
        }
        for field, name, unit, device_class in (
            ("rssi", "RSSI", "dBm", SensorDeviceClass.SIGNAL_STRENGTH),
            ("rsrp", "RSRP", "dBm", SensorDeviceClass.SIGNAL_STRENGTH),
            ("rsrq", "RSRQ", "dB", SensorDeviceClass.SIGNAL_STRENGTH),
            ("sinr", "SINR", "dB", SensorDeviceClass.SIGNAL_STRENGTH),
        ):
            def signal_value(
                values: Mapping[str, Any],
                ident: str = interface_id,
                source: str = field,
            ):
                return _modem_signal_reading(values, ident, source)[0]

            def signal_attributes(
                values: Mapping[str, Any],
                ident: str = interface_id,
                source: str = field,
            ) -> dict[str, Any]:
                reading, antenna = _modem_signal_reading(values, ident, source)
                if antenna is None:
                    return {}
                attrs: dict[str, Any] = {}
                channel = antenna.get("channel")
                if channel is not None:
                    attrs["antenna_channel"] = channel
                if source == "sinr" and reading is not None:
                    attrs["source"] = "SINR" if parse_number(antenna.get("sinr")) is not None else "CINR"
                return attrs

            specs.append(
                _SensorSpec(
                    f"{prefix}_modem_{interface_id}_{field}",
                    name,
                    signal_value,
                    unit,
                    device_class,
                    SensorStateClass.MEASUREMENT,
                    signal_attributes if field == "sinr" else None,
                    modem_device_id,
                    label,
                    translation_key=signal_keys[field],
                    entity_category=EntityCategory.DIAGNOSTIC,
                )
            )

        temperature = parse_number(interface_info.get("temperature"))
        if temperature is not None:
            specs.append(
                _SensorSpec(
                    f"{prefix}_modem_{interface_id}_temperature",
                    "Temperature",
                    lambda values, ident=interface_id: parse_number(
                        (_modem_interface_info(values, ident) or {}).get("temperature")
                    ),
                    UnitOfTemperature.CELSIUS,
                    SensorDeviceClass.TEMPERATURE,
                    SensorStateClass.MEASUREMENT,
                    device_id=modem_device_id,
                    device_name=label,
                    translation_key="modem_temperature",
                    entity_category=EntityCategory.DIAGNOSTIC,
                )
            )

        quota = parse_lte_data_usage(modem.get("traffic_counter"))
        if quota is not None:
            for field, suffix, name, translation_key, unit, device_class, state_class, category in (
                ("used_gb", "data_used", "Data used", "modem_data_used", "GB", SensorDeviceClass.DATA_SIZE, SensorStateClass.TOTAL_INCREASING, None),
                ("remaining_gb", "data_remaining", "Data remaining", "modem_data_remaining", "GB", SensorDeviceClass.DATA_SIZE, SensorStateClass.MEASUREMENT, None),
                ("limit_gb", "data_limit", "Data limit", "modem_data_limit", "GB", SensorDeviceClass.DATA_SIZE, SensorStateClass.MEASUREMENT, EntityCategory.DIAGNOSTIC),
                ("threshold_gb", "data_threshold", "Data threshold", "modem_data_threshold", "GB", SensorDeviceClass.DATA_SIZE, SensorStateClass.MEASUREMENT, EntityCategory.DIAGNOSTIC),
                ("days_left", "days_until_reset", "Days until reset", "modem_days_until_reset", "d", None, SensorStateClass.MEASUREMENT, None),
                ("usage_percent", "quota_usage", "Quota usage", "modem_quota_usage", "%", None, SensorStateClass.MEASUREMENT, None),
            ):
                if field not in quota:
                    continue

                def quota_value(
                    values: Mapping[str, Any],
                    ident: str = interface_id,
                    key: str = field,
                ) -> Any:
                    current_modems = _mapping(values.get("modems")) or {}
                    current_modem = _mapping(current_modems.get(ident)) or {}
                    parsed = parse_lte_data_usage(current_modem.get("traffic_counter"))
                    return parsed.get(key) if parsed is not None else None

                specs.append(
                    _SensorSpec(
                        f"{prefix}_modem_{interface_id}_{suffix}",
                        name,
                        quota_value,
                        unit,
                        device_class,
                        state_class,
                        device_id=modem_device_id,
                        device_name=label,
                        translation_key=translation_key,
                        entity_category=category,
                    )
                )

        sms_available = modem.get("sms_available") is True

        def unread_count(values: Mapping[str, Any], ident: str = interface_id) -> int | None:
            current_modems = _mapping(values.get("modems")) or {}
            current_modem = _mapping(current_modems.get(ident))
            if current_modem is None or current_modem.get("sms_available") is not True:
                return None
            normalized = parse_sms_list_response(current_modem.get("sms"))
            if normalized is None:
                return None
            if any(message["read"] is None for message in normalized):
                return None
            return sum(message["read"] is False for message in normalized)

        def latest_sms_attributes(values: Mapping[str, Any], ident: str = interface_id) -> dict[str, Any]:
            current_modems = _mapping(values.get("modems")) or {}
            current_modem = _mapping(current_modems.get(ident))
            if current_modem is None or current_modem.get("sms_available") is not True:
                return {}
            messages = parse_sms_list_response(current_modem.get("sms"))
            if messages is None:
                return {}
            complete = [message for message in messages if message["complete"] is True]
            if not complete:
                return {}
            message = complete[-1]
            return {
                "sms_id": message["id"],
                "from": message["from"],
                "timestamp": message["timestamp"],
                "message": message["text"],
            }

        if sms_available:
            specs.append(
                _SensorSpec(
                    f"{prefix}_modem_{interface_id}_unread_sms",
                    "Unread SMS",
                    unread_count,
                    state_class=SensorStateClass.MEASUREMENT,
                    attributes=latest_sms_attributes,
                    device_id=modem_device_id,
                    device_name=label,
                    translation_key="unread_sms",
                )
            )
    return specs


def _mesh_specs(entry: ConfigEntry, data: Mapping[str, Any]) -> list[_SensorSpec]:
    mesh = data.get("mesh")
    if mesh is None:
        return []
    specs: list[_SensorSpec] = []
    prefix = str(entry.unique_id)
    seen: set[str] = set()
    for record in _walk_mappings(mesh):
        member_id = mesh_member_id(record)
        if member_id is None:
            continue
        if member_id in seen:
            continue
        seen.add(member_id)
        label = _mesh_device_name(data, record)
        device_id = f"mesh:{member_id}"
        firmware = parse_mesh_member(record).get("fw")
        if isinstance(firmware, str) and firmware.strip():
            specs.append(
                _SensorSpec(
                    f"{prefix}_mesh_{member_id}_firmware_version",
                    "Firmware version",
                    lambda values, ident=member_id: _mesh_metric(values, ident, "fw"),
                    attributes=lambda values, ident=member_id: _mesh_attributes(values, ident),
                    translation_key="mesh_firmware_version",
                    device_id=device_id,
                    device_name=label,
                    entity_category=EntityCategory.DIAGNOSTIC,
                )
            )
        for index, port in enumerate(_mesh_port_records(record)):
            if port.get("link") not in {"up", "down"}:
                continue
            port_label = port.get("label") or port.get("interface-name") or f"{index + 1}"
            specs.append(
                _SensorSpec(
                    f"{prefix}_mesh_{member_id}_port_{index}",
                    f"Port {port_label}",
                    lambda values, ident=member_id, port_index=index: _mesh_port_value(
                        values, ident, port_index
                    ),
                    attributes=lambda values, ident=member_id, port_index=index: _mesh_port_attributes(
                        values, ident, port_index
                    ),
                    translation_key="port_status",
                    translation_placeholders={"port": str(port_label)},
                    device_id=device_id,
                    device_name=label,
                    entity_category=EntityCategory.DIAGNOSTIC,
                )
            )
        translation_keys = {
            "cpuload": "cpu_load",
            "memory_percent": "memory_used",
            "uptime": "mesh_uptime",
            "associations": "mesh_associations",
            "backhaul_speed": "mesh_backhaul_speed",
            "backhaul_uplink": "mesh_backhaul_uplink",
            "backhaul_ap": "mesh_backhaul_ap",
            "backhaul_rssi": "mesh_backhaul_rssi",
            "backhaul_txrate": "mesh_backhaul_txrate",
        }
        for field, suffix, name, unit in (
            ("cpuload", "cpu_load", "CPU load", "%"),
            ("memory_percent", "memory_used", "Memory used", "%"),
            ("uptime", "uptime", "Uptime", "s"),
            ("associations", "associations", "Associated clients", None),
            ("backhaul_speed", "backhaul_speed", "Backhaul link speed", UnitOfDataRate.MEGABITS_PER_SECOND),
            ("backhaul_uplink", "backhaul_uplink", "Backhaul uplink", None),
            ("backhaul_ap", "backhaul_ap", "Backhaul access point", None),
            ("backhaul_rssi", "backhaul_rssi", "Backhaul RSSI", "dBm"),
            ("backhaul_txrate", "backhaul_txrate", "Backhaul transmit rate", UnitOfDataRate.MEGABITS_PER_SECOND),
        ):
            if field == "backhaul_speed" and parse_mesh_member(record).get(field) is None:
                continue
            specs.append(
                _SensorSpec(
                    f"{prefix}_mesh_{member_id}_{suffix}",
                    name,
                    lambda values, ident=member_id, key=field: _mesh_metric(values, ident, key),
                    unit,
                    SensorDeviceClass.DURATION if field == "uptime" else (
                        SensorDeviceClass.SIGNAL_STRENGTH if field == "backhaul_rssi" else (
                            SensorDeviceClass.DATA_RATE
                            if field in {"backhaul_speed", "backhaul_txrate"}
                            else None
                        )
                    ),
                    SensorStateClass.MEASUREMENT if field not in {"backhaul_uplink", "backhaul_ap"} else None,
                    attributes=lambda values, ident=member_id: _mesh_attributes(values, ident),
                    translation_key=translation_keys[field],
                    device_id=device_id,
                    device_name=label,
                    entity_category=EntityCategory.DIAGNOSTIC,
                    suggested_unit=UnitOfTime.DAYS if field == "uptime" else None,
                )
            )
    return specs


def _mesh_port_records(record: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    ports = record.get("port")
    if isinstance(ports, list):
        return [port for port in ports if isinstance(port, Mapping)]
    if isinstance(ports, Mapping):
        return [port for port in ports.values() if isinstance(port, Mapping)]
    return []


def _mesh_port_value(data: Mapping[str, Any], member_id: str, index: int) -> str | None:
    record = _mesh_member_record(data, member_id)
    ports = _mesh_port_records(record) if record is not None else []
    if index >= len(ports):
        return None
    link = ports[index].get("link")
    return link if link in {"up", "down"} else None


def _mesh_port_attributes(data: Mapping[str, Any], member_id: str, index: int) -> dict[str, Any]:
    record = _mesh_member_record(data, member_id)
    ports = _mesh_port_records(record) if record is not None else []
    if index >= len(ports):
        return {}
    port = ports[index]
    attributes: dict[str, Any] = {}
    speed = parse_number(port.get("speed"))
    if speed is not None:
        attributes["speed"] = speed
    for field in ("duplex", "appearance"):
        value = port.get(field)
        if isinstance(value, str) and value.strip():
            attributes[field] = value.strip()
    return attributes


def _mesh_device_name(data: Mapping[str, Any], record: Mapping[str, Any]) -> str:
    """Use Keenetic's friendly mesh/host name, keeping MACs for identity only."""
    raw_mac = record.get("mac")
    mac = normalize_mac(raw_mac) if isinstance(raw_mac, str) else None
    for field in ("known-host", "name", "description"):
        value = record.get(field)
        if (
            isinstance(value, str)
            and value.strip()
            and (mac is None or normalize_mac(value) != mac)
        ):
            return value.strip()

    host = _client_record(data, mac) if mac is not None else None
    if host is not None:
        for field in ("name", "hostname"):
            host_name = host.get(field)
            if (
                isinstance(host_name, str)
                and host_name.strip()
                and normalize_mac(host_name) != mac
            ):
                return host_name.strip()

    model = record.get("model")
    if isinstance(model, str) and model.strip():
        return model.strip()
    return "Mesh node"


def _mesh_member_record(data: Mapping[str, Any], member_id: str) -> Mapping[str, Any] | None:
    mesh = data.get("mesh")
    for record in _walk_mappings(mesh):
        if mesh_member_id(record) != member_id:
            continue
        return record
    return None


def _mesh_metric(data: Mapping[str, Any], member_id: str, field: str) -> int | float | str | None:
    record = _mesh_member_record(data, member_id)
    if record is None:
        return None
    return parse_mesh_member(record).get(field)


def _mesh_attributes(data: Mapping[str, Any], member_id: str) -> dict[str, str]:
    record = _mesh_member_record(data, member_id)
    if record is None:
        return {}
    details = parse_mesh_member(record)
    return {key: details[key] for key in ("mac", "ip") if isinstance(details.get(key), str)}


def _wireguard_peer_value(
    data: Mapping[str, Any], interface_id: str, peer_id: str, field: str
) -> Any:
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
    return peer.get(field) if peer is not None else None


def _wireguard_peer_attributes(
    data: Mapping[str, Any], interface_id: str, peer_id: str
) -> dict[str, Any]:
    inventory = _wireguard_peer_inventory(data, interface_id)
    if inventory is None:
        return {"interface": interface_id}
    peers = inventory.get("peers")
    peer = next(
        (item for item in peers if isinstance(item, Mapping) and item.get("id") == peer_id),
        None,
    ) if isinstance(peers, list) else None
    attributes: dict[str, Any] = {"interface": interface_id}
    if peer is not None and peer.get("remote_port") is not None:
        attributes["remote_port"] = peer["remote_port"]
    return attributes


def _wireguard_peer_inventory(data: Mapping[str, Any], interface_id: str) -> Mapping[str, Any] | None:
    inventories = _mapping(data.get("wireguard_peer_inventories"))
    if inventories is not None:
        return _mapping(inventories.get(interface_id))
    server = _mapping(data.get("wireguard_server"))
    return server if server is not None and server.get("interface_id") == interface_id else None


def _wireguard_peer_specs(
    entry: ConfigEntry, data: Mapping[str, Any]
) -> list[_SensorSpec]:
    inventories = _mapping(data.get("wireguard_peer_inventories"))
    if inventories is None:
        server = _mapping(data.get("wireguard_server"))
        inventories = (
            {server.get("interface_id"): server}
            if server is not None and isinstance(server.get("interface_id"), str)
            else {}
        )
    prefix = str(entry.unique_id)
    specs: list[_SensorSpec] = []
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
        for peer in peers:
            if not isinstance(peer, Mapping) or not isinstance(peer.get("id"), str):
                continue
            peer_id = peer["id"]
            peer_name = str(peer.get("name") or peer_id)
            for field, suffix, translation_key, name in (
                ("rxbytes", "received_bytes", "wireguard_peer_received_data", "Received data"),
                ("txbytes", "sent_bytes", "wireguard_peer_sent_data", "Sent data"),
                ("remote_endpoint", "remote_endpoint", "wireguard_peer_remote_endpoint", "Remote endpoint"),
                ("last_handshake", "handshake", "wireguard_peer_handshake", "Last handshake age"),
            ):
                is_counter = field in {"rxbytes", "txbytes"}
                is_handshake = field == "last_handshake"
                specs.append(
                    _SensorSpec(
                        f"{prefix}_wireguard_{interface_id}_{peer_id}_{suffix}",
                        name,
                        lambda values, if_id=interface_id, key=peer_id, source=field: _wireguard_peer_value(
                            values, if_id, key, source
                        ),
                        UnitOfInformation.BYTES if is_counter else "s" if is_handshake else None,
                        SensorDeviceClass.DATA_SIZE if is_counter else SensorDeviceClass.DURATION if is_handshake else None,
                        SensorStateClass.TOTAL_INCREASING if is_counter else SensorStateClass.MEASUREMENT if is_handshake else None,
                        attributes=lambda values, if_id=interface_id, key=peer_id: _wireguard_peer_attributes(
                            values, if_id, key
                        ),
                        translation_key=translation_key,
                        translation_placeholders={"peer": peer_name},
                        device_id=f"vpn:{interface_id}",
                        device_name=interface_name,
                        entity_category=EntityCategory.DIAGNOSTIC,
                        suggested_unit=(
                            UnitOfInformation.MEGABYTES if is_counter else None
                        ),
                        suggested_precision=0 if is_handshake else None,
                    )
                )
    return specs


class KeeneticSensor(CoordinatorEntity[KeeneticCoordinator], SensorEntity):
    """A sensor read from one coordinator snapshot."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: KeeneticCoordinator,
        entry: ConfigEntry,
        spec: _SensorSpec,
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
        self._last_rate_sample: CounterSample | None = None
        self._last_rate_time: float | None = None
        self._rate_value: float | None = None
        if spec.rate_counter is not None and coordinator.data:
            self._last_rate_sample = spec.rate_counter(coordinator.data)
            self._last_rate_time = time.monotonic() if self._last_rate_sample is not None else None

    @property
    def device_info(self) -> DeviceInfo | ChildDeviceInfo:
        if self._spec.device_id is not None:
            device_name = self._spec.device_name
            if self._spec.device_id.startswith("vpn:"):
                return ChildDeviceInfo(
                    identifiers={(DOMAIN, _device_identifier(self._entry, self._spec.device_id))},
                    name=device_name,
                    parent_device_id=router_device_id(self.hass, self._entry),
                )
            if self._spec.device_id.startswith("mesh:"):
                member_id = self._spec.device_id.removeprefix("mesh:")
                member = _mesh_member_record(self.coordinator.data or {}, member_id)
                details = parse_mesh_member(member) if member is not None else {}
                model = details.get("model")
                firmware = details.get("fw")
                return DeviceInfo(
                    identifiers={(DOMAIN, _device_identifier(self._entry, self._spec.device_id))},
                    via_device_id=router_device_id(self.hass, self._entry),
                    name=device_name,
                    model=model if isinstance(model, str) else None,
                    sw_version=firmware if isinstance(firmware, str) else None,
                )
            return DeviceInfo(
                identifiers={(DOMAIN, _device_identifier(self._entry, self._spec.device_id))},
                via_device_id=router_device_id(self.hass, self._entry),
                name=device_name,
            )
        version = _mapping((self.coordinator.data or {}).get("version")) or {}
        return DeviceInfo(
            identifiers={(DOMAIN, str(self._entry.unique_id))},
            name=self._entry.title,
            manufacturer=version.get("manufacturer") if isinstance(version.get("manufacturer"), str) else "Keenetic",
            model=version.get("model") if isinstance(version.get("model"), str) else None,
            hw_version=version.get("hw_version") if isinstance(version.get("hw_version"), str) else None,
            sw_version=version.get("release") if isinstance(version.get("release"), str) else None,
            configuration_url=f"{self._entry.data[CONF_ADDRESS]}:{self._entry.data[CONF_PORT]}",
        )

    @property
    def native_value(self):
        if not self.coordinator.data:
            return None
        if self._spec.rate_only:
            return self._rate_value
        return self._spec.value(self.coordinator.data)

    @property
    def available(self) -> bool:
        if not super().available or not self.coordinator.data:
            return False
        if self._spec.rate_only:
            return self._rate_value is not None
        return self._spec.value(self.coordinator.data) is not None

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self._spec.unit

    @property
    def suggested_unit_of_measurement(self) -> str | None:
        return self._spec.suggested_unit

    @property
    def suggested_display_precision(self) -> int | None:
        return self._spec.suggested_precision

    @property
    def icon(self) -> str | None:
        if self._spec.translation_key == "port_status":
            return {
                "up": "mdi:ethernet",
                "down": "mdi:ethernet-off",
            }.get(self.native_value)
        return super().icon

    @property
    def device_class(self) -> SensorDeviceClass | None:
        return self._spec.device_class

    @property
    def state_class(self) -> SensorStateClass | None:
        return self._spec.state_class

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if not self._spec.attributes or not self.coordinator.data:
            return None
        attrs = self._spec.attributes(self.coordinator.data)
        return attrs or None

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._spec.rate_counter is not None:
            if not self.coordinator.last_update_success or not self.coordinator.data:
                self._last_rate_sample = None
                self._last_rate_time = None
                self._rate_value = None
            else:
                sample = self._spec.rate_counter(self.coordinator.data)
                now = time.monotonic()
                if sample is None:
                    self._last_rate_sample = None
                    self._last_rate_time = None
                    self._rate_value = None
                elif (
                    self._last_rate_sample is not None
                    and self._last_rate_time is not None
                    and sample.source == self._last_rate_sample.source
                ):
                    elapsed = now - self._last_rate_time
                    self._rate_value = _rate_from_samples(
                        self._last_rate_sample, sample, elapsed
                    )
                    self._last_rate_sample = sample
                    self._last_rate_time = now
                else:
                    self._last_rate_sample = sample
                    self._last_rate_time = now
                    self._rate_value = None
        super()._handle_coordinator_update()


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up router sensors and add discovered interface/modem/Mesh sensors."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator: KeeneticCoordinator = runtime.coordinator
    known: set[str] = set()

    @callback
    def add_current_specs() -> None:
        data = coordinator.data or {}
        removed_ids = data.get("removed_entity_ids")
        if isinstance(removed_ids, set):
            known.difference_update(removed_ids)
        specs = _root_specs(entry, data)
        specs.extend(_interface_specs(entry, data))
        specs.extend(_diagnostic_specs(entry, data))
        specs.extend(_client_specs(entry, data))
        specs.extend(_modem_specs(entry, data))
        specs.extend(_mesh_specs(entry, data))
        specs.extend(_wireguard_peer_specs(entry, data))
        new_specs = [spec for spec in specs if spec.unique_id not in known]
        if not new_specs:
            return
        known.update(spec.unique_id for spec in new_specs)
        async_add_entities([KeeneticSensor(coordinator, entry, spec) for spec in new_specs])

    add_current_specs()
    entry.async_on_unload(coordinator.async_add_listener(add_current_specs))
