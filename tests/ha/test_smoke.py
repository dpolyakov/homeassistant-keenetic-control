"""Exercise one config entry with real Home Assistant modules and fixture RCI data."""

import json
from pathlib import Path

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


async def test_config_entry_setup_refresh_platforms_and_unload(hass, aioclient_mock):
    responses = fixture("snapshot.json")
    responses.update(
        {
            "show/internet/status": fixture("internet.json"),
            "show/usb": fixture("usb.json"),
            "show/media": fixture("media.json"),
            "show/ip/hotspot": fixture("hotspot.json"),
        }
    )
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
        version=2,
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
    assert (
        hass.data[DOMAIN][entry.entry_id].coordinator.data["version"]["release"]
        == "4.3.5"
    )
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

    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.entry_id not in hass.data[DOMAIN]
