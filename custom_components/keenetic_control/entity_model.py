"""Normalize raw interface inventory into the Home Assistant product model."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .model import (
    Number,
    is_known_vpn_interface_type,
    normalize_interface_records,
    parse_number,
    parse_stat_counters,
    parse_yes_no,
    select_default_route,
)


InterfaceRole = Literal[
    "access_point",
    "internal",
    "modem",
    "port",
    "vpn",
    "other",
]


@dataclass(frozen=True)
class CounterSample:
    """One cumulative counter tied to the interface that produced it."""

    source: str
    value: Number


@dataclass(frozen=True)
class TemperatureReading:
    """One unambiguous router-level temperature reading."""

    source: str
    value: Number


@dataclass(frozen=True)
class WifiAccessPoint:
    """One user-facing Wi-Fi network exposed by the interface inventory."""

    identifier: str
    name: str
    enabled: bool


@dataclass(frozen=True)
class InterfaceState:
    """Normalized fields needed by polling and entity policy."""

    identifier: str
    interface_name: str | None
    name: str
    role: InterfaceRole
    enabled: bool | None
    connected: bool | None
    uptime: Number | None
    address: str | None
    temperature: Number | None
    rxbytes: Number | None
    txbytes: Number | None


@dataclass(frozen=True)
class InterfaceEntityModel:
    """Product-facing interface inventory used by every HA platform."""

    inventory_complete: bool
    interfaces: tuple[InterfaceState, ...]
    primary_wan: InterfaceState | None
    router_temperature: TemperatureReading | None
    access_points: tuple[WifiAccessPoint, ...]
    statistics_ids: frozenset[str]

    def primary_wan_counter(self, field: Literal["rxbytes", "txbytes"]) -> CounterSample | None:
        """Return a counter only for an unambiguous current default route."""
        if self.primary_wan is None:
            return None
        value = getattr(self.primary_wan, field)
        if value is None:
            return None
        return CounterSample(self.primary_wan.identifier, value)


def _identifier(record: dict[str, Any] | Any) -> str | None:
    value = record.get("id")
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    identifier = str(value).strip()
    return identifier or None


def _text(record: Any, field: str) -> str | None:
    value = record.get(field)
    return value.strip() if isinstance(value, str) and value.strip() else None


def _role(record: Any, identifier: str) -> InterfaceRole:
    raw_type = _text(record, "type") or ""
    interface_type = raw_type.casefold()
    normalized_id = identifier.casefold()
    if is_known_vpn_interface_type(raw_type):
        return "vpn"
    if interface_type == "accesspoint":
        return "access_point"
    if interface_type == "port":
        return "port"
    if interface_type in {"usb", "usbmodem", "usbqmi", "usblte", "usbyota", "yotaone", "cdcethernet"} or normalized_id.startswith(
        ("usbqmi", "usbmodem", "usblte", "usbyota", "yotaone", "cdcethernet")
    ):
        return "modem"
    if interface_type in {"wifimaster", "bridge", "loopback"}:
        return "internal"
    return "other"


def _supports_statistics(interface: InterfaceState) -> bool:
    return interface.connected is True and interface.role not in {
        "access_point",
        "internal",
        "port",
    }


def build_interface_entity_model(interfaces: Any, routes: Any) -> InterfaceEntityModel:
    """Build the single interface policy consumed by polling and HA entities."""
    records = normalize_interface_records(interfaces)
    if records is None:
        return InterfaceEntityModel(False, (), None, None, (), frozenset())

    normalized: list[InterfaceState] = []
    access_points: list[WifiAccessPoint] = []
    temperatures: list[TemperatureReading] = []
    for record in records:
        identifier = _identifier(record)
        if identifier is None:
            return InterfaceEntityModel(False, (), None, None, (), frozenset())
        interface_name = _text(record, "interface-name")
        name = _text(record, "description") or interface_name or identifier
        role = _role(record, identifier)
        statistics = record.get("statistic")
        counters = parse_stat_counters(statistics if isinstance(statistics, Mapping) else record)
        interface = InterfaceState(
            identifier=identifier,
            interface_name=interface_name,
            name=name,
            role=role,
            enabled={"up": True, "down": False}.get((_text(record, "state") or "").casefold()),
            connected=parse_yes_no(record.get("connected")),
            uptime=parse_number(record.get("uptime")),
            address=_text(record, "address"),
            temperature=parse_number(record.get("temperature")),
            rxbytes=counters.get("rxbytes"),
            txbytes=counters.get("txbytes"),
        )
        normalized.append(interface)

        if role == "internal" and (_text(record, "type") or "").casefold() == "wifimaster":
            if interface.temperature is not None:
                temperatures.append(TemperatureReading(identifier, interface.temperature))

        state = _text(record, "state")
        if (
            role == "access_point"
            and identifier.startswith("WifiMaster")
            and "/AccessPoint" in identifier
            and state in {"up", "down"}
        ):
            ssid = _text(record, "ssid")
            if ssid is not None or state == "up" or interface_name not in {None, identifier}:
                access_points.append(
                    WifiAccessPoint(identifier, ssid or name, state == "up")
                )

    selection = select_default_route(routes)
    primary_wan: InterfaceState | None = None
    if selection.state == "unique" and selection.interface is not None:
        matches = [
            interface
            for interface in normalized
            if selection.interface in {interface.identifier, interface.interface_name}
        ]
        if len(matches) == 1:
            primary_wan = matches[0]

    statistics_ids = {
        interface.identifier
        for interface in normalized
        if interface.role in {"modem", "vpn"} and _supports_statistics(interface)
    }
    if primary_wan is not None and _supports_statistics(primary_wan):
        statistics_ids.add(primary_wan.identifier)

    return InterfaceEntityModel(
        True,
        tuple(normalized),
        primary_wan,
        temperatures[0] if len(temperatures) == 1 else None,
        tuple(access_points),
        frozenset(statistics_ids),
    )
