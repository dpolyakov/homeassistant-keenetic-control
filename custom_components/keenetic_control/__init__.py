"""Home Assistant entry point for Keenetic Control."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping
from typing import Any, cast

import voluptuous as vol
from aiohttp import CookieJar

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.service import async_get_config_entry

from .api import KeeneticApi, KeeneticError
from .const import (
    CONF_ADDRESS,
    CONF_ALLOW_MUTATING_RCI,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SCAN_INTERVAL,
    CONF_SMS_TARGET,
    CONF_USERNAME,
    DOMAIN,
    PLATFORMS,
    USSD_TIMEOUT,
)
from .client_registry import known_client_macs
from .coordinator import KeeneticCoordinator
from .model import has_sms_service, parse_sms_list_response
from .registry_cleanup import (
    cleanup_retired_registry,
    migrate_duplicate_client_entity_ids,
    migrated_options_v2,
)
from .ussd import async_complete_ussd
from .runtime import KeeneticConfigEntry, Runtime

_SERVICE_REQUEST_API = "request_api"
_SERVICE_USSD = "send_ussd"
_SERVICE_SEND_SMS = "send_sms"
_SERVICE_LIST_SMS = "list_sms"
_SERVICE_MARK_SMS_READ = "mark_sms_read"
_SERVICE_DELETE_SMS = "delete_sms"

PLATFORM_LIST = [Platform(platform) for platform in PLATFORMS]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


def _device_ids(value: Any) -> Any:
    """Validate a Home Assistant device target as one ID or a list of IDs."""
    if isinstance(value, str) and value:
        return value
    if isinstance(value, (list, tuple)) and value and all(
        isinstance(item, str) and item for item in value
    ):
        return value
    raise vol.Invalid("Expected a device ID or a list of device IDs")


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Register integration actions once for the Home Assistant instance."""
    async def async_request_api(call: ServiceCall) -> dict[str, Any] | None:
        entry, runtime = _entry_and_runtime_for_call(hass, call)
        endpoint = call.data["endpoint"]
        method = call.data["method"].upper()
        payload = call.data.get("data_json")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as err:
                raise ServiceValidationError("data_json must contain valid JSON") from err
        if method == "GET" and payload is not None:
            raise ServiceValidationError("GET requests cannot include data_json")
        if method == "POST" and not entry.options.get(CONF_ALLOW_MUTATING_RCI, False):
            raise ServiceValidationError(
                "Enable arbitrary mutating RCI requests in the integration options first"
            )
        try:
            if method == "GET":
                result = await runtime.api.async_get(endpoint)
            else:
                result = await runtime.coordinator.async_call(endpoint, payload)
        except KeeneticError as err:
            raise HomeAssistantError(str(err)) from err
        except ValueError as err:
            raise HomeAssistantError(str(err)) from err
        if call.return_response:
            return {"response": _json_safe(result)}
        return None

    async def async_send_ussd(call: ServiceCall) -> dict[str, str] | None:
        device_id = _single_device_id(call.data["device_id"])
        runtime, interface = _runtime_for_modem_device(hass, device_id)
        code = call.data["request"].strip()
        if not code or "/" in code:
            raise ServiceValidationError("Enter one USSD request")
        # RCI resource paths mirror the documented CLI command tree:
        # `ussd <interface> send <request>`.
        endpoint = "ussd/send"

        async def send(remaining: float) -> Any:
            return await runtime.coordinator.async_call(
                endpoint, {"interface": interface, "request": code}, timeout=remaining
            )

        async def poll(remaining: float) -> Any:
            return await runtime.api.async_get(endpoint, timeout=remaining)

        if runtime.ussd_lock.locked():
            raise ServiceValidationError(
                "A USSD request is already running on this router",
                translation_domain=DOMAIN, translation_key="ussd_busy",
            )
        try:
            async with runtime.ussd_lock:
                result = await async_complete_ussd(
                    send,
                    poll,
                    timeout=USSD_TIMEOUT,
                )
        except asyncio.TimeoutError as err:
            raise HomeAssistantError("The USSD request did not complete before the 60-second timeout") from err
        except KeeneticError as err:
            raise HomeAssistantError(str(err)) from err
        result = _json_safe(result)
        response = result.get("response") if isinstance(result, Mapping) else None
        if not isinstance(response, str) or not response.strip():
            raise HomeAssistantError("The router returned no USSD response")
        if call.return_response:
            return {"response": response}
        return None

    async def async_send_sms(call: ServiceCall) -> None:
        device_id = _single_device_id(call.data["device_id"])
        runtime, interface = _runtime_for_modem_device(hass, device_id)
        _sms_interface(runtime, interface)
        recipient = call.data.get("recipient")
        if recipient is None or not recipient.strip():
            recipient = runtime.entry.options.get(CONF_SMS_TARGET)
        if not isinstance(recipient, str) or not recipient.strip():
            raise ServiceValidationError(
                "Set a recipient or configure the default SMS phone number",
                translation_domain=DOMAIN, translation_key="sms_recipient_missing",
            )
        recipient = recipient.strip()
        if not re.fullmatch(r"\+?[0-9]+", recipient):
            raise ServiceValidationError(
                "Enter one phone number using digits and an optional leading +",
                translation_domain=DOMAIN, translation_key="sms_recipient_invalid",
            )
        await runtime._send_sms(interface, call.data["message"], recipient)

    async def async_list_sms(call: ServiceCall) -> dict[str, Any]:
        runtime, interface = _runtime_for_sms_call(hass, call)
        return {"messages": await _async_sms_messages(runtime, interface)}

    async def async_update_sms(call: ServiceCall, command: str) -> None:
        runtime, interface = _runtime_for_sms_call(hass, call)
        sms_id = call.data["sms_id"].strip()
        if not sms_id or any(ord(char) < 32 for char in sms_id):
            raise ServiceValidationError(
                "Enter one SMS ID returned by list_sms",
                translation_domain=DOMAIN, translation_key="sms_id_required",
            )
        messages = await _async_sms_messages(runtime, interface)
        if not any(message["id"] == sms_id for message in messages):
            raise ServiceValidationError(
                "The SMS ID does not exist on the selected modem",
                translation_domain=DOMAIN, translation_key="sms_not_found",
            )
        try:
            await runtime.api.async_post(f"sms/{command}", {"interface": interface, "id": sms_id})
            messages = await _async_sms_messages(runtime, interface)
        except (KeeneticError, HomeAssistantError) as err:
            raise HomeAssistantError(
                "The SMS change result is not confirmed; do not automatically retry",
                translation_domain=DOMAIN, translation_key="sms_change_unconfirmed",
            ) from err
        message = next((message for message in messages if message["id"] == sms_id), None)
        if (command == "delete" and message is not None) or (
            command == "read" and (message is None or message["read"] is not True)
        ):
            raise HomeAssistantError(
                "The router did not confirm the SMS change",
                translation_domain=DOMAIN, translation_key="sms_change_unconfirmed",
            )
        await runtime.coordinator.async_request_refresh()

    async def async_mark_sms_read(call: ServiceCall) -> None:
        await async_update_sms(call, "read")

    async def async_delete_sms(call: ServiceCall) -> None:
        await async_update_sms(call, "delete")

    hass.services.async_register(
        DOMAIN,
        _SERVICE_REQUEST_API,
        async_request_api,
        schema=vol.Schema(
            {
                vol.Exclusive("entry_id", "router"): cv.string,
                vol.Exclusive("device_id", "router"): _device_ids,
                vol.Required("method", default="GET"): vol.In(("GET", "POST")),
                vol.Required("endpoint"): cv.string,
                vol.Optional("data_json"): vol.Any(cv.string, dict, list, int, float, bool),
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    sms_target_schema = {
        vol.Required("device_id"): _device_ids,
    }
    hass.services.async_register(
        DOMAIN,
        _SERVICE_LIST_SMS,
        async_list_sms,
        schema=vol.Schema(sms_target_schema),
        supports_response=SupportsResponse.ONLY,
    )
    for name, handler in (
        (_SERVICE_MARK_SMS_READ, async_mark_sms_read),
        (_SERVICE_DELETE_SMS, async_delete_sms),
    ):
        hass.services.async_register(
            DOMAIN,
            name,
            handler,
            schema=vol.Schema({**sms_target_schema, vol.Required("sms_id"): cv.string}),
        )
    hass.services.async_register(
        DOMAIN,
        _SERVICE_USSD,
        async_send_ussd,
        schema=vol.Schema(
            {
                vol.Required("device_id"): _device_ids,
                vol.Required("request"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        _SERVICE_SEND_SMS,
        async_send_sms,
        schema=vol.Schema(
            {
                vol.Required("device_id"): _device_ids,
                vol.Required("message"): cv.string,
                vol.Optional("recipient"): cv.string,
            }
        ),
    )
    return True


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Preserve client selections and retire the configurable polling interval."""
    if entry.version > 3:
        return False
    if entry.version == 3:
        return True

    options = dict(entry.options)
    if entry.version == 1:
        known_clients = known_client_macs(hass, entry)
        options = migrated_options_v2(options, known_clients)
        cleanup_retired_registry(hass, entry)
    options.pop(CONF_SCAN_INTERVAL, None)
    hass.config_entries.async_update_entry(entry, options=options, version=3)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: KeeneticConfigEntry) -> bool:
    """Set up one router and its platform entities."""
    session = async_create_clientsession(hass, verify_ssl=False, cookie_jar=CookieJar(unsafe=True))
    api = KeeneticApi(
        session,
        entry.data[CONF_ADDRESS],
        entry.data[CONF_PORT],
        entry.data.get(CONF_USERNAME, ""),
        entry.data.get(CONF_PASSWORD, ""),
    )
    coordinator = KeeneticCoordinator(hass, entry, api)
    await coordinator.async_load_client_seen()
    await coordinator.async_config_entry_first_refresh()
    migrate_duplicate_client_entity_ids(hass, entry)
    version = coordinator.data.get("version", {}) if coordinator.data else {}
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, str(entry.unique_id))},
        name=entry.title,
        manufacturer=version.get("manufacturer", "Keenetic"),
        model=version.get("model"),
        hw_version=version.get("hw_version"),
        sw_version=version.get("release"),
        configuration_url=f"{entry.data[CONF_ADDRESS]}:{entry.data[CONF_PORT]}",
    )
    entry.runtime_data = Runtime(entry=entry, api=api, coordinator=coordinator)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORM_LIST)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload platforms and release per-router objects."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORM_LIST)


def _runtime_for_sms_call(hass: HomeAssistant, call: ServiceCall) -> tuple[Runtime, str]:
    runtime, interface = _runtime_for_modem_device(hass, _single_device_id(call.data["device_id"]))
    return runtime, _sms_interface(runtime, interface)


async def _async_sms_messages(runtime: Runtime, interface: str) -> list[dict[str, Any]]:
    try:
        response = await runtime.api.async_get("sms/list", params={"interface": interface})
    except KeeneticError as err:
        raise HomeAssistantError(str(err)) from err
    messages = parse_sms_list_response(response)
    if messages is None:
        raise HomeAssistantError("The router returned an unsupported SMS list")
    return messages


def _entry_and_runtime_for_call(hass: HomeAssistant, call: ServiceCall) -> tuple[ConfigEntry, Runtime]:
    entry_id = call.data.get("entry_id")
    device_id = call.data.get("device_id")
    if device_id is not None:
        device_id = _single_device_id(device_id)
    if (entry_id is None) == (device_id is None):
        raise ServiceValidationError("Select exactly one router by entry_id or device_id")
    if entry_id is None:
        device = dr.async_get(hass).async_get(device_id)
        if device is None:
            raise ServiceValidationError("The selected Home Assistant device does not exist")
        router_entries = [
            candidate
            for candidate in hass.config_entries.async_entries(DOMAIN)
            if (DOMAIN, str(candidate.unique_id)) in device.identifiers
        ]
        if len(router_entries) != 1:
            raise ServiceValidationError("Select the Keenetic router device, not one of its child devices")
        entry_id = router_entries[0].entry_id
    entry = cast(KeeneticConfigEntry, async_get_config_entry(hass, DOMAIN, entry_id))
    runtime = entry.runtime_data
    return entry, runtime


def _runtime_for_modem_device(hass: HomeAssistant, device_id: str) -> tuple[Runtime, str]:
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        raise ServiceValidationError("The selected modem device does not exist")
    interface = next(
        (
            identifier.split("|", 1)[1]
            for domain, identifier in device.identifiers
            if domain == DOMAIN and identifier.startswith("modem:") and "|" in identifier
        ),
        None,
    )
    if not interface:
        raise ServiceValidationError("Select one Keenetic modem device")
    entry_id = getattr(device, "config_entry_id", None)
    entry = hass.config_entries.async_get_entry(entry_id) if entry_id else None
    if entry is None or (DOMAIN, f"modem:{entry.unique_id}|{interface}") not in device.identifiers:
        raise ServiceValidationError("The selected device is not a modem of this Keenetic router")
    if entry.domain != DOMAIN:
        raise ServiceValidationError("The selected modem does not belong to a Keenetic router")
    entry = cast(KeeneticConfigEntry, async_get_config_entry(hass, DOMAIN, entry.entry_id))
    runtime = entry.runtime_data
    if interface not in runtime.coordinator.data.get("modems", {}):
        raise ServiceValidationError("The selected modem is no longer available")
    return runtime, interface


def _single_device_id(value: Any) -> str:
    """Require one Home Assistant device target."""
    if isinstance(value, str) and value:
        return value
    if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], str) and value[0]:
        return value[0]
    raise ServiceValidationError("Select exactly one Home Assistant device")


def _sms_interface(runtime: Runtime, interface: str) -> str:
    modems = runtime.coordinator.data.get("modems", {})
    if not has_sms_service(modems.get(interface, {})):
        raise ServiceValidationError("The selected interface is not an available SMS modem")
    return interface


def _json_safe(value: Any) -> Any:
    """Reject response values that cannot be returned to an automation."""
    try:
        json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as err:
        raise HomeAssistantError("The router returned a non-JSON response") from err
    if isinstance(value, Mapping):
        return dict(value)
    return value
