"""Per-client IP policy selectors."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .client_registry import normalize_mac
from .const import CONF_POLICY_CLIENTS, DOMAIN
from .coordinator import KeeneticCoordinator
from .device_helpers import router_device_id
from .model import client_is_live, normalize_records, parse_hotspot_client


def _policy_rows(value: Any) -> list[Mapping[str, Any]] | None:
    """Normalize documented policy rows or a mapping keyed by policy name."""
    if isinstance(value, Mapping):
        for key in ("policy", "policies"):
            if key in value:
                return _policy_rows(value[key])
        if "id" in value or "name" in value:
            return [value]

        rows: list[Mapping[str, Any]] = []
        for policy_id, details in value.items():
            if not isinstance(policy_id, (str, int)) or isinstance(policy_id, bool):
                return None
            if isinstance(details, Mapping):
                row = dict(details)
                row.setdefault("id", policy_id)
                rows.append(row)
            elif isinstance(details, str):
                rows.append({"id": policy_id, "description": details})
            else:
                return None
        return rows

    records = normalize_records(value)
    return records


def _policy_options(data: Mapping[str, Any] | None) -> dict[str, str]:
    """Return display option to actual Keenetic policy ID mappings."""
    if data is None:
        return {}
    rows = _policy_rows(data.get("policies"))
    if rows is None:
        return {}

    options: dict[str, str] = {}
    seen_ids: set[str] = set()
    for row in rows:
        raw_id = row.get("id", row.get("name"))
        if isinstance(raw_id, bool) or not isinstance(raw_id, (str, int)):
            continue
        policy_id = str(raw_id).strip()
        if not policy_id or policy_id in seen_ids:
            continue
        seen_ids.add(policy_id)
        raw_description = row.get("description")
        description = (
            raw_description.strip()
            if isinstance(raw_description, str) and raw_description.strip()
            else policy_id
        )
        label = f"{description} ({policy_id})" if description != policy_id else policy_id
        options[label] = policy_id
    return options


def _hotspot_records(data: Mapping[str, Any] | None) -> list[Mapping[str, Any]] | None:
    if data is None:
        return None
    response = data.get("hotspot")
    for key in ("host", "hosts", "client"):
        records = normalize_records(response, key)
        if records is not None:
            return records
    records = normalize_records(response)
    if records is None or any("mac" not in record for record in records):
        return None
    return records


def _client_record(data: Mapping[str, Any] | None, mac: str) -> Mapping[str, Any] | None:
    records = _hotspot_records(data)
    if records is None:
        return None
    for record in records:
        value = record.get("mac")
        normalized = normalize_mac(value) if isinstance(value, str) else None
        if normalized == mac and client_is_live(parse_hotspot_client(record)):
            return record
    return None


def _policy_id(record: Mapping[str, Any] | None) -> str | None:
    if record is None:
        return None
    value = record.get("policy")
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    result = str(value).strip()
    return result or None


def _no_internet_label(hass: HomeAssistant) -> str:
    """Return the built-in deny option in the Home Assistant language."""
    config = getattr(hass, "config", None)
    language = getattr(config, "language", "en")
    return "Без интернета" if isinstance(language, str) and language.casefold().startswith("ru") else "No internet"


def _has_internet_access_denied(record: Mapping[str, Any] | None) -> bool:
    return isinstance(record, Mapping) and isinstance(record.get("access"), str) and record["access"].strip().casefold() == "deny"


def _selected_clients(
    entry: ConfigEntry, _hass: HomeAssistant, _data: Mapping[str, Any] | None
) -> set[str]:
    return {
        mac
        for value in entry.options.get(CONF_POLICY_CLIENTS, [])
        if isinstance(value, str) and (mac := normalize_mac(value)) is not None
    }


def _registered_policy_select_mac(unique_id: str, entry: ConfigEntry) -> str | None:
    prefix = f"{entry.unique_id}_client_"
    suffix = "_policy"
    if not unique_id.startswith(prefix) or not unique_id.endswith(suffix):
        return None
    return normalize_mac(unique_id[len(prefix) : -len(suffix)])


def _cleanup_policy_selects(
    hass: HomeAssistant, entry: ConfigEntry, selected_macs: set[str]
) -> None:
    """Remove only this entry's deselected policy select entities."""
    registry = er.async_get(hass)
    for entity in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity.platform != DOMAIN or entity.domain != "select":
            continue
        mac = _registered_policy_select_mac(entity.unique_id, entry)
        if mac is not None and mac not in selected_macs:
            registry.async_remove(entity.entity_id)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add selectors for configured or discovered client MACs."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    coordinator: KeeneticCoordinator = runtime.coordinator
    selected = _selected_clients(entry, hass, coordinator.data)
    _cleanup_policy_selects(hass, entry, selected)
    entities: dict[str, KeeneticClientPolicySelect] = {}

    def add_mac(mac: str) -> None:
        if mac in entities:
            return
        entity = KeeneticClientPolicySelect(coordinator, entry, mac)
        entities[mac] = entity
        async_add_entities([entity])

    for mac in sorted(selected):
        add_mac(mac)

class KeeneticClientPolicySelect(CoordinatorEntity[KeeneticCoordinator], SelectEntity):
    """Choose an IP policy for a configured client device."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "client_policy"

    def __init__(self, coordinator: KeeneticCoordinator, entry: ConfigEntry, mac: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._mac = mac
        self._attr_unique_id = f"{entry.unique_id}_client_{mac}_policy"
        record = _client_record(coordinator.data, mac)
        label = next(
            (
                value.strip()
                for field in ("name", "hostname")
                if record is not None
                and isinstance((value := record.get(field)), str)
                and value.strip()
            ),
            mac,
        )
        self._label = label

    @property
    def options(self) -> list[str]:
        return [_no_internet_label(self.hass), *_policy_options(self.coordinator.data)]

    @property
    def current_option(self) -> str | None:
        record = _client_record(self.coordinator.data, self._mac)
        if _has_internet_access_denied(record):
            return _no_internet_label(self.hass)
        policy_id = _policy_id(record)
        if policy_id is None:
            return None
        return next(
            (
                label
                for label, option_id in _policy_options(self.coordinator.data).items()
                if option_id == policy_id
            ),
            None,
        )

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.unique_id}:client:{self._mac}")},
            name=self._label,
            via_device_id=router_device_id(self.hass, self._entry),
        )

    async def async_select_option(self, option: str) -> None:
        """Set one listed policy for a host currently returned by show/ip/hotspot."""
        record = _client_record(self.coordinator.data, self._mac)
        if record is None:
            raise HomeAssistantError("The client is not currently registered by the router")

        if option == _no_internet_label(self.hass):
            payload = {"mac": self._mac, "access": "deny"}
        else:
            policy_id = _policy_options(self.coordinator.data).get(option)
            if policy_id is None:
                raise HomeAssistantError("The selected IP policy is no longer available")
            payload = {"mac": self._mac, "access": "permit", "policy": policy_id}

        await self.coordinator.async_call(
            "ip/hotspot/host",
            payload,
        )
        await self.coordinator.async_request_refresh()
        updated_record = _client_record(self.coordinator.data, self._mac)
        if not self.coordinator.last_update_success or updated_record is None:
            raise HomeAssistantError("The router did not confirm the client policy request")
        verification_field = "access" if option == _no_internet_label(self.hass) else "policy"
        if verification_field in updated_record and self.current_option != option:
            raise HomeAssistantError("The router did not confirm the client policy request")
        self.async_write_ha_state()
