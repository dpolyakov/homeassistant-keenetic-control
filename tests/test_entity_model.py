"""Product-facing interface model tests."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


_PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "keenetic_control"
_PACKAGE_NAME = "custom_components.keenetic_control"
if _PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(_PACKAGE_NAME)
    package.__path__ = [str(_PACKAGE_PATH)]
    sys.modules[_PACKAGE_NAME] = package


def _load(name: str):
    qualified = f"{_PACKAGE_NAME}.{name}"
    if qualified in sys.modules:
        return sys.modules[qualified]
    spec = importlib.util.spec_from_file_location(qualified, _PACKAGE_PATH / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[qualified] = module
    spec.loader.exec_module(module)
    return module


_load("model")
ENTITY_MODEL = _load("entity_model")


class InterfaceEntityModelTests(unittest.TestCase):
    def test_vpn_administrative_state_is_separate_from_connection(self):
        model = ENTITY_MODEL.build_interface_entity_model(
            [
                {
                    "id": "Wireguard0",
                    "type": "Wireguard",
                    "state": "up",
                    "connected": "no",
                },
                {
                    "id": "Wireguard1",
                    "type": "Wireguard",
                    "state": "down",
                    "connected": "no",
                },
            ],
            [],
        )

        self.assertEqual(
            [(interface.identifier, interface.enabled, interface.connected) for interface in model.interfaces],
            [("Wireguard0", True, False), ("Wireguard1", False, False)],
        )

    def test_polls_only_semantic_wan_and_child_subsystems(self):
        model = ENTITY_MODEL.build_interface_entity_model(
            [
                {
                    "id": "WifiMaster0",
                    "type": "WifiMaster",
                    "connected": "yes",
                    "temperature": "52",
                },
                {
                    "id": "GigabitEthernet0",
                    "type": "GigabitEthernet",
                    "connected": "yes",
                },
                {
                    "id": "ISP",
                    "interface-name": "ISP",
                    "type": "PPPoE",
                    "connected": "yes",
                },
                {"id": "UsbLte0", "type": "UsbLte", "connected": "yes"},
                {"id": "Wireguard0", "type": "Wireguard", "connected": "yes"},
            ],
            [{"destination": "0.0.0.0/0", "interface": "ISP", "metric": 10}],
        )

        self.assertTrue(model.inventory_complete)
        self.assertEqual(model.primary_wan.identifier, "ISP")
        self.assertEqual(
            model.statistics_ids,
            frozenset({"ISP", "UsbLte0", "Wireguard0"}),
        )
        self.assertNotIn("WifiMaster0", model.statistics_ids)
        self.assertNotIn("GigabitEthernet0", model.statistics_ids)

    def test_exposes_semantic_wan_counters_only_for_unique_route(self):
        interfaces = [
            {
                "id": "Wan0",
                "interface-name": "ISP",
                "type": "Ethernet",
                "connected": "yes",
                "statistic": {"rxbytes": "1000", "txbytes": 2000},
            },
            {
                "id": "Wan1",
                "interface-name": "Backup",
                "type": "Ethernet",
                "connected": "yes",
                "statistic": {"rxbytes": 3000, "txbytes": 4000},
            },
        ]

        unique = ENTITY_MODEL.build_interface_entity_model(
            interfaces,
            [{"destination": "0.0.0.0/0", "interface": "ISP", "metric": 1}],
        )
        ambiguous = ENTITY_MODEL.build_interface_entity_model(
            interfaces,
            [
                {"destination": "0.0.0.0/0", "interface": "ISP", "metric": 1},
                {"destination": "0.0.0.0/0", "interface": "Backup", "metric": 1},
            ],
        )

        self.assertEqual(
            unique.primary_wan_counter("rxbytes"),
            ENTITY_MODEL.CounterSample("Wan0", 1000),
        )
        self.assertEqual(
            unique.primary_wan_counter("txbytes"),
            ENTITY_MODEL.CounterSample("Wan0", 2000),
        )
        self.assertIsNone(ambiguous.primary_wan)
        self.assertIsNone(ambiguous.primary_wan_counter("rxbytes"))

    def test_router_temperature_requires_one_wifi_master_reading(self):
        one = ENTITY_MODEL.build_interface_entity_model(
            [{"id": "WifiMaster0", "type": "WifiMaster", "temperature": "51"}],
            [],
        )
        ambiguous = ENTITY_MODEL.build_interface_entity_model(
            [
                {"id": "WifiMaster0", "type": "WifiMaster", "temperature": 51},
                {"id": "WifiMaster1", "type": "WifiMaster", "temperature": 49},
            ],
            [],
        )

        self.assertEqual(
            one.router_temperature,
            ENTITY_MODEL.TemperatureReading("WifiMaster0", 51),
        )
        self.assertIsNone(ambiguous.router_temperature)

    def test_access_points_have_user_facing_names(self):
        model = ENTITY_MODEL.build_interface_entity_model(
            [
                {
                    "id": "WifiMaster0/AccessPoint0",
                    "interface-name": "Home network",
                    "type": "AccessPoint",
                    "ssid": "My Wi-Fi",
                    "state": "up",
                },
                {
                    "id": "WifiMaster1/AccessPoint0",
                    "interface-name": "Guest network",
                    "type": "AccessPoint",
                    "state": "down",
                },
            ],
            [],
        )

        self.assertEqual(
            model.access_points,
            (
                ENTITY_MODEL.WifiAccessPoint(
                    "WifiMaster0/AccessPoint0", "My Wi-Fi", True
                ),
                ENTITY_MODEL.WifiAccessPoint(
                    "WifiMaster1/AccessPoint0", "Guest network", False
                ),
            ),
        )

    def test_malformed_interface_inventory_stays_unknown(self):
        model = ENTITY_MODEL.build_interface_entity_model(
            {"interface": [{"type": "WifiMaster", "temperature": 50}]},
            [],
        )

        self.assertFalse(model.inventory_complete)
        self.assertEqual(model.interfaces, ())
        self.assertEqual(model.statistics_ids, frozenset())
        self.assertIsNone(model.router_temperature)


if __name__ == "__main__":
    unittest.main()
