"""Exercise configuration, recovery and options through Home Assistant flows."""

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.keenetic_control import config_flow as flow_module
from custom_components.keenetic_control.api import (
    CannotConnect,
    InvalidAuth,
    UnsupportedResponse,
)
from custom_components.keenetic_control.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")
MAC = "aa:bb:cc:dd:ee:ff"
DATA = {"address": "192.0.2.1", "port": 80, "username": "admin", "password": "secret"}
OPTIONS = {
    "clients": [],
    "policy_clients": [],
    "allow_mutating_rci": False,
    "wifi_enabled_access_points": [],
}
WIFI = {
    "interface_records": {
        "WifiMaster0/AccessPoint0": {
            "id": "WifiMaster0/AccessPoint0",
            "type": "AccessPoint",
            "state": "up",
            "ssid": "Home",
        },
        "WifiMaster0/AccessPoint1": {
            "id": "WifiMaster0/AccessPoint1",
            "type": "AccessPoint",
            "state": "down",
            "ssid": "Home",
        },
    },
    "hotspot": {"host": [{"mac": MAC, "name": "Phone"}, {"mac": "broken"}, {"mac": 5}]},
}


@pytest.fixture
def hass_config_dir(hass_tmp_config_dir):
    (Path(hass_tmp_config_dir) / "custom_components" / DOMAIN).symlink_to(
        Path(__file__).parents[2] / "custom_components" / DOMAIN,
        target_is_directory=True,
    )
    return hass_tmp_config_dir


@pytest.fixture(autouse=True)
def mock_setup():
    with patch(
        "custom_components.keenetic_control.async_setup_entry", return_value=True
    ):
        yield


@pytest.fixture(autouse=True)
def sessions():
    """Track real sessions without replacing their cookie or cleanup behavior."""
    created = []
    factory = flow_module.async_create_clientsession

    def create(*args, **kwargs):
        session = factory(*args, **kwargs)
        created.append(session)
        return session

    with patch.object(flow_module, "async_create_clientsession", side_effect=create):
        yield created
    assert all(session.closed for session in created)
    assert len({id(session.cookie_jar) for session in created}) == len(created)


@pytest.fixture
def api():
    with patch.object(flow_module, "KeeneticApi", autospec=True) as client:
        client.return_value.async_connect.return_value = {
            "mac": MAC,
            "model": "Keenetic Test",
        }
        client.return_value.async_get.return_value = {
            "interface": {"Home": {"id": "Bridge0", "mac": MAC}}
        }
        yield client.return_value


def existing_entry(hass, *, options=None, runtime=True):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id=MAC, version=3, data=DATA, options=options or {}
    )
    entry.add_to_hass(hass)
    if runtime:
        entry.runtime_data = SimpleNamespace(
            coordinator=SimpleNamespace(data=deepcopy(WIFI), async_call=AsyncMock())
        )
    return entry


async def start(hass, source, entry=None):
    context = {"source": source}
    if entry is not None:
        context["entry_id"] = entry.entry_id
    return await hass.config_entries.flow.async_init(DOMAIN, context=context)


async def finish(hass, result, data=DATA):
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], deepcopy(data)
    )
    await hass.async_block_till_done()
    return result


@pytest.mark.parametrize("fallback", [False, True])
async def test_user_flow(hass, api, fallback):
    data = dict(DATA)
    if fallback:
        api.async_connect.return_value = {}
        data.pop("username")
        data.pop("password")
    result = await start(hass, SOURCE_USER)
    assert result["type"] is FlowResultType.FORM
    result = await finish(hass, result, data)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == MAC
    assert result["result"].version == 3
    assert result["title"] == ("Keenetic" if fallback else "Keenetic Test")
    assert result["data"] == {
        **DATA,
        "address": "http://192.0.2.1",
        "username": "" if fallback else "admin",
        "password": "" if fallback else "secret",
    }
    api.async_connect.assert_awaited_once()
    api.async_get.assert_awaited_once_with("show/interface", params={"name": "Home"})


@pytest.mark.parametrize("source", [SOURCE_USER, SOURCE_REAUTH])
@pytest.mark.parametrize(
    "failure,error",
    [
        ("connect", "cannot_connect"),
        ("auth", "invalid_auth"),
        ("unsupported", "unsupported_response"),
        ("interfaces", "cannot_connect"),
        ("identity", "unsupported_response"),
        ("address", "invalid_address"),
    ],
)
async def test_flow_errors_recover_in_same_flow(
    hass, api, sessions, source, failure, error
):
    entry = existing_entry(hass) if source == SOURCE_REAUTH else None
    result = await start(hass, source, entry)
    data = dict(DATA)
    if failure in {"connect", "auth", "unsupported"}:
        api.async_connect.side_effect = {
            "connect": CannotConnect,
            "auth": InvalidAuth,
            "unsupported": UnsupportedResponse,
        }[failure]("test failure")
    elif failure == "interfaces":
        api.async_get.side_effect = CannotConnect("test failure")
    elif failure == "identity":
        api.async_connect.return_value = {}
        api.async_get.return_value = {}
    else:
        data["address"] = "ftp://192.0.2.1"
    result = await finish(hass, result, data)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}
    assert len(sessions) == 1
    assert sessions[0].closed
    api.async_connect.side_effect = api.async_get.side_effect = None
    api.async_connect.return_value = {"mac": MAC}
    with patch.object(hass.config_entries, "async_reload", return_value=True) as reload:
        result = await finish(hass, result)
    assert len(sessions) == 2
    assert all(session.closed for session in sessions)
    if entry is None:
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["result"].unique_id == MAC
    else:
        assert result["type"] is FlowResultType.ABORT
        assert result["reason"] == "reauth_successful"
        assert entry.unique_id == MAC
        assert entry.data["address"] == "http://192.0.2.1"
        reload.assert_awaited_once_with(entry.entry_id)


async def test_duplicate_router(hass, api):
    existing_entry(hass)
    result = await finish(hass, await start(hass, SOURCE_USER))
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_wrong_router_then_correct_router(hass, api):
    entry = existing_entry(hass)
    api.async_connect.return_value = {"mac": "00:11:22:33:44:55"}
    result = await finish(hass, await start(hass, SOURCE_REAUTH, entry))
    assert result["errors"] == {"base": "wrong_router"}
    assert dict(entry.data) == DATA
    api.async_connect.return_value = {"mac": MAC}
    with patch.object(hass.config_entries, "async_reload", return_value=True):
        result = await finish(
            hass,
            result,
            {"address": DATA["address"], "port": 80, "username": "", "password": ""},
        )
    assert result["reason"] == "reauth_successful"
    assert entry.unique_id == MAC
    assert entry.data["username"] == entry.data["password"] == ""


@pytest.mark.parametrize(
    "value", [True, None, "invalid", 0, 65536, 80.5, float("nan"), float("inf")]
)
def test_invalid_port(value):
    with pytest.raises(ValueError):
        flow_module._normalized_port(value)


@pytest.mark.parametrize(
    "value",
    [
        "http://",
        "ftp://router",
        "http://u:p@router",
        "http://router/?q=1",
        "http://router/#a",
        "http://router/rci",
        "http://router:81",
        "http://router:invalid",
    ],
)
def test_invalid_address(value):
    with pytest.raises(ValueError):
        flow_module._normalized_address(value, 80)


@pytest.mark.parametrize(
    "value,port,expected",
    [
        ("router", 80, "http://router"),
        (" https://router:443/ ", 443, "https://router"),
        ("http://[::1]", 80, "http://[::1]"),
    ],
)
def test_normalize_address(value, port, expected):
    assert flow_module._normalized_address(value, port)[0] == expected


@pytest.mark.parametrize(
    "version,interfaces,expected",
    [
        ({"mac": "AA-BB-CC-DD-EE-FF"}, {}, MAC),
        (
            {"mac": "bad"},
            [
                {"id": "Other", "mac": MAC},
                {"interface-name": "Home", "mac": "bad"},
                {"id": "Bridge0", "mac": MAC},
            ],
            MAC,
        ),
        (
            {},
            {
                "interface": [
                    {"interface-name": "Home", "mac": 5},
                    {"id": "Bridge0", "mac": MAC},
                ]
            },
            MAC,
        ),
        ({}, {"id": "Bridge0", "mac": None}, None),
    ],
)
def test_stable_identity_from_supported_responses(version, interfaces, expected):
    assert flow_module._stable_router_id(version, interfaces) == expected


@pytest.mark.parametrize("values", [None, "aa:bb:cc:dd:ee:ff", [5], ["bad"]])
def test_invalid_client_list(values):
    with pytest.raises(ValueError):
        flow_module._normalized_mac_values(values)


async def options_finish(hass, result, data):
    with patch.object(hass.config_entries, "async_reload", return_value=True):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], deepcopy(data)
        )
        await hass.async_block_till_done()
    return result


async def test_options_normalize_clients_and_apply_only_changed_wifi(hass):
    entry = existing_entry(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert "scan_interval" not in result["data_schema"].schema
    data = {
        **OPTIONS,
        "clients": [MAC.upper().replace(":", "-"), MAC],
        "wifi_enabled_access_points": ["WifiMaster0/AccessPoint1"],
    }
    result = await options_finish(hass, result, data)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["clients"] == [MAC]
    assert entry.options["wifi_enabled_access_points"] == ["WifiMaster0/AccessPoint1"]
    assert entry.runtime_data.coordinator.async_call.await_args_list == [
        (("interface/down", {"name": "WifiMaster0/AccessPoint0"}),),
        (("interface/up", {"name": "WifiMaster0/AccessPoint1"}),),
    ]


async def test_options_invalid_mac_recovers(hass):
    entry = existing_entry(hass, runtime=False)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await options_finish(hass, result, {**OPTIONS, "clients": ["invalid"]})
    assert result["errors"] == {"base": "invalid_mac"}
    result = await options_finish(hass, result, OPTIONS)
    assert result["type"] is FlowResultType.CREATE_ENTRY


@pytest.mark.parametrize("failure", ["post", "unknown_ap", "missing_inventory"])
async def test_options_wifi_error_recovers(hass, failure):
    entry = existing_entry(hass)
    coordinator = entry.runtime_data.coordinator
    result = await hass.config_entries.options.async_init(entry.entry_id)
    # The router inventory may change while the user has the form open.
    if failure == "post":
        coordinator.async_call.side_effect = HomeAssistantError("offline")
    elif failure == "missing_inventory":
        coordinator.data = {"interfaces": {"unrecognized": "response"}}
    else:
        del coordinator.data["interface_records"]["WifiMaster0/AccessPoint1"]
    data = {**OPTIONS, "wifi_enabled_access_points": ["WifiMaster0/AccessPoint1"]}
    result = await options_finish(hass, result, data)
    assert result["errors"] == {"base": "wifi_update_failed"}
    assert not entry.options
    coordinator.async_call.side_effect = None
    coordinator.data = deepcopy(WIFI)
    result = await options_finish(hass, result, OPTIONS)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_options_preserve_offline_clients_and_wifi(hass):
    entry = existing_entry(
        hass,
        runtime=False,
        options={
            "clients": [MAC],
            "policy_clients": ["11:22:33:44:55:66"],
            "wifi_enabled_access_points": ["offline-ap", 5],
        },
    )
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{MAC}:client:{MAC}")},
        name="Stored phone",
    )
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{MAC}:client:22:33:44:55:66:77")},
        name=" ",
    )
    registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, MAC), ("other", "id")}
    )
    result = await hass.config_entries.options.async_init(entry.entry_id)
    fields = result["data_schema"].schema
    choices = fields["clients"].config["options"]
    assert {row["value"] for row in choices} == {MAC, "11:22:33:44:55:66"}
    assert any("Stored phone" in row["label"] for row in choices)
    result = await options_finish(
        hass,
        result,
        {**OPTIONS, "clients": [MAC], "wifi_enabled_access_points": ["offline-ap"]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_options_handles_empty_snapshot_and_unique_wifi_labels(hass):
    entry = existing_entry(hass)
    entry.runtime_data.coordinator.data["interface_records"][
        "WifiMaster0/AccessPoint1"
    ]["ssid"] = "Guest"
    result = await hass.config_entries.options.async_init(entry.entry_id)
    fields = result["data_schema"].schema
    assert [
        row["label"] for row in fields["wifi_enabled_access_points"].config["options"]
    ] == ["Home", "Guest"]
    result = await options_finish(
        hass,
        result,
        {**OPTIONS, "wifi_enabled_access_points": ["WifiMaster0/AccessPoint0"]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry.runtime_data.coordinator.data = []
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await options_finish(
        hass,
        result,
        {**OPTIONS, "wifi_enabled_access_points": ["WifiMaster0/AccessPoint0"]},
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_wifi_apply_rejects_changes_if_runtime_disappears(hass):
    entry = existing_entry(hass, runtime=False)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    flow = hass.config_entries.options._progress[result["flow_id"]]
    rows = [{"id": "ap", "enabled": True}]
    await flow._async_apply_wifi_access_points({"ap"}, rows)
    with pytest.raises(flow_module._WifiInventoryUnavailable):
        await flow._async_apply_wifi_access_points(set(), rows)
    await options_finish(hass, result, OPTIONS)
