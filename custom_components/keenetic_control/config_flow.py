"""UI configuration and options flows."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol
from aiohttp import ClientSession

from homeassistant.config_entries import ConfigFlow, OptionsFlowWithReload
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import CannotConnect, InvalidAuth, KeeneticApi, KeeneticError, UnsupportedResponse
from .const import (
    CONF_ADDRESS,
    CONF_ALLOW_MUTATING_RCI,
    CONF_CLIENTS,
    CONF_PORT,
    CONF_POLICY_CLIENTS,
    CONF_SCAN_INTERVAL,
    CONF_SMS_TARGET,
    CONF_USERNAME,
    CONF_WIFI_ENABLED_ACCESS_POINTS,
    DEFAULT_ADDRESS,
    DEFAULT_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MIN_SCAN_INTERVAL,
)
from .client_registry import normalize_mac
from .entity_model import build_interface_entity_model

_LOGGER = logging.getLogger(__name__)


class _WifiInventoryUnavailable(Exception):
    """The options flow has no current access-point state to apply safely."""


_ERRORS = {
    CannotConnect: "cannot_connect",
    InvalidAuth: "invalid_auth",
    UnsupportedResponse: "unsupported_response",
}


def _normalized_address(value: str, port: int) -> tuple[str, str]:
    """Validate address and return its URL base and scheme."""
    port = _normalized_port(port)
    address = value.strip()
    if "://" not in address:
        address = f"http://{address}"
    parsed = urlsplit(address)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Enter an HTTP or HTTPS router address")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("The address must not contain credentials or query parameters")
    if parsed.path not in ("", "/"):
        raise ValueError("Enter the router address without a path")
    if parsed.port is not None and parsed.port != port:
        raise ValueError("Use the separate port field for the router port")
    host = parsed.hostname or ""
    netloc = f"[{host}]" if ":" in host else host
    return f"{parsed.scheme}://{netloc}", parsed.scheme


def _normalized_port(value: Any) -> int:
    """Convert an integral number-selector value to a URL-safe integer port."""
    if isinstance(value, bool):
        raise ValueError("The port must be an integer between 1 and 65535")
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        raise ValueError("The port must be an integer between 1 and 65535") from None
    if not numeric.is_integer() or not 1 <= numeric <= 65535:
        raise ValueError("The port must be an integer between 1 and 65535")
    return int(numeric)


def _stable_router_id(version: Mapping[str, Any], interface_response: Any) -> str | None:
    """Use a documented device MAC as identity; an address is never an ID."""
    direct = version.get("mac")
    if isinstance(direct, str) and _is_mac(direct):
        return direct.lower().replace("-", ":")
    for item in _walk(interface_response):
        if not isinstance(item, Mapping):
            continue
        if item.get("interface-name") == "Home" or item.get("id") == "Bridge0":
            value = item.get("mac")
            if isinstance(value, str) and _is_mac(value):
                return value.lower().replace("-", ":")
    return None


def _is_mac(value: str) -> bool:
    parts = value.replace("-", ":").split(":")
    return len(parts) == 6 and all(len(part) == 2 and all(c in "0123456789abcdefABCDEF" for c in part) for part in parts)


def _walk(value: Any):
    if isinstance(value, Mapping):
        yield value
        for child in value.values():
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk(child)


def _wifi_access_points(data: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """Return configured Wi-Fi access points from the latest interface inventory."""
    raw_interfaces = data.get("interface_records", data.get("interfaces"))
    model = build_interface_entity_model(raw_interfaces, data.get("routes"))
    if not model.inventory_complete:
        return None
    return [
        {"id": item.identifier, "label": item.name, "enabled": item.enabled}
        for item in model.access_points
    ]


def _normalized_mac_values(values: Any) -> list[str]:
    if not isinstance(values, list):
        raise ValueError("Client selection must be a list")
    normalized: set[str] = set()
    for value in values:
        mac = normalize_mac(value) if isinstance(value, str) else None
        if mac is None:
            raise ValueError("Enter client MAC addresses as aa:bb:cc:dd:ee:ff")
        normalized.add(mac)
    return sorted(normalized)


async def _async_validate(
    session: ClientSession, user_input: Mapping[str, Any]
) -> tuple[str, Mapping[str, Any]]:
    port = _normalized_port(user_input[CONF_PORT])
    base, _scheme = _normalized_address(user_input[CONF_ADDRESS], port)
    api = KeeneticApi(
        session,
        base,
        port,
        user_input.get(CONF_USERNAME, ""),
        user_input.get(CONF_PASSWORD, ""),
    )
    try:
        version = await api.async_connect()
    except KeeneticError as err:
        _LOGGER.warning(
            "Keenetic setup validation failed during show/version (%s): %s",
            type(err).__name__,
            err,
        )
        raise
    try:
        interfaces = await api.async_get("show/interface", params={"name": "Home"})
    except KeeneticError as err:
        _LOGGER.warning(
            "Keenetic setup validation failed during show/interface (%s): %s",
            type(err).__name__,
            err,
        )
        raise
    unique_id = _stable_router_id(version, interfaces)
    if unique_id is None:
        err = UnsupportedResponse("The response has no stable router MAC address")
        _LOGGER.warning("Keenetic setup validation failed during router identity lookup: %s", err)
        raise err
    return unique_id, version


class KeeneticConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add one local Keenetic router per stable hardware identity."""

    VERSION = 2

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                unique_id, version = await _async_validate(
                    async_get_clientsession(self.hass, verify_ssl=False),
                    user_input,
                )
            except (CannotConnect, InvalidAuth, UnsupportedResponse) as err:
                errors["base"] = _ERRORS[type(err)]
            except ValueError:
                errors["base"] = "invalid_address"
            else:
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=str(version.get("model") or "Keenetic"),
                    data={
                        **user_input,
                        CONF_ADDRESS: _normalized_address(user_input[CONF_ADDRESS], user_input[CONF_PORT])[0],
                        CONF_PORT: _normalized_port(user_input[CONF_PORT]),
                        CONF_USERNAME: user_input.get(CONF_USERNAME, ""),
                        CONF_PASSWORD: user_input.get(CONF_PASSWORD, ""),
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS, default=DEFAULT_ADDRESS): TextSelector(),
                vol.Required(CONF_PORT, default=DEFAULT_PORT): NumberSelector(
                    NumberSelectorConfig(min=1, max=65535, step=1, mode=NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_USERNAME, default=""): TextSelector(),
                vol.Optional(CONF_PASSWORD, default=""): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reauth(self, user_input: dict[str, Any] | None = None):
        """Ask for valid credentials when the router rejects authentication."""
        entry = self._get_reauth_entry()
        await self.async_set_unique_id(entry.unique_id)
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                unique_id, _version = await _async_validate(
                    async_get_clientsession(self.hass, verify_ssl=False),
                    user_input,
                )
            except (CannotConnect, InvalidAuth, UnsupportedResponse) as err:
                errors["base"] = _ERRORS[type(err)]
            except ValueError:
                errors["base"] = "invalid_address"
            else:
                if unique_id != entry.unique_id:
                    errors["base"] = "wrong_router"
                else:
                    normalized = {
                        **user_input,
                        CONF_ADDRESS: _normalized_address(user_input[CONF_ADDRESS], user_input[CONF_PORT])[0],
                        CONF_PORT: _normalized_port(user_input[CONF_PORT]),
                        CONF_USERNAME: user_input.get(CONF_USERNAME, ""),
                        CONF_PASSWORD: user_input.get(CONF_PASSWORD, ""),
                    }
                    self.hass.config_entries.async_update_entry(entry, data=normalized)
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason="reauth_successful")

        schema = vol.Schema(
            {
                vol.Required(CONF_ADDRESS, default=entry.data[CONF_ADDRESS]): TextSelector(),
                vol.Required(CONF_PORT, default=entry.data[CONF_PORT]): NumberSelector(
                    NumberSelectorConfig(min=1, max=65535, step=1, mode=NumberSelectorMode.BOX)
                ),
                vol.Optional(CONF_USERNAME, default=entry.data.get(CONF_USERNAME, "")): TextSelector(),
                vol.Optional(CONF_PASSWORD, default=""): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(step_id="reauth", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return KeeneticOptionsFlow()


class KeeneticOptionsFlow(OptionsFlowWithReload):
    """Configure client entities and optional features for this router."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        wifi_rows = self._current_wifi_access_points()
        live_wifi_rows = wifi_rows
        if user_input is not None:
            try:
                user_input[CONF_CLIENTS] = _normalized_mac_values(
                    user_input.get(CONF_CLIENTS, [])
                )
                user_input[CONF_POLICY_CLIENTS] = _normalized_mac_values(
                    user_input.get(CONF_POLICY_CLIENTS, [])
                )
            except ValueError:
                errors["base"] = "invalid_mac"
            else:
                desired_wifi = {
                    value
                    for value in user_input.get(CONF_WIFI_ENABLED_ACCESS_POINTS, [])
                    if isinstance(value, str)
                }
                try:
                    await self._async_apply_wifi_access_points(desired_wifi, wifi_rows)
                except (HomeAssistantError, KeeneticError, _WifiInventoryUnavailable) as err:
                    _LOGGER.warning("Could not update Keenetic Wi-Fi access points (%s)", type(err).__name__)
                    errors["base"] = "wifi_update_failed"
                else:
                    user_input[CONF_WIFI_ENABLED_ACCESS_POINTS] = sorted(desired_wifi)
                    return self.async_create_entry(title="", data=user_input)

        old = self.config_entry.options
        client_rows = self._known_clients()
        client_options = [
            SelectOptionDict(
                value=mac,
                label=name if name.casefold() == mac.casefold() else f"{name} ({mac})",
            )
            for mac, name in client_rows
        ]
        interval = NumberSelector(
            NumberSelectorConfig(
                min=MIN_SCAN_INTERVAL,
                max=2147483647,
                step=1,
                mode=NumberSelectorMode.BOX,
                unit_of_measurement="s",
            )
        )
        clients = SelectSelector(
            SelectSelectorConfig(
                options=client_options,
                multiple=True,
                custom_value=True,
                mode=SelectSelectorMode.DROPDOWN,
            )
        )
        if wifi_rows is None:
            old_wifi = old.get(CONF_WIFI_ENABLED_ACCESS_POINTS, [])
            wifi_rows = [
                {"id": value, "label": value, "enabled": True}
                for value in old_wifi
                if isinstance(value, str)
            ]
        label_counts: dict[str, int] = {}
        for row in wifi_rows:
            label_counts[row["label"]] = label_counts.get(row["label"], 0) + 1
        wifi_options = [
            SelectOptionDict(
                value=row["id"],
                label=(
                    f"{row['label']} ({row['id']})"
                    if label_counts[row["label"]] > 1
                    else row["label"]
                ),
            )
            for row in wifi_rows
        ]
        wifi_enabled = (
            [row["id"] for row in live_wifi_rows if row["enabled"]]
            if live_wifi_rows is not None
            else old.get(CONF_WIFI_ENABLED_ACCESS_POINTS, [])
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=old.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)): interval,
                vol.Required(CONF_CLIENTS, default=old.get(CONF_CLIENTS, [])): clients,
                vol.Required(CONF_POLICY_CLIENTS, default=old.get(CONF_POLICY_CLIENTS, [])): clients,
                vol.Required(
                    CONF_ALLOW_MUTATING_RCI,
                    default=old.get(CONF_ALLOW_MUTATING_RCI, False),
                ): BooleanSelector(),
                vol.Required(CONF_WIFI_ENABLED_ACCESS_POINTS, default=wifi_enabled): SelectSelector(
                    SelectSelectorConfig(
                        options=wifi_options,
                        multiple=True,
                        mode=SelectSelectorMode.LIST,
                    )
                ),
                vol.Optional(CONF_SMS_TARGET, default=old.get(CONF_SMS_TARGET, "")): TextSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)

    def _current_data(self) -> Mapping[str, Any]:
        runtime = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        coordinator = getattr(runtime, "coordinator", None)
        data = getattr(coordinator, "data", None)
        return data if isinstance(data, Mapping) else {}

    def _current_wifi_access_points(self) -> list[dict[str, Any]] | None:
        data = self._current_data()
        return _wifi_access_points(data) if data else None

    async def _async_apply_wifi_access_points(
        self,
        desired: set[str],
        rows: list[dict[str, Any]] | None,
    ) -> None:
        """Apply explicit access-point selections before saving the options."""
        old = self.config_entry.options.get(CONF_WIFI_ENABLED_ACCESS_POINTS, [])
        if rows is None:
            if desired != {value for value in old if isinstance(value, str)}:
                raise _WifiInventoryUnavailable
            return

        available_ids = {row["id"] for row in rows}
        if not desired <= available_ids:
            raise _WifiInventoryUnavailable
        runtime = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        coordinator = getattr(runtime, "coordinator", None)
        if coordinator is None:
            if any(row["enabled"] != (row["id"] in desired) for row in rows):
                raise _WifiInventoryUnavailable
            return

        for row in rows:
            enabled = row["id"] in desired
            if enabled == row["enabled"]:
                continue
            endpoint = "interface/up" if enabled else "interface/down"
            await coordinator.async_call(endpoint, {"name": row["id"]})

    def _known_clients(self) -> list[tuple[str, str]]:
        """Return known client MACs; keep configured-but-offline choices selectable."""
        runtime = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)
        data = getattr(getattr(runtime, "coordinator", None), "data", None) or {}
        labels: dict[str, str] = {}
        for item in _walk(data.get("hotspot")):
            if not isinstance(item, Mapping):
                continue
            mac = item.get("mac")
            if isinstance(mac, str) and _is_mac(mac):
                labels[mac.lower()] = str(item.get("description") or item.get("name") or mac)
        client_prefix = f"{self.config_entry.unique_id}:client:"
        for device in dr.async_entries_for_config_entry(dr.async_get(self.hass), self.config_entry.entry_id):
            for domain, identifier in device.identifiers:
                if domain != DOMAIN or not identifier.startswith(client_prefix):
                    continue
                mac = identifier[len(client_prefix) :].lower()
                name = getattr(device, "name_by_user", None) or getattr(device, "name", None)
                if isinstance(name, str) and name.strip():
                    labels.setdefault(mac, name.strip())
        for mac in self.config_entry.options.get(CONF_CLIENTS, []) + self.config_entry.options.get(CONF_POLICY_CLIENTS, []):
            labels.setdefault(mac.lower(), mac)
        return sorted(labels.items())
