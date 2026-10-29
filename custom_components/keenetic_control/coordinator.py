"""Per-router polling and change detection."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CannotConnect, InvalidAuth, KeeneticApi, KeeneticError, RciCommandError, UnsupportedResponse
from .client_registry import selected_client_macs
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    EVENT_SMS,
    EVENT_WAN_CHANGED,
)
from .entity_model import build_interface_entity_model
from .model import (
    client_is_live,
    complete_sms,
    default_route,
    merge_wireguard_peer_sources,
    parse_dns_proxy_diagnostics,
    parse_ipsec_vici_diagnostics,
    parse_hotspot_client,
    normalize_interface_records,
    normalize_records,
    parse_wireguard_interface_peers,
    parse_wireguard_server_response,
    sms_fingerprint,
)
from .registry_cleanup import cleanup_dynamic_registry

_LOGGER = logging.getLogger(__name__)
_OPTIONAL_COOLDOWN = timedelta(minutes=30)


class KeeneticCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Collect one router's state and emit confirmed WAN/SMS changes."""

    def __init__(self, hass: HomeAssistant, entry, api: KeeneticApi) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"Keenetic {entry.title}",
            update_interval=timedelta(seconds=entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
        )
        self.entry = entry
        self.api = api
        self._optional_until: dict[str, Any] = {}
        self._modem_optional_until: dict[str, float] = {}
        self._ipsec_diagnostics_until = 0.0
        self._ipsec_diagnostics_summary: dict[str, Any] | None = None
        self._version_id: str | None = None
        self._previous_default_interface: str | None = None
        self._wan_initialized = False
        self._sms_initialized: set[str] = set()
        self._sms_fingerprints: dict[str, set[str]] = {}
        self._client_seen: dict[str, dict[str, str]] = {}
        self._client_seen_store = None

    async def async_load_client_seen(self) -> None:
        """Load the small per-entry client observation journal."""
        from homeassistant.helpers.storage import Store

        self._client_seen_store = Store(
            self.hass, 1, f"{DOMAIN}.{self.entry.entry_id}.client_seen"
        )
        try:
            stored = await self._client_seen_store.async_load()
        except Exception as err:  # Storage is optional state, never router state.
            _LOGGER.debug("Could not load client observation journal: %s", type(err).__name__)
            return
        rows = stored.get("clients") if isinstance(stored, Mapping) else None
        if not isinstance(rows, Mapping):
            return
        for raw_mac, raw_row in rows.items():
            if not isinstance(raw_mac, str) or not isinstance(raw_row, Mapping):
                continue
            mac = parse_hotspot_client({"mac": raw_mac})["mac"]
            if mac is None:
                continue
            timestamps = {
                key: value
                for key in ("first_seen", "last_seen")
                if isinstance((value := raw_row.get(key)), str) and value.strip()
            }
            if timestamps:
                self._client_seen[mac] = timestamps

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch mandatory system state and isolate optional feature failures."""
        try:
            version = await self.api.async_get("show/version")
            system = await self.api.async_get("show/system")
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed("The router rejected the configured credentials") from err
        except KeeneticError as err:
            raise UpdateFailed(str(err)) from err

        if not isinstance(version, Mapping) or not isinstance(system, Mapping):
            raise UpdateFailed("The router returned an unsupported system response")

        version_id = str(version.get("title") or version.get("release") or "")
        if version_id and version_id != self._version_id:
            self._optional_until.clear()
            self._modem_optional_until.clear()
            self._ipsec_diagnostics_until = 0.0
            self._ipsec_diagnostics_summary = None
            self._version_id = version_id

        data: dict[str, Any] = {
            "observed_at": datetime.now(UTC),
            "version": dict(version),
            "system": dict(system),
            "interfaces": [],
            "internet": None,
            "routes": None,
            "policies": None,
            "hotspot": None,
            "traffic_shape": None,
            "client_seen": dict(self._client_seen),
            "mesh": None,
            "modems": {},
            "sms": {},
        }

        interface_response = await self._optional("show/interface", "interfaces")
        data["interfaces"] = interface_response
        data["internet"] = await self._optional("show/internet/status", "internet")
        route_response = await self._optional("show/ip/route", "routes", {"table": 254})
        route_records = normalize_records(route_response, "route")
        data["routes"] = route_records if route_records is not None else normalize_records(route_response)
        data["policies"] = await self._optional("show/ip/policy", "policies")
        data["hotspot"] = await self._optional("show/ip/hotspot", "hotspot")
        data["traffic_shape"] = await self._optional(
            "ip/traffic-shape/host", "traffic_shape"
        )
        self._update_client_seen(data["hotspot"], data["observed_at"])
        data["client_seen"] = dict(self._client_seen)
        data["mesh"] = await self._optional("show/mws/member", "mesh")
        data["mesh_associations"] = await self._optional("show/mws/associations", "mesh_associations")
        data["usb"] = await self._optional("show/usb", "usb")
        data["media"] = await self._optional("show/media", "media")
        dns_proxy_response = await self._optional("show/dns-proxy", "dns_proxy")
        data["dns_proxy"] = parse_dns_proxy_diagnostics(dns_proxy_response)
        # Log parsing is optional and can be cancelled while HA reloads the entry.
        # Do not make the first refresh wait for it; the next scheduled refresh can
        # populate the diagnostic entities after the entry is fully available.
        data["ipsec_diagnostics"] = (
            await self._poll_ipsec_diagnostics(version) if self.data is not None else None
        )
        system_cpu = await self._optional("show/system/cpustat", "cpu")
        if system_cpu is not None:
            data["cpu"] = system_cpu
        data["wireguard_server"] = None
        data["wireguard_peer_inventories"] = {}
        release = version.get("release")
        major = release.split(".", 1)[0] if isinstance(release, str) else ""
        if major.isdigit() and int(major) >= 5:
            wireguard_response = await self._optional("show/wireguard-server", "wireguard_server")
            data["wireguard_server"] = parse_wireguard_server_response(wireguard_response)

        records = normalize_interface_records(interface_response)
        interface_peers = parse_wireguard_interface_peers(interface_response)
        wireguard_server = data["wireguard_server"]
        if records is not None:
            wireguard_ids = {
                str(record.get("id")).strip()
                for record in records
                if isinstance(record.get("type"), str)
                and record["type"].strip().casefold() == "wireguard"
            }
            if not isinstance(wireguard_server, Mapping) or wireguard_server.get("interface_id") not in wireguard_ids:
                wireguard_server = None
        data["wireguard_peer_inventories"] = merge_wireguard_peer_sources(
            interface_peers, wireguard_server
        ) or {}
        interface_model = build_interface_entity_model(records, data["routes"])
        data["interface_records"] = (
            await self._poll_interface_stats(records, interface_model.statistics_ids)
            if records is not None
            else None
        )
        modem_ids = {
            interface.identifier
            for interface in interface_model.interfaces
            if interface.role == "modem"
        }
        data["modems"] = await self._poll_modems(records or [], modem_ids)
        peer_inventories = data["wireguard_peer_inventories"]
        if peer_inventories:
            removed_entity_ids: set[str] = set()
            for inventory in peer_inventories.values():
                removed_entity_ids.update(
                    cleanup_dynamic_registry(
                        self.hass,
                        self.entry,
                        interface_response,
                        data["mesh"],
                        None,
                        inventory,
                    )
                )
            data["removed_entity_ids"] = removed_entity_ids
        else:
            data["removed_entity_ids"] = cleanup_dynamic_registry(
                self.hass,
                self.entry,
                interface_response,
                data["mesh"],
                None,
                data["wireguard_server"],
            )
        self._process_wan(data)
        return data

    def _update_client_seen(self, hotspot: Any, observed_at: datetime) -> None:
        """Record first/last observations only for valid hotspot host rows."""
        records = normalize_records(hotspot, "host")
        if records is None:
            records = normalize_records(hotspot)
        if records is None:
            return
        timestamp = observed_at.isoformat()
        selected = selected_client_macs(self.entry)
        changed = False
        for record in records:
            client = parse_hotspot_client(record)
            mac = client["mac"]
            if mac is None or mac not in selected or not client_is_live(client):
                continue
            row = self._client_seen.get(mac)
            if row is None:
                row = {"first_seen": timestamp, "last_seen": timestamp}
                self._client_seen[mac] = row
                changed = True
            if "first_seen" not in row:
                row["first_seen"] = timestamp
                changed = True
            if row.get("last_seen") != timestamp:
                row["last_seen"] = timestamp
                changed = True
        if changed and self._client_seen_store is not None:
            self._client_seen_store.async_delay_save(
                lambda: {"clients": self._client_seen}, 5
            )

    async def _poll_ipsec_diagnostics(self, version: Mapping[str, Any]) -> dict[str, Any] | None:
        """Read the reference VICI marker occasionally, retaining no raw log text."""
        ndw = version.get("ndw")
        components = ndw.get("components") if isinstance(ndw, Mapping) else None
        if isinstance(components, str):
            has_ipsec = "ipsec" in {part.strip() for part in components.split(",")}
        elif isinstance(components, list):
            has_ipsec = "ipsec" in components
        else:
            has_ipsec = False
        if not has_ipsec:
            return None

        now = self.hass.loop.time()
        if now < self._ipsec_diagnostics_until:
            return self._ipsec_diagnostics_summary
        try:
            log_response = await self.api.async_post("parse", "show log", allow_text=True)
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed("The router rejected the configured credentials") from err
        except CannotConnect:
            self._ipsec_diagnostics_until = now + 120
            return self._ipsec_diagnostics_summary
        except (RciCommandError, UnsupportedResponse):
            self._ipsec_diagnostics_until = now + _OPTIONAL_COOLDOWN.total_seconds()
            return None
        except KeeneticError:
            self._ipsec_diagnostics_until = now + 120
            return self._ipsec_diagnostics_summary

        self._ipsec_diagnostics_summary = parse_ipsec_vici_diagnostics(log_response)
        self._ipsec_diagnostics_until = now + 300
        return self._ipsec_diagnostics_summary

    async def _optional(
        self, endpoint: str, key: str, params: Mapping[str, Any] | None = None
    ) -> Any:
        """Read an optional endpoint with a quiet compatibility cooldown."""
        now = self.hass.loop.time()
        cooldown_key = f"{endpoint}:{params.get('name')}" if params and params.get("name") else endpoint
        until = self._optional_until.get(cooldown_key, 0)
        if now < until:
            return None
        try:
            return await self.api.async_get(endpoint, params=params)
        except InvalidAuth as err:
            raise ConfigEntryAuthFailed("The router rejected the configured credentials") from err
        except CannotConnect as err:
            _LOGGER.debug("Optional Keenetic resource %s could not be read", key)
            return None
        except (RciCommandError, UnsupportedResponse) as err:
            self._optional_until[cooldown_key] = now + _OPTIONAL_COOLDOWN.total_seconds()
            _LOGGER.debug("Optional Keenetic resource %s is unavailable: %s", key, type(err).__name__)
            return None

    async def _poll_interface_stats(
        self, interfaces: list[dict[str, Any]], statistics_ids: set[str] | frozenset[str]
    ) -> list[dict[str, Any]]:
        """Read byte counters only for interfaces selected by the entity model."""
        result: list[dict[str, Any]] = []
        for item in interfaces:
            updated = dict(item)
            interface_id = self._interface_id(item)
            if interface_id in statistics_ids:
                try:
                    statistics = await self._optional(
                        "show/interface/stat",
                        f"interface_stats_{interface_id}",
                        {"name": interface_id},
                    )
                except ConfigEntryAuthFailed:
                    raise
                if isinstance(statistics, Mapping):
                    updated["statistic"] = statistics.get("statistic", statistics)
            result.append(updated)
        return result

    async def _poll_modems(
        self, interfaces: list[dict[str, Any]], modem_ids: set[str]
    ) -> dict[str, dict[str, Any]]:
        """Poll only modem resources that the documented interface list exposes."""
        results: dict[str, dict[str, Any]] = {}
        for item in interfaces:
            interface_id = self._interface_id(item)
            if not interface_id or interface_id not in modem_ids:
                continue
            modem: dict[str, Any] = {"interface": interface_id, "interface_info": item}
            try:
                antennas = await self._modem_optional(
                    "show/interface/antennas", "GET", {"name": interface_id}
                )
                if antennas is not None:
                    modem["antennas"] = antennas
            except InvalidAuth as err:
                raise ConfigEntryAuthFailed("The router rejected the configured credentials") from err
            except KeeneticError as err:
                modem["antennas_error"] = type(err).__name__
            try:
                cells = await self._modem_optional(
                    "show/interface/cells", "GET", {"name": interface_id}
                )
                if cells is not None:
                    modem["cells"] = cells
            except InvalidAuth as err:
                raise ConfigEntryAuthFailed("The router rejected the configured credentials") from err
            except KeeneticError as err:
                modem["cells_error"] = type(err).__name__
            try:
                traffic_counter = await self._modem_optional(
                    "show/interface/traffic-counter", "GET", {"name": interface_id}
                )
                if traffic_counter is not None:
                    modem["traffic_counter"] = traffic_counter
            except InvalidAuth as err:
                raise ConfigEntryAuthFailed("The router rejected the configured credentials") from err
            except KeeneticError as err:
                modem["traffic_counter_error"] = type(err).__name__
            try:
                sms = await self._modem_optional("sms/list", "POST", {"name": interface_id})
            except InvalidAuth as err:
                raise ConfigEntryAuthFailed("The router rejected the configured credentials") from err
            except KeeneticError as err:
                modem["sms_available"] = False
                modem["sms_error"] = type(err).__name__
            else:
                modem["sms_available"] = sms is not None
                if sms is not None:
                    modem["sms"] = sms
                    self._process_sms(interface_id, sms)
            results[interface_id] = modem
        return results

    async def _modem_optional(self, endpoint: str, method: str, params: Mapping[str, Any]) -> Any:
        now = self.hass.loop.time()
        cooldown_key = f"{endpoint}:{params.get('name')}" if params.get("name") else endpoint
        if now < self._modem_optional_until.get(cooldown_key, 0):
            return None
        try:
            if method == "POST":
                return await self.api.async_post(endpoint, dict(params))
            return await self.api.async_get(endpoint, params=params)
        except (RciCommandError, UnsupportedResponse):
            self._modem_optional_until[cooldown_key] = now + _OPTIONAL_COOLDOWN.total_seconds()
            raise

    @staticmethod
    def _interface_id(item: Mapping[str, Any]) -> str | None:
        value = item.get("id")
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            return None
        identifier = str(value).strip()
        return identifier or None

    def _process_wan(self, data: Mapping[str, Any]) -> None:
        """Emit a change only for a successful, unambiguous route-table result."""
        if data.get("routes") is None:
            return
        status, current = default_route(data["routes"])
        if status == "unknown" or status == "ambiguous":
            return
        if status == "missing":
            current = None
        if not self._wan_initialized:
            self._previous_default_interface = current
            self._wan_initialized = True
            return
        previous = self._previous_default_interface
        if previous == current:
            return
        self._previous_default_interface = current
        self.hass.bus.async_fire(
            EVENT_WAN_CHANGED,
            {
                "entry_id": self.entry.entry_id,
                "previous_interface": previous,
                "interface": current,
            },
        )

    def _process_sms(self, interface: str, response: Any) -> None:
        """Suppress boot-time SMS and publish each new complete message once."""
        messages = normalize_records(response, "messages")
        if messages is None:
            return
        current = {
            fingerprint
            for message in messages
            if complete_sms(message)
            if (fingerprint := sms_fingerprint(message)) is not None
        }
        if interface not in self._sms_initialized:
            self._sms_initialized.add(interface)
            self._sms_fingerprints[interface] = current
            return
        previous = self._sms_fingerprints.setdefault(interface, set())
        for message in messages:
            if not complete_sms(message):
                continue
            fingerprint = sms_fingerprint(message)
            if fingerprint is None:
                continue
            if fingerprint in previous:
                continue
            self.hass.bus.async_fire(
                EVENT_SMS,
                {
                    "entry_id": self.entry.entry_id,
                    "interface": interface,
                    "sms_id": str(message.get("id") or ""),
                    "from": message.get("from"),
                    "timestamp": message.get("timestamp"),
                    "message": message.get("text"),
                },
            )
            previous.add(fingerprint)
        previous.update(current)

    async def async_call(self, endpoint: str, data: Any = None, *, timeout: int | float | None = None) -> Any:
        """Send one command without retries; preserve timeout ambiguity to callers."""
        try:
            return await self.api.async_post(endpoint, data, timeout=timeout)
        except CannotConnect as err:
            if timeout is not None:
                raise HomeAssistantError("The router did not confirm the command before the timeout") from err
            raise

    async def async_delete(
        self, endpoint: str, data: Any = None, *, timeout: int | float | None = None
    ) -> Any:
        """Reset one setting without retrying a submitted request."""
        try:
            return await self.api.async_delete(endpoint, data, timeout=timeout)
        except CannotConnect as err:
            if timeout is not None:
                raise HomeAssistantError("The router did not confirm the reset before the timeout") from err
            raise

