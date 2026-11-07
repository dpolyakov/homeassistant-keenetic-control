"""Exercise one config entry with real Home Assistant modules and fixture RCI data."""

import json
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "keenetic_control"
ROUTER = "http://localhost:18080/rci/"
FIXTURES = Path(__file__).parents[1] / "fixtures/rci"

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
def hass_config_dir(hass_tmp_config_dir: str) -> str:
    (Path(hass_tmp_config_dir) / "custom_components" / DOMAIN).symlink_to(
        Path(__file__).parents[2] / "custom_components" / DOMAIN,
        target_is_directory=True,
    )
    return hass_tmp_config_dir


def fixture(name: str):
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize("version", [1, 2, 3])
async def test_config_entry_setup_refresh_platforms_and_unload(
    hass, aioclient_mock, version
):
    responses = fixture("snapshot.json")
    responses.update(
        {
            "show/internet/status": fixture("internet.json"),
            "show/usb": fixture("usb.json"),
            "show/media": fixture("media.json"),
            "show/ip/hotspot": fixture("hotspot.json"),
        }
    )
    responses["show/ip/hotspot"] = {
        "host": [
            {
                "mac": "aa:bb:cc:dd:ee:ff",
                "name": "Phone",
                "active": True,
                "ip": "192.0.2.10",
            }
        ]
    }
    for path, response in responses.items():
        url = ROUTER + path
        if path == "show/ip/route":
            url += "?table=254"
        aioclient_mock.get(
            url, json=response, headers={"Content-Type": "application/json"}
        )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Fixture router",
        unique_id="fixture-router",
        version=version,
        options={"clients": ["aa:bb:cc:dd:ee:ff"], "scan_interval": 1}
        if version < 3
        else {"clients": ["aa:bb:cc:dd:ee:ff"]},
        data={
            "address": "http://localhost",
            "port": 18080,
            "username": "",
            "password": "",
        },
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert entry.version == 3
    assert "scan_interval" not in entry.options
    assert entry.options["clients"] == ["aa:bb:cc:dd:ee:ff"]
    assert entry.runtime_data.coordinator.update_interval == timedelta(seconds=30)
    tracker = hass.states.get("device_tracker.phone")
    assert tracker is not None
    assert tracker.attributes["friendly_name"] == "Phone"
    assert not any(
        name.startswith("keenetic_sms_")
        for name in hass.services.async_services().get("notify", {})
    )
    assert entry.runtime_data.coordinator.data["version"]["release"] == "4.3.5"
    assert dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, "fixture-router"), entry.entry_id
    )
    for platform in (
        "binary_sensor",
        "button",
        "device_tracker",
        "number",
        "select",
        "sensor",
        "switch",
    ):
        assert f"{DOMAIN}.{platform}" in hass.config.components

    original_runtime = entry.runtime_data
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.runtime_data is not original_runtime
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert not hasattr(entry, "runtime_data")


@pytest.mark.parametrize(
    "error, state",
    [("connect", ConfigEntryState.SETUP_RETRY), ("auth", ConfigEntryState.SETUP_ERROR)],
)
async def test_first_refresh_failure_does_not_load_platforms(hass, error, state):
    from custom_components.keenetic_control.api import CannotConnect, InvalidAuth

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="offline",
        version=3,
        data={"address": "http://router", "port": 80},
    )
    entry.add_to_hass(hass)
    exception = (
        CannotConnect("offline") if error == "connect" else InvalidAuth("credentials")
    )
    with (
        patch(
            "custom_components.keenetic_control.api.KeeneticApi.async_get",
            side_effect=exception,
        ),
        patch.object(hass.config_entries, "async_forward_entry_setups") as platforms,
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is state
    assert not hasattr(entry, "runtime_data")
    platforms.assert_not_called()
    assert hass.services.has_service(DOMAIN, "send_sms")
