"""Focused tests for explicit client trackers without child devices."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


_PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "keenetic_control"
_PACKAGE_NAME = "keenetic_control_tracker_test"


class _CoordinatorEntity:
    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self):
        return self.coordinator.last_update_success


def _load_module():
    fake_homeassistant = types.ModuleType("homeassistant")
    fake_components = types.ModuleType("homeassistant.components")
    fake_tracker = types.ModuleType("homeassistant.components.device_tracker")
    fake_tracker.ScannerEntity = type("ScannerEntity", (), {})
    fake_tracker.SourceType = types.SimpleNamespace(ROUTER="router")
    fake_config_entries = types.ModuleType("homeassistant.config_entries")
    fake_config_entries.ConfigEntry = object
    fake_core = types.ModuleType("homeassistant.core")
    fake_core.HomeAssistant = object
    fake_core.callback = lambda function: function
    fake_helpers = types.ModuleType("homeassistant.helpers")
    fake_entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    fake_entity_platform.AddEntitiesCallback = object
    fake_update = types.ModuleType("homeassistant.helpers.update_coordinator")
    fake_update.CoordinatorEntity = _CoordinatorEntity
    fake_package = types.ModuleType(_PACKAGE_NAME)
    fake_package.__path__ = [str(_PACKAGE_PATH)]
    fake_client_registry = types.ModuleType(f"{_PACKAGE_NAME}.client_registry")

    def normalize_mac(value):
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower().replace("-", ":")
        parts = normalized.split(":")
        return normalized if len(parts) == 6 and all(len(part) == 2 for part in parts) else None

    fake_client_registry.normalize_mac = normalize_mac
    fake_client_registry.cleanup_unselected_clients = lambda *_args: None
    fake_device_helpers = types.ModuleType(f"{_PACKAGE_NAME}.device_helpers")
    fake_device_helpers.router_device_id = lambda _hass, _entry: "router-device"
    fake_components.device_tracker = fake_tracker
    modules = {
        "homeassistant": fake_homeassistant,
        "homeassistant.components": fake_components,
        "homeassistant.components.device_tracker": fake_tracker,
        "homeassistant.config_entries": fake_config_entries,
        "homeassistant.core": fake_core,
        "homeassistant.helpers": fake_helpers,
        "homeassistant.helpers.entity_platform": fake_entity_platform,
        "homeassistant.helpers.update_coordinator": fake_update,
        _PACKAGE_NAME: fake_package,
        f"{_PACKAGE_NAME}.client_registry": fake_client_registry,
        f"{_PACKAGE_NAME}.device_helpers": fake_device_helpers,
    }
    module_patch = patch.dict(sys.modules, modules)
    module_patch.start()
    try:
        path = _PACKAGE_PATH / "device_tracker.py"
        spec = importlib.util.spec_from_file_location(f"{_PACKAGE_NAME}.device_tracker", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        module_patch.stop()
        for name in list(sys.modules):
            if name == _PACKAGE_NAME or name.startswith(f"{_PACKAGE_NAME}."):
                sys.modules.pop(name, None)
    return module


TRACKER = _load_module()


class ClientTrackerTests(unittest.TestCase):
    def _entity(self, hotspot):
        coordinator = types.SimpleNamespace(
            data={"hotspot": hotspot},
            last_update_success=True,
        )
        entry = types.SimpleNamespace(unique_id="router")
        return TRACKER.KeeneticClientTracker(
            coordinator,
            entry,
            "aa:bb:cc:dd:ee:ff",
            None,
        )

    def test_tracker_uses_client_child_device_and_details_as_attributes(self):
        entity = self._entity(
            {
                "host": [
                    {
                        "mac": "aa:bb:cc:dd:ee:ff",
                        "name": "Phone",
                        "ip": "192.0.2.10",
                        "interface": "Home Wi-Fi",
                        "rssi": -52,
                        "uptime": 120,
                    }
                ]
            }
        )

        self.assertTrue(entity.is_connected)
        self.assertEqual(entity.ip_address, "192.0.2.10")
        self.assertEqual(
            entity.extra_state_attributes,
            {"interface": "Home Wi-Fi", "rssi": -52, "uptime": 120},
        )
        entity.hass = types.SimpleNamespace()
        self.assertEqual(
            entity.device_info,
            {
                "identifiers": {
                    ("keenetic_control", "router:client:aa:bb:cc:dd:ee:ff")
                },
                "name": "Phone",
                "via_device_id": "router-device",
            },
        )

    def test_empty_inventory_means_away_but_malformed_inventory_is_unavailable(self):
        entity = self._entity({"host": []})
        self.assertFalse(entity.is_connected)
        self.assertTrue(entity.available)

        entity.coordinator.data = {"hotspot": {"unexpected": "shape"}}
        self.assertIsNone(entity.is_connected)
        self.assertFalse(entity.available)


if __name__ == "__main__":
    unittest.main()
