"""Focused tests for VPN and modem device controls."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


_PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "keenetic_control"
_PACKAGE_NAME = "keenetic_control_controls_test"


class _CoordinatorEntity:
    def __class_getitem__(cls, _item):
        return cls

    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self):
        return self.coordinator.last_update_success


class _Coordinator:
    def __init__(self, data, reflect_writes=True):
        self.data = data
        self.reflect_writes = reflect_writes
        self.last_update_success = True
        self.calls = []
        self.refreshes = 0

    async def async_call(self, endpoint, payload):
        self.calls.append((endpoint, payload))
        if self.reflect_writes and endpoint in {"interface/up", "interface/down"}:
            for record in self.data.get("interface_records", []):
                if record.get("id") == payload.get("name"):
                    record["state"] = "up" if endpoint.endswith("/up") else "down"

    async def async_delete(self, endpoint, payload):
        self.calls.append((f"DELETE {endpoint}", payload))
        if endpoint == "ip/traffic-shape/host":
            self.data["traffic_shape"] = {"host": []}

    async def async_request_refresh(self):
        self.refreshes += 1


class _Entry:
    entry_id = "entry-1"
    unique_id = "router"
    title = "Main router"
    data = {"address": "http://192.0.2.1", "port": 80}


def _load_modules():
    fake_homeassistant = types.ModuleType("homeassistant")
    fake_components = types.ModuleType("homeassistant.components")
    fake_switch = types.ModuleType("homeassistant.components.switch")
    fake_switch.SwitchEntity = type("SwitchEntity", (), {})
    fake_button = types.ModuleType("homeassistant.components.button")
    fake_button.ButtonEntity = type("ButtonEntity", (), {})
    fake_config_entries = types.ModuleType("homeassistant.config_entries")
    fake_config_entries.ConfigEntry = object
    fake_core = types.ModuleType("homeassistant.core")
    fake_core.HomeAssistant = object
    fake_core.callback = lambda function: function
    fake_exceptions = types.ModuleType("homeassistant.exceptions")
    fake_exceptions.HomeAssistantError = type("HomeAssistantError", (Exception,), {})
    fake_helpers = types.ModuleType("homeassistant.helpers")
    fake_devices = types.ModuleType("homeassistant.helpers.device_registry")
    fake_entity_registry = types.ModuleType("homeassistant.helpers.entity_registry")

    class DeviceInfo(dict):
        def __init__(self, **values):
            super().__init__(values)

    fake_devices.DeviceInfo = DeviceInfo
    fake_devices.ChildDeviceInfo = DeviceInfo
    fake_devices.async_get_device_id_by_identifier = (
        lambda _hass, _identifier, **_kwargs: "router-device"
    )
    fake_devices.async_get = lambda _hass: None
    fake_devices.async_entries_for_config_entry = lambda _registry, _entry_id: []
    fake_entity_registry.async_get = lambda _hass: None
    fake_entity_registry.async_entries_for_config_entry = lambda _registry, _entry_id: []
    fake_helpers.device_registry = fake_devices
    fake_helpers.entity_registry = fake_entity_registry
    fake_entity = types.ModuleType("homeassistant.helpers.entity")
    fake_entity.EntityCategory = types.SimpleNamespace(CONFIG="config")
    fake_entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    fake_entity_platform.AddEntitiesCallback = object
    fake_update = types.ModuleType("homeassistant.helpers.update_coordinator")
    fake_update.CoordinatorEntity = _CoordinatorEntity
    fake_package = types.ModuleType(_PACKAGE_NAME)
    fake_package.__path__ = [str(_PACKAGE_PATH)]
    fake_coordinator = types.ModuleType(f"{_PACKAGE_NAME}.coordinator")
    fake_coordinator.KeeneticCoordinator = object

    fake_components.switch = fake_switch
    fake_components.button = fake_button
    modules = {
        "homeassistant": fake_homeassistant,
        "homeassistant.components": fake_components,
        "homeassistant.components.switch": fake_switch,
        "homeassistant.components.button": fake_button,
        "homeassistant.config_entries": fake_config_entries,
        "homeassistant.core": fake_core,
        "homeassistant.exceptions": fake_exceptions,
        "homeassistant.helpers": fake_helpers,
        "homeassistant.helpers.device_registry": fake_devices,
        "homeassistant.helpers.entity_registry": fake_entity_registry,
        "homeassistant.helpers.entity": fake_entity,
        "homeassistant.helpers.entity_platform": fake_entity_platform,
        "homeassistant.helpers.update_coordinator": fake_update,
        _PACKAGE_NAME: fake_package,
        f"{_PACKAGE_NAME}.coordinator": fake_coordinator,
    }
    module_patch = patch.dict(sys.modules, modules)
    module_patch.start()
    try:
        loaded = {}
        for name in ("const", "model", "entity_model", "device_helpers", "switch", "button"):
            path = _PACKAGE_PATH / f"{name}.py"
            spec = importlib.util.spec_from_file_location(f"{_PACKAGE_NAME}.{name}", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            loaded[name] = module
    finally:
        module_patch.stop()
        for name in list(sys.modules):
            if name == _PACKAGE_NAME or name.startswith(f"{_PACKAGE_NAME}."):
                sys.modules.pop(name, None)
    return loaded["switch"], loaded["button"]


SWITCH, BUTTON = _load_modules()


class VpnSwitchTests(unittest.IsolatedAsyncioTestCase):
    def _entity(self, state="up", reflect_writes=True):
        coordinator = _Coordinator(
            {
                "interface_records": [
                    {
                        "id": "Wireguard0",
                        "type": "Wireguard",
                        "description": "Home VPN",
                        "state": state,
                        "connected": "no",
                    }
                ],
                "routes": [],
            },
            reflect_writes=reflect_writes,
        )
        entity = SWITCH.KeeneticVpnSwitch(coordinator, _Entry(), "Wireguard0", "Home VPN")
        entity.hass = types.SimpleNamespace()
        return entity

    async def test_switch_uses_admin_state_and_posts_interface_commands(self):
        entity = self._entity()

        self.assertTrue(entity.is_on)
        self.assertEqual(entity.device_info["parent_device_id"], "router-device")
        await entity.async_turn_off()

        self.assertFalse(entity.is_on)
        self.assertEqual(
            entity.coordinator.calls,
            [("interface/down", {"name": "Wireguard0"})],
        )
        self.assertEqual(entity.coordinator.refreshes, 1)

    async def test_switch_requires_refreshed_state_confirmation(self):
        entity = self._entity(reflect_writes=False)

        with self.assertRaises(SWITCH.HomeAssistantError):
            await entity.async_turn_off()


class ModemRestartButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_button_posts_one_power_cycle_without_immediate_refresh(self):
        coordinator = _Coordinator({})
        entity = BUTTON.KeeneticModemRestartButton(
            coordinator,
            _Entry(),
            "UsbLte0",
            "Mobile internet",
        )
        entity.hass = types.SimpleNamespace()

        await entity.async_press()

        self.assertEqual(
            coordinator.calls,
            [("interface/usb/power-cycle", {"name": "UsbLte0", "pause": 3000})],
        )
        self.assertEqual(coordinator.refreshes, 0)
        self.assertEqual(entity.device_info["via_device_id"], "router-device")


class ClientButtonTests(unittest.IsolatedAsyncioTestCase):
    async def test_wake_on_lan_uses_selected_mac(self):
        coordinator = _Coordinator({})
        entity = BUTTON.KeeneticClientWakeButton(
            coordinator,
            _Entry(),
            "aa:bb:cc:dd:ee:ff",
            "Phone",
        )

        await entity.async_press()

        self.assertEqual(coordinator.calls, [("ip/hotspot/wake", {"mac": "aa:bb:cc:dd:ee:ff"})])

    async def test_clear_bandwidth_limit_deletes_and_confirms(self):
        coordinator = _Coordinator(
            {
                "traffic_shape": {
                    "host": [{"mac": "aa:bb:cc:dd:ee:ff", "rate": 1000}]
                }
            }
        )
        entity = BUTTON.KeeneticClientClearBandwidthButton(
            coordinator,
            _Entry(),
            "aa:bb:cc:dd:ee:ff",
            "Phone",
        )

        await entity.async_press()

        self.assertEqual(
            coordinator.calls,
            [("DELETE ip/traffic-shape/host", {"mac": "aa:bb:cc:dd:ee:ff"})],
        )
        self.assertEqual(coordinator.refreshes, 1)


if __name__ == "__main__":
    unittest.main()
