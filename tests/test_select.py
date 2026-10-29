"""Focused tests for per-client IP policy select entities."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


_PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "keenetic_control"
_PACKAGE_NAME = "keenetic_control_select_test"


class _SelectEntity:
    def async_write_ha_state(self):
        self.wrote_state = True


class _CoordinatorEntity:
    def __class_getitem__(cls, _item):
        return cls

    def __init__(self, coordinator):
        self.coordinator = coordinator

    @property
    def available(self):
        return self.coordinator.last_update_success


class _Registry:
    def __init__(self, entries):
        self.entries = list(entries)
        self.removed = []

    def async_remove(self, entity_id):
        self.removed.append(entity_id)
        self.entries = [entry for entry in self.entries if entry.entity_id != entity_id]


class _RegistryEntity:
    def __init__(self, entity_id, unique_id, entry_id, platform, domain):
        self.entity_id = entity_id
        self.unique_id = unique_id
        self.config_entry_id = entry_id
        self.platform = platform
        self.domain = domain


class _Hass:
    def __init__(self, entities=()):
        self.entity_registry = _Registry(entities)


class _Coordinator:
    def __init__(self, data, reflect_writes=True):
        self.data = data
        self.reflect_writes = reflect_writes
        self.last_update_success = True
        self.calls = []

    async def async_call(self, endpoint, payload):
        self.calls.append((endpoint, payload))
        if endpoint == "ip/hotspot/host" and self.reflect_writes:
            for record in self.data.get("hotspot", {}).get("host", []):
                if record.get("mac") == payload.get("mac"):
                    record.update(payload)

    async def async_request_refresh(self):
        return None


class _Entry:
    entry_id = "entry-1"
    unique_id = "router"
    options = {}


def _load_select_module():
    fake_homeassistant = types.ModuleType("homeassistant")
    fake_config_entries = types.ModuleType("homeassistant.config_entries")
    fake_config_entries.ConfigEntry = object
    fake_core = types.ModuleType("homeassistant.core")
    fake_core.HomeAssistant = object
    fake_core.callback = lambda function: function
    fake_exceptions = types.ModuleType("homeassistant.exceptions")
    fake_exceptions.HomeAssistantError = type("HomeAssistantError", (Exception,), {})

    fake_components = types.ModuleType("homeassistant.components")
    fake_select = types.ModuleType("homeassistant.components.select")
    fake_select.SelectEntity = _SelectEntity

    fake_helpers = types.ModuleType("homeassistant.helpers")
    fake_devices = types.ModuleType("homeassistant.helpers.device_registry")
    fake_entities = types.ModuleType("homeassistant.helpers.entity_registry")

    class DeviceInfo(dict):
        def __init__(self, **values):
            super().__init__(values)

    fake_devices.DeviceInfo = DeviceInfo
    fake_devices.async_get = lambda hass: getattr(hass, "device_registry", None)
    fake_devices.async_get_device_id_by_identifier = (
        lambda hass, _identifier, **_kwargs: "router-device"
    )
    fake_devices.async_entries_for_config_entry = lambda _registry, _entry_id: []
    fake_entities.async_get = lambda hass: hass.entity_registry
    fake_entities.async_entries_for_config_entry = lambda registry, entry_id: [
        entity for entity in registry.entries if entity.config_entry_id == entry_id
    ]
    fake_helpers.device_registry = fake_devices
    fake_helpers.entity_registry = fake_entities

    fake_entity = types.ModuleType("homeassistant.helpers.entity")
    fake_entity.EntityCategory = types.SimpleNamespace(CONFIG="config")
    fake_entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    fake_entity_platform.AddEntitiesCallback = object
    fake_update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")
    fake_update_coordinator.CoordinatorEntity = _CoordinatorEntity
    fake_package = types.ModuleType(_PACKAGE_NAME)
    fake_package.__path__ = [str(_PACKAGE_PATH)]
    fake_coordinator_module = types.ModuleType(f"{_PACKAGE_NAME}.coordinator")
    fake_coordinator_module.KeeneticCoordinator = object

    fake_components.select = fake_select
    modules = {
        "homeassistant": fake_homeassistant,
        "homeassistant.components": fake_components,
        "homeassistant.components.select": fake_select,
        "homeassistant.config_entries": fake_config_entries,
        "homeassistant.core": fake_core,
        "homeassistant.exceptions": fake_exceptions,
        "homeassistant.helpers": fake_helpers,
        "homeassistant.helpers.device_registry": fake_devices,
        "homeassistant.helpers.entity_registry": fake_entities,
        "homeassistant.helpers.entity": fake_entity,
        "homeassistant.helpers.entity_platform": fake_entity_platform,
        "homeassistant.helpers.update_coordinator": fake_update_coordinator,
        _PACKAGE_NAME: fake_package,
        f"{_PACKAGE_NAME}.coordinator": fake_coordinator_module,
    }
    module_patch = patch.dict(sys.modules, modules)
    module_patch.start()
    try:
        module_path = _PACKAGE_PATH / "select.py"
        spec = importlib.util.spec_from_file_location(f"{_PACKAGE_NAME}.select", module_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    finally:
        module_patch.stop()
        for name in list(sys.modules):
            if name == _PACKAGE_NAME or name.startswith(f"{_PACKAGE_NAME}."):
                sys.modules.pop(name, None)
    return module


SELECT = _load_select_module()


class PolicyOptionTests(unittest.TestCase):
    def test_options_use_policy_name_as_id_and_include_description(self):
        self.assertEqual(
            SELECT._policy_options(
                {"policies": {"policy": [{"name": "Policy0", "description": "VPN route"}]}}
            ),
            {"VPN route (Policy0)": "Policy0"},
        )

    def test_options_accept_mapping_from_policy_ids_to_descriptions(self):
        self.assertEqual(
            SELECT._policy_options({"policies": {"Policy1": {"description": "Guest"}}}),
            {"Guest (Policy1)": "Policy1"},
        )

    def test_missing_policy_description_shows_the_id_once(self):
        self.assertEqual(
            SELECT._policy_options({"policies": {"policy": [{"name": "Policy0"}]}}),
            {"Policy0": "Policy0"},
        )

    def test_missing_policy_is_not_inferred_as_a_default(self):
        coordinator = _Coordinator(
            {
                "policies": {"policy": [{"name": "Policy0", "description": "VPN route"}]},
                "hotspot": {"host": [{"mac": "aa:bb:cc:dd:ee:ff"}]},
            }
        )
        entity = SELECT.KeeneticClientPolicySelect(coordinator, _Entry(), "aa:bb:cc:dd:ee:ff")

        self.assertIsNone(entity.current_option)

    def test_current_option_uses_the_host_policy_id_and_client_mac(self):
        mac = "aa:bb:cc:dd:ee:ff"
        coordinator = _Coordinator(
            {
                "policies": {"policy": [{"name": "Policy0", "description": "VPN route"}]},
                "hotspot": {"host": [{"mac": mac, "policy": "Policy0"}]},
            }
        )
        entity = SELECT.KeeneticClientPolicySelect(coordinator, _Entry(), mac)
        entity.hass = types.SimpleNamespace()

        self.assertEqual(entity.current_option, "VPN route (Policy0)")
        self.assertEqual(
            entity.device_info["identifiers"],
            {("keenetic_control", "router:client:aa:bb:cc:dd:ee:ff")},
        )
        self.assertEqual(entity.device_info["via_device_id"], "router-device")

    def test_options_include_no_internet_and_current_state_uses_access_deny(self):
        mac = "aa:bb:cc:dd:ee:ff"
        coordinator = _Coordinator(
            {"policies": {"Policy0": {"description": "VPN route"}}, "hotspot": {"host": [{"mac": mac, "access": "deny"}]}}
        )
        entity = SELECT.KeeneticClientPolicySelect(coordinator, _Entry(), mac)
        entity.hass = types.SimpleNamespace(config=types.SimpleNamespace(language="en"))

        self.assertEqual(entity.options, ["No internet", "VPN route (Policy0)"])
        self.assertEqual(entity.current_option, "No internet")


class PolicySelectActionTests(unittest.IsolatedAsyncioTestCase):
    async def test_select_posts_actual_policy_id_for_live_host_and_refreshes(self):
        coordinator = _Coordinator(
            {
                "policies": {"policy": [{"name": "Policy0", "description": "VPN route"}]},
                "hotspot": {"host": [{"mac": "aa:bb:cc:dd:ee:ff", "policy": "Policy0"}]},
            }
        )
        entity = SELECT.KeeneticClientPolicySelect(coordinator, _Entry(), "aa:bb:cc:dd:ee:ff")
        entity.hass = types.SimpleNamespace()

        await entity.async_select_option("VPN route (Policy0)")

        self.assertEqual(
            coordinator.calls,
            [
                (
                    "ip/hotspot/host",
                    {"mac": "aa:bb:cc:dd:ee:ff", "access": "permit", "policy": "Policy0"},
                )
            ],
        )
        self.assertTrue(entity.wrote_state)

    async def test_select_can_deny_internet_for_one_host_and_refreshes(self):
        mac = "aa:bb:cc:dd:ee:ff"
        coordinator = _Coordinator(
            {"policies": {"Policy0": {"description": "VPN route"}}, "hotspot": {"host": [{"mac": mac, "access": "permit", "policy": "Policy0"}]}}
        )
        entity = SELECT.KeeneticClientPolicySelect(coordinator, _Entry(), mac)
        entity.hass = types.SimpleNamespace(config=types.SimpleNamespace(language="en"))

        await entity.async_select_option("No internet")

        self.assertEqual(coordinator.calls, [("ip/hotspot/host", {"mac": mac, "access": "deny"})])
        self.assertEqual(entity.current_option, "No internet")
        self.assertTrue(entity.wrote_state)

    async def test_os4_policy_write_does_not_fail_when_hotspot_omits_policy_field(self):
        mac = "aa:bb:cc:dd:ee:ff"
        coordinator = _Coordinator(
            {
                "policies": {"Policy0": {"description": "VPN route"}},
                "hotspot": {"host": [{"mac": mac, "access": "permit"}]},
            },
            reflect_writes=False,
        )
        entity = SELECT.KeeneticClientPolicySelect(coordinator, _Entry(), mac)
        entity.hass = types.SimpleNamespace(config=types.SimpleNamespace(language="en"))

        await entity.async_select_option("VPN route (Policy0)")

        self.assertEqual(
            coordinator.calls,
            [("ip/hotspot/host", {"mac": mac, "access": "permit", "policy": "Policy0"})],
        )
        self.assertTrue(entity.wrote_state)

    async def test_select_rejects_host_absent_from_current_hotspot_inventory(self):
        coordinator = _Coordinator(
            {
                "policies": {"policy": [{"name": "Policy0", "description": "VPN route"}]},
                "hotspot": {"host": []},
            }
        )
        entity = SELECT.KeeneticClientPolicySelect(coordinator, _Entry(), "aa:bb:cc:dd:ee:ff")

        with self.assertRaises(SELECT.HomeAssistantError):
            await entity.async_select_option("VPN route (Policy0)")

        self.assertEqual(coordinator.calls, [])


class PolicySelectRegistryScopeTests(unittest.TestCase):
    def test_cleanup_removes_only_deselected_selects_from_this_entry(self):
        mac = "aa:bb:cc:dd:ee:ff"
        stale_mac = "11:22:33:44:55:66"
        entities = [
            _RegistryEntity(
                "select.kept",
                f"router_client_{mac}_policy",
                "entry-1",
                "keenetic_control",
                "select",
            ),
            _RegistryEntity(
                "select.stale",
                f"router_client_{stale_mac}_policy",
                "entry-1",
                "keenetic_control",
                "select",
            ),
            _RegistryEntity(
                "device_tracker.client",
                f"router_{stale_mac}",
                "entry-1",
                "keenetic_control",
                "device_tracker",
            ),
            _RegistryEntity(
                "select.other",
                f"router_client_{stale_mac}_policy",
                "entry-2",
                "keenetic_control",
                "select",
            ),
            _RegistryEntity(
                "select.foreign",
                f"router_client_{stale_mac}_policy",
                "entry-1",
                "other_integration",
                "select",
            ),
        ]
        hass = _Hass(entities)

        SELECT._cleanup_policy_selects(hass, _Entry(), {mac})

        self.assertEqual(hass.entity_registry.removed, ["select.stale"])

    def test_disabling_selectors_removes_only_this_entries_policy_selects(self):
        mac = "aa:bb:cc:dd:ee:ff"
        entities = [
            _RegistryEntity(
                "select.policy",
                f"router_client_{mac}_policy",
                "entry-1",
                "keenetic_control",
                "select",
            ),
            _RegistryEntity(
                "select.other",
                f"router_setting_{mac}",
                "entry-1",
                "keenetic_control",
                "select",
            ),
            _RegistryEntity(
                "device_tracker.client",
                f"router_{mac}",
                "entry-1",
                "keenetic_control",
                "device_tracker",
            ),
            _RegistryEntity(
                "select.foreign_entry",
                f"router_client_{mac}_policy",
                "entry-2",
                "keenetic_control",
                "select",
            ),
            _RegistryEntity(
                "select.foreign_platform",
                f"router_client_{mac}_policy",
                "entry-1",
                "other_integration",
                "select",
            ),
        ]
        hass = _Hass(entities)

        SELECT._cleanup_policy_selects(hass, _Entry(), set())

        self.assertEqual(hass.entity_registry.removed, ["select.policy"])


if __name__ == "__main__":
    unittest.main()
