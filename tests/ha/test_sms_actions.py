"""Exercise modem-targeted actions through the real Home Assistant service registry."""

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.keenetic_control import Runtime, async_setup
from custom_components.keenetic_control.api import CannotConnect, RciCommandError
from custom_components.keenetic_control.const import CONF_SMS_TARGET, DOMAIN

SMS = {
    "messages": {
        "sim-5": {
            "from": "+79990000000",
            "timestamp": "2026-10-30T12:00:00",
            "read": False,
            "parts": 1,
            "total-parts": 1,
            "text": "Message text",
        }
    }
}
ACTIONS = {
    "list_sms": {},
    "send_sms": {"message": "Hello"},
    "mark_sms_read": {"sms_id": "sim-5"},
    "delete_sms": {"sms_id": "sim-5"},
    "send_ussd": {"request": "*100#"},
}


@pytest.fixture
async def routers(hass):
    """Register two routers sharing an interface name to catch incorrect routing."""
    await async_setup(hass, {})
    routers = []
    registry = dr.async_get(hass)
    for name in ("first", "second"):
        entry = MockConfigEntry(
            domain=DOMAIN,
            unique_id=name,
            options={CONF_SMS_TARGET: "+79991111111"},
        )
        entry.add_to_hass(hass)
        router = registry.async_get_or_create(
            config_entry_id=entry.entry_id, identifiers={(DOMAIN, name)}
        )
        modems = [
            registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, f"modem:{name}|{interface}")},
                via_device_id=router.id,
            )
            for interface in ("UsbLte0", "UsbLte1")
        ]
        api = SimpleNamespace(
            async_get=AsyncMock(return_value=deepcopy(SMS)),
            async_post=AsyncMock(return_value={}),
        )
        coordinator = SimpleNamespace(
            data={
                "modems": {
                    interface: {"sms_available": True, "sms": deepcopy(SMS)}
                    for interface in ("UsbLte0", "UsbLte1")
                }
            },
            async_call=AsyncMock(return_value={"response": "Balance: 100"}),
            async_request_refresh=AsyncMock(),
        )
        runtime = Runtime(hass=hass, entry=entry, api=api, coordinator=coordinator)
        hass.data[DOMAIN][entry.entry_id] = runtime
        routers.append(
            SimpleNamespace(
                entry=entry, router=router, modems=modems, runtime=runtime, api=api
            )
        )
    return routers


async def call(hass, router, action, data=None, *, modem=0):
    return await hass.services.async_call(
        DOMAIN,
        action,
        {**ACTIONS[action], **(data or {})},
        target={"device_id": router.modems[modem].id},
        blocking=True,
        return_response=action in {"list_sms", "send_ussd"},
    )


async def test_list_targets_selected_modem_and_returns_messages_without_writing(
    hass, routers
):
    selected = routers[1]
    result = await call(hass, selected, "list_sms", modem=1)
    assert result["messages"][0]["id"] == "sim-5"
    assert result["messages"][0]["text"] == "Message text"
    assert result["messages"][0]["read"] is False
    selected.api.async_get.assert_awaited_once_with(
        "sms/list", params={"interface": "UsbLte1"}
    )
    selected.api.async_post.assert_not_awaited()
    routers[0].api.async_get.assert_not_awaited()


@pytest.mark.parametrize("action", ACTIONS)
@pytest.mark.parametrize("invalid_target", ["router", "missing", "multiple"])
async def test_rejects_wrong_target_before_router_io(
    hass, routers, action, invalid_target
):
    selected = routers[0]
    targets = {
        "router": selected.router.id,
        "missing": "unknown-device",
        "multiple": [modem.id for modem in selected.modems],
    }
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            action,
            ACTIONS[action],
            target={"device_id": targets[invalid_target]},
            blocking=True,
            return_response=action == "list_sms",
        )
    selected.api.async_get.assert_not_awaited()
    selected.api.async_post.assert_not_awaited()
    selected.runtime.coordinator.async_call.assert_not_awaited()


@pytest.mark.parametrize("action", ["list_sms", "mark_sms_read", "delete_sms"])
async def test_rejects_old_router_and_interface_fields(hass, routers, action):
    with pytest.raises(vol.Invalid):
        await call(
            hass,
            routers[0],
            action,
            {"entry_id": routers[0].entry.entry_id, "interface": "UsbLte0"},
        )


@pytest.mark.parametrize("recipient", [None, "", "   ", " +79992222222 "])
async def test_send_uses_one_recipient_or_configured_default(hass, routers, recipient):
    data = {} if recipient is None else {"recipient": recipient}
    await call(hass, routers[0], "send_sms", data)
    routers[0].api.async_post.assert_awaited_once_with(
        "sms/send",
        {
            "interface": "UsbLte0",
            "to": "+79992222222" if recipient and recipient.strip() else "+79991111111",
            "message": "Hello",
        },
    )


@pytest.mark.parametrize("recipient", [["+79991111111"], "+79991111111,+79992222222"])
async def test_send_rejects_multiple_recipients(hass, routers, recipient):
    with pytest.raises((vol.Invalid, ServiceValidationError)):
        await call(hass, routers[0], "send_sms", {"recipient": recipient})
    routers[0].api.async_post.assert_not_awaited()


async def test_send_requires_a_recipient_without_default(hass, routers):
    hass.config_entries.async_update_entry(routers[0].entry, options={})
    with pytest.raises(ServiceValidationError, match="default SMS"):
        await call(hass, routers[0], "send_sms")
    routers[0].api.async_post.assert_not_awaited()


async def test_send_failure_is_not_retried(hass, routers):
    routers[0].api.async_post.side_effect = CannotConnect("Timed out")
    with pytest.raises(HomeAssistantError, match="not confirmed"):
        await call(hass, routers[0], "send_sms")
    assert routers[0].api.async_post.await_count == 1


@pytest.mark.parametrize("action", ["mark_sms_read", "delete_sms"])
async def test_sms_change_uses_exact_id_and_confirms_result(hass, routers, action):
    updated = deepcopy(SMS) if action == "mark_sms_read" else {"messages": {}}
    if action == "mark_sms_read":
        updated["messages"]["sim-5"]["read"] = True
    selected = routers[1]
    selected.api.async_get.side_effect = [deepcopy(SMS), updated]
    await call(hass, selected, action, {"sms_id": " sim-5 "}, modem=1)
    command = "read" if action == "mark_sms_read" else "delete"
    selected.api.async_post.assert_awaited_once_with(
        f"sms/{command}", {"interface": "UsbLte1", "id": "sim-5"}
    )
    selected.runtime.coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.parametrize("action", ["mark_sms_read", "delete_sms"])
@pytest.mark.parametrize("sms_id", ["", "sim-unknown", "*"])
async def test_sms_change_rejects_missing_or_unknown_id(hass, routers, action, sms_id):
    with pytest.raises(ServiceValidationError):
        await call(hass, routers[0], action, {"sms_id": sms_id})
    routers[0].api.async_post.assert_not_awaited()


@pytest.mark.parametrize("action", ["mark_sms_read", "delete_sms"])
async def test_sms_change_without_confirmation_fails(hass, routers, action):
    with pytest.raises(HomeAssistantError, match="did not confirm"):
        await call(hass, routers[0], action)
    assert routers[0].api.async_post.await_count == 1


@pytest.mark.parametrize("action", ["mark_sms_read", "delete_sms"])
async def test_sms_command_error_is_not_retried(hass, routers, action):
    routers[0].api.async_post.side_effect = RciCommandError("Rejected")
    with pytest.raises(HomeAssistantError, match="not confirmed"):
        await call(hass, routers[0], action)
    assert routers[0].api.async_post.await_count == 1


async def test_list_rejects_malformed_data_instead_of_returning_empty(hass, routers):
    routers[0].api.async_get.return_value = {"messages": "invalid"}
    with pytest.raises(HomeAssistantError, match="unsupported SMS list"):
        await call(hass, routers[0], "list_sms")


@pytest.mark.parametrize(
    "response", [{"messages": "invalid"}, CannotConnect("Timed out")]
)
async def test_delete_with_unknown_verification_does_not_report_success(
    hass, routers, response
):
    routers[0].api.async_get.side_effect = [deepcopy(SMS), response]
    with pytest.raises(HomeAssistantError, match="not confirmed"):
        await call(hass, routers[0], "delete_sms")
    assert routers[0].api.async_post.await_count == 1


async def test_ussd_polls_delayed_reply_without_resending(hass, routers):
    routers[0].runtime.coordinator.async_call.return_value = {"continued": True}
    routers[0].api.async_get.return_value = {"response": "Balance: 100"}
    assert await call(hass, routers[0], "send_ussd") == {"response": "Balance: 100"}
    assert routers[0].runtime.coordinator.async_call.await_count == 1
    routers[0].api.async_get.assert_awaited_once_with(
        "ussd/send", timeout=pytest.approx(59, abs=1)
    )


async def test_ussd_returns_response_and_rejects_parallel_request_on_same_router(
    hass, routers
):
    started = asyncio.Event()
    finish = asyncio.Event()

    async def send(*args, **kwargs):
        started.set()
        await finish.wait()
        return {"response": "Balance: 100"}

    selected = routers[0]
    selected.runtime.coordinator.async_call.side_effect = send
    first = asyncio.create_task(call(hass, selected, "send_ussd"))
    await started.wait()
    try:
        with pytest.raises(ServiceValidationError, match="already running"):
            await call(hass, selected, "send_ussd", modem=1)
        assert await call(hass, routers[1], "send_ussd") == {"response": "Balance: 100"}
    finally:
        finish.set()
        assert await first == {"response": "Balance: 100"}
    assert selected.runtime.coordinator.async_call.await_count == 1
    assert not selected.runtime.ussd_lock.locked()


@pytest.mark.parametrize("error", [asyncio.TimeoutError(), CannotConnect("Timed out")])
async def test_ussd_releases_lock_after_error(hass, routers, error):
    with (
        patch(
            "custom_components.keenetic_control.async_complete_ussd", side_effect=error
        ),
        pytest.raises(HomeAssistantError),
    ):
        await call(hass, routers[0], "send_ussd")
    assert not routers[0].runtime.ussd_lock.locked()
    assert await call(hass, routers[0], "send_ussd") == {"response": "Balance: 100"}
