"""Dynamic device and entity cleanup tests against small HA registry fakes."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


_PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "keenetic_control"
_PACKAGE_NAME = "custom_components.keenetic_control"
if _PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(_PACKAGE_NAME)
    package.__path__ = [str(_PACKAGE_PATH)]
    sys.modules[_PACKAGE_NAME] = package


class _Entity:
    def __init__(self, entity_id, unique_id, entry_id):
        self.entity_id = entity_id
        self.unique_id = unique_id
        self.config_entry_id = entry_id
        self.platform = "keenetic_control"


class _Device:
    def __init__(self, device_id, identifiers, entry_id):
        self.id = device_id
        self.identifiers = identifiers
        self.config_entry_id = entry_id


class _EntityRegistry:
    def __init__(self, entries):
        self.entries = list(entries)
        self.removed = []
        self.renamed = []

    def async_remove(self, entity_id):
        self.removed.append(entity_id)
        self.entries = [entry for entry in self.entries if entry.entity_id != entity_id]

    def async_get(self, entity_id):
        return next(
            (entry for entry in self.entries if entry.entity_id == entity_id), None
        )

    def async_update_entity(self, entity_id, *, new_entity_id):
        entry = self.async_get(entity_id)
        if entry is None:
            raise AssertionError(f"Unknown entity {entity_id}")
        self.renamed.append((entity_id, new_entity_id))
        entry.entity_id = new_entity_id
        return entry


class _DeviceRegistry:
    def __init__(self, entries):
        self.entries = list(entries)
        self.removed = []

    def async_remove_device(self, device_id):
        self.removed.append(device_id)
        self.entries = [entry for entry in self.entries if entry.id != device_id]


class _Hass:
    def __init__(self, entities=(), devices=()):
        self.entity_registry = _EntityRegistry(entities)
        self.device_registry = _DeviceRegistry(devices)


class RegistryCleanupTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fake_ha = types.ModuleType("homeassistant")
        fake_config_entries = types.ModuleType("homeassistant.config_entries")
        fake_config_entries.ConfigEntry = object
        fake_core = types.ModuleType("homeassistant.core")
        fake_core.HomeAssistant = object
        fake_helpers = types.ModuleType("homeassistant.helpers")
        fake_devices = types.ModuleType("homeassistant.helpers.device_registry")
        fake_entities = types.ModuleType("homeassistant.helpers.entity_registry")
        fake_devices.async_get = lambda hass: hass.device_registry
        fake_devices.async_entries_for_config_entry = lambda registry, entry_id: [
            entry for entry in registry.entries if entry.config_entry_id == entry_id
        ]
        fake_entities.async_get = lambda hass: hass.entity_registry
        fake_entities.async_entries_for_config_entry = lambda registry, entry_id: [
            entity for entity in registry.entries if entity.config_entry_id == entry_id
        ]
        fake_helpers.device_registry = fake_devices
        fake_helpers.entity_registry = fake_entities
        modules = {
            "homeassistant": fake_ha,
            "homeassistant.config_entries": fake_config_entries,
            "homeassistant.core": fake_core,
            "homeassistant.helpers": fake_helpers,
            "homeassistant.helpers.device_registry": fake_devices,
            "homeassistant.helpers.entity_registry": fake_entities,
        }
        cls._module_patch = patch.dict(sys.modules, modules)
        cls._module_patch.start()
        module_path = _PACKAGE_PATH / "registry_cleanup.py"
        spec = importlib.util.spec_from_file_location(
            f"{_PACKAGE_NAME}.registry_cleanup_test", module_path
        )
        cls.cleanup = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = cls.cleanup
        spec.loader.exec_module(cls.cleanup)
        cls.domain = cls.cleanup.DOMAIN

    @classmethod
    def tearDownClass(cls):
        cls._module_patch.stop()
        sys.modules.pop(cls.cleanup.__name__, None)

    def _entry(self):
        return types.SimpleNamespace(entry_id="entry-1", unique_id="router")

    def test_retired_cleanup_removes_raw_entities_but_preserves_client_devices(self):
        retired = [
            _Entity("sensor.wifi", "router_interface_WifiMaster0_rxbytes", "entry-1"),
            _Entity("binary_sensor.ping", "router_ping_check_ISP_main", "entry-1"),
            _Entity("sensor.client_rssi", "router_client_aa:bb:cc:dd:ee:ff_rssi", "entry-1"),
            _Entity("sensor.last_boot", "router_last_boot", "entry-1"),
        ]
        preserved = [
            _Entity("select.client_policy", "router_client_aa:bb:cc:dd:ee:ff_policy", "entry-1"),
            _Entity("device_tracker.phone", "router_aa:bb:cc:dd:ee:ff", "entry-1"),
            _Entity("sensor.uptime", "router_uptime", "entry-1"),
        ]
        client_device = _Device(
            "client-device",
            {(self.domain, "router:client:aa:bb:cc:dd:ee:ff")},
            "entry-1",
        )
        hass = _Hass(entities=retired + preserved, devices=[client_device])

        removed = self.cleanup.cleanup_retired_registry(hass, self._entry())

        self.assertCountEqual(
            hass.entity_registry.removed,
            [entity.entity_id for entity in retired],
        )
        self.assertEqual(removed, {entity.unique_id for entity in retired})
        self.assertEqual(hass.device_registry.removed, [])

    def test_version_two_options_replace_broad_and_dead_toggles(self):
        migrated = self.cleanup.migrated_options_v2(
            {
                "all_clients": True,
                "clients": ["11:22:33:44:55:66"],
                "all_policies": True,
                "policy_clients": [],
                "port_forward_switches": True,
                "backup_types": ["config"],
                "scan_interval": 30,
            },
            {"aa:bb:cc:dd:ee:ff"},
        )

        self.assertEqual(
            migrated,
            {
                "clients": ["11:22:33:44:55:66", "aa:bb:cc:dd:ee:ff"],
                "policy_clients": ["aa:bb:cc:dd:ee:ff"],
                "scan_interval": 30,
                "allow_mutating_rci": False,
            },
        )

    def test_duplicate_client_entity_id_is_renamed_without_duplicate_device_slug(self):
        entity = _Entity(
            "sensor.moi_aifon_moi_aifon_wi_fi_mode",
            "router_client_aa:bb:cc:dd:ee:ff_wifi_mode",
            "entry-1",
        )
        hass = _Hass(entities=[entity])

        renamed = self.cleanup.migrate_duplicate_client_entity_ids(
            hass, self._entry()
        )

        self.assertEqual(renamed, {("sensor.moi_aifon_moi_aifon_wi_fi_mode", "sensor.moi_aifon_wi_fi_mode")})
        self.assertEqual(entity.entity_id, "sensor.moi_aifon_wi_fi_mode")

    def test_ping_check_cleanup_runs_without_other_valid_inventories(self):
        entity = _Entity("binary_sensor.old_ping", "router_ping_check_Wan0_old", "entry-1")
        hass = _Hass(entities=[entity])

        removed = self.cleanup.cleanup_dynamic_registry(
            hass,
            self._entry(),
            interface_response=None,
            mesh_response=None,
            ping_check_response={"pingcheck": []},
        )

        self.assertEqual(hass.entity_registry.removed, ["binary_sensor.old_ping"])
        self.assertEqual(removed, {"router_ping_check_Wan0_old"})

    def test_complete_empty_interface_inventory_removes_modem_entities_and_device(self):
        entities = [
            _Entity("sensor.modem_sms", "router_modem_UsbQmi0_unread_sms", "entry-1"),
            _Entity("button.modem_restart", "router_modem_UsbQmi0_restart", "entry-1"),
        ]
        device = _Device(
            "modem-device",
            {(self.domain, "modem:router|UsbQmi0")},
            "entry-1",
        )
        hass = _Hass(entities=entities, devices=[device])

        removed = self.cleanup.cleanup_dynamic_registry(
            hass,
            self._entry(),
            interface_response={"interface": []},
            mesh_response=None,
            ping_check_response=None,
        )

        self.assertCountEqual(
            hass.entity_registry.removed,
            ["sensor.modem_sms", "button.modem_restart"],
        )
        self.assertEqual(hass.device_registry.removed, ["modem-device"])
        self.assertEqual(removed, {entity.unique_id for entity in entities})

    def test_gigabit_port_entity_is_preserved_by_interface_cleanup(self):
        entity = _Entity(
            "sensor.router_port_1",
            "router_port_GigabitEthernet0/1",
            "entry-1",
        )
        hass = _Hass(entities=[entity])

        removed = self.cleanup.cleanup_dynamic_registry(
            hass,
            self._entry(),
            interface_response={"interface": []},
            mesh_response=None,
            ping_check_response=None,
        )

        self.assertEqual(hass.entity_registry.removed, [])
        self.assertEqual(removed, set())

    def test_removed_mesh_member_cleans_every_backhaul_entity(self):
        suffixes = (
            "firmware_version",
            "port_0",
            "backhaul_ap",
            "backhaul_rssi",
            "backhaul_txrate",
        )
        entities = [
            _Entity(
                f"sensor.mesh_{suffix}",
                f"router_mesh_member-1_{suffix}",
                "entry-1",
            )
            for suffix in suffixes
        ]
        hass = _Hass(entities=entities)

        removed = self.cleanup.cleanup_dynamic_registry(
            hass,
            self._entry(),
            interface_response=None,
            mesh_response={"member": []},
            ping_check_response=None,
        )

        self.assertCountEqual(
            hass.entity_registry.removed,
            [entity.entity_id for entity in entities],
        )
        self.assertEqual(removed, {entity.unique_id for entity in entities})

    def test_removed_mesh_member_keeps_device_used_by_another_entry(self):
        device = _Device(
            "shared-mesh-device",
            {(self.domain, "mesh:router|member-1")},
            "entry-2",
        )
        hass = _Hass(devices=[device])

        self.cleanup.cleanup_dynamic_registry(
            hass,
            self._entry(),
            interface_response=None,
            mesh_response={"member": []},
            ping_check_response=None,
        )

        self.assertEqual(hass.device_registry.removed, [])
        self.assertFalse(self.cleanup._only_used_by_entry(device, "entry-1"))

    def test_mesh_backhaul_speed_without_source_is_removed(self):
        entity = _Entity(
            "sensor.mesh_speed",
            "router_mesh_member-1_backhaul_speed",
            "entry-1",
        )
        hass = _Hass(entities=[entity])

        removed = self.cleanup.cleanup_dynamic_registry(
            hass,
            self._entry(),
            interface_response=None,
            mesh_response={"member": [{"mac": "member-1", "backhaul": {}}]},
            ping_check_response=None,
        )

        self.assertEqual(hass.entity_registry.removed, ["sensor.mesh_speed"])
        self.assertEqual(removed, {entity.unique_id})

    def test_mesh_inventory_accepts_cid_only_members(self):
        entity = _Entity("sensor.mesh_member_uptime", "router_mesh_member-123_uptime", "entry-1")
        hass = _Hass(entities=[entity])

        removed = self.cleanup.cleanup_dynamic_registry(
            hass,
            self._entry(),
            interface_response=None,
            mesh_response={"member": [{"cid": "member-123"}]},
            ping_check_response=None,
        )

        self.assertEqual(hass.entity_registry.removed, [])
        self.assertEqual(removed, set())

    def test_malformed_inventory_does_not_remove_registered_entities(self):
        entity = _Entity("sensor.modem_sms", "router_modem_UsbQmi0_unread_sms", "entry-1")
        hass = _Hass(entities=[entity])

        removed = self.cleanup.cleanup_dynamic_registry(
            hass,
            self._entry(),
            interface_response={"interface": [{"description": "incomplete"}]},
            mesh_response=None,
            ping_check_response=None,
        )

        self.assertEqual(hass.entity_registry.removed, [])
        self.assertEqual(removed, set())

    def test_id_only_interface_inventory_preserves_registered_entities(self):
        entity = _Entity("binary_sensor.old_vpn", "router_interface_OldTunnel_connected", "entry-1")
        hass = _Hass(entities=[entity])

        removed = self.cleanup.cleanup_dynamic_registry(
            hass,
            self._entry(),
            interface_response={"interface": [{"id": "NewTunnel"}]},
            mesh_response=None,
            ping_check_response=None,
        )

        self.assertEqual(hass.entity_registry.removed, [])
        self.assertEqual(removed, set())

    def test_removed_wireguard_interface_cleans_its_peer_entities(self):
        peer_id = "a" * 64
        entity = _Entity(
            "sensor.wireguard_peer",
            f"router_wireguard_Wireguard0_{peer_id}_online",
            "entry-1",
        )
        hass = _Hass(entities=[entity])

        removed = self.cleanup.cleanup_dynamic_registry(
            hass,
            self._entry(),
            interface_response={"interface": []},
            mesh_response=None,
            ping_check_response=None,
        )

        self.assertEqual(hass.entity_registry.removed, ["sensor.wireguard_peer"])
        self.assertEqual(removed, {entity.unique_id})

    def test_removed_openvpn_interface_cleans_its_child_device(self):
        entities = [
            _Entity(
                "switch.openvpn_admin_up",
                "router_interface_OpenVPN0_admin_up",
                "entry-1",
            ),
            _Entity(
                "switch.openvpn_enabled",
                "router_vpn_OpenVPN0_enabled",
                "entry-1",
            ),
        ]
        device = _Device(
            "openvpn-device",
            {(self.domain, "vpn:router|OpenVPN0")},
            "entry-1",
        )
        hass = _Hass(entities=entities, devices=[device])

        removed = self.cleanup.cleanup_dynamic_registry(
            hass,
            self._entry(),
            interface_response={"interface": []},
            mesh_response=None,
            ping_check_response=None,
        )

        self.assertCountEqual(
            hass.entity_registry.removed,
            ["switch.openvpn_admin_up", "switch.openvpn_enabled"],
        )
        self.assertEqual(hass.device_registry.removed, ["openvpn-device"])
        self.assertEqual(removed, {entity.unique_id for entity in entities})

    def test_active_openvpn_interface_preserves_its_child_device(self):
        device = _Device(
            "openvpn-device",
            {(self.domain, "vpn:router|OpenVPN0")},
            "entry-1",
        )
        hass = _Hass(devices=[device])

        self.cleanup.cleanup_dynamic_registry(
            hass,
            self._entry(),
            interface_response={"interface": [{"id": "OpenVPN0", "type": "OpenVPN"}]},
            mesh_response=None,
            ping_check_response=None,
        )

        self.assertEqual(hass.device_registry.removed, [])

    def test_unknown_present_interface_type_preserves_vpn_child_device(self):
        device = _Device(
            "secure-device",
            {(self.domain, "vpn:router|Secure0")},
            "entry-1",
        )
        hass = _Hass(devices=[device])

        self.cleanup.cleanup_dynamic_registry(
            hass,
            self._entry(),
            interface_response={"interface": [{"id": "Secure0", "type": "Secure"}]},
            mesh_response=None,
            ping_check_response=None,
        )

        self.assertEqual(hass.device_registry.removed, [])

    def test_keyed_interface_inventory_preserves_present_vpn_child_device(self):
        device = _Device(
            "secure-device",
            {(self.domain, "vpn:router|Secure0")},
            "entry-1",
        )
        hass = _Hass(devices=[device])

        self.cleanup.cleanup_dynamic_registry(
            hass,
            self._entry(),
            interface_response={
                "interface": {
                    "Secure0": {"id": "Secure0", "type": "Secure"},
                    "WifiMaster0": {"id": "WifiMaster0", "type": "WifiMaster"},
                }
            },
            mesh_response=None,
            ping_check_response=None,
        )

        self.assertEqual(hass.device_registry.removed, [])

    def test_inconsistent_named_interface_key_preserves_vpn_child_device(self):
        device = _Device(
            "old-device",
            {(self.domain, "vpn:router|Old0")},
            "entry-1",
        )
        hass = _Hass(devices=[device])

        removed = self.cleanup.cleanup_dynamic_registry(
            hass,
            self._entry(),
            interface_response={
                "interface": {
                    "New0": {"id": "Other0", "type": "OpenVPN"},
                }
            },
            mesh_response=None,
            ping_check_response=None,
        )

        self.assertEqual(hass.device_registry.removed, [])
        self.assertEqual(removed, set())

    def test_removed_wireguard_peer_is_cleaned_only_after_complete_inventory(self):
        peer_id = "a" * 16
        entity = _Entity(
            "sensor.wireguard_peer",
            f"router_wireguard_Wireguard0_{peer_id}_online",
            "entry-1",
        )
        hass = _Hass(entities=[entity])
        self.cleanup.cleanup_dynamic_registry(
            hass,
            self._entry(),
            interface_response={"interface": [{"id": "Wireguard0", "type": "Wireguard"}]},
            mesh_response=None,
            ping_check_response=None,
            wireguard_peer_response={"interface_id": "Wireguard0", "peers": []},
        )

        self.assertEqual(hass.entity_registry.removed, ["sensor.wireguard_peer"])

    def test_unavailable_wireguard_peer_inventory_preserves_entities(self):
        peer_id = "a" * 16
        entity = _Entity(
            "sensor.wireguard_peer",
            f"router_wireguard_Wireguard0_{peer_id}_online",
            "entry-1",
        )
        hass = _Hass(entities=[entity])
        self.cleanup.cleanup_dynamic_registry(
            hass,
            self._entry(),
            interface_response={"interface": [{"id": "Wireguard0", "type": "Wireguard"}]},
            mesh_response=None,
            ping_check_response=None,
            wireguard_peer_response=None,
        )

        self.assertEqual(hass.entity_registry.removed, [])


if __name__ == "__main__":
    unittest.main()
