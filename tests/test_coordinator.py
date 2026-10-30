"""Coordinator identity tests with minimal Home Assistant import stubs."""

from __future__ import annotations

import asyncio
import importlib.util
import importlib
import datetime
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

if not hasattr(datetime, "UTC"):
    datetime.UTC = datetime.timezone.utc


_PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "keenetic_control"
_PACKAGE_NAME = "custom_components.keenetic_control"
if _PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(_PACKAGE_NAME)
    package.__path__ = [str(_PACKAGE_PATH)]
    sys.modules[_PACKAGE_NAME] = package

if "aiohttp" not in sys.modules and importlib.util.find_spec("aiohttp") is None:
    aiohttp = types.ModuleType("aiohttp")
    aiohttp.ClientError = type("ClientError", (Exception,), {})
    aiohttp.ClientSession = object

    class ClientTimeout:
        def __init__(self, total=None):
            self.total = total

    class CookieJar:
        def __init__(self, unsafe=False):
            self.unsafe = unsafe

        def filter_cookies(self, _url):
            return {}

        def update_cookies(self, _cookies, _url):
            return None

    aiohttp.ClientTimeout = ClientTimeout
    aiohttp.CookieJar = CookieJar
    sys.modules["aiohttp"] = aiohttp

if "yarl" not in sys.modules and importlib.util.find_spec("yarl") is None:
    yarl = types.ModuleType("yarl")
    yarl.URL = str
    sys.modules["yarl"] = yarl


def _install_homeassistant_stubs() -> None:
    if "homeassistant" in sys.modules:
        return
    fake_ha = types.ModuleType("homeassistant")
    fake_config_entries = types.ModuleType("homeassistant.config_entries")
    fake_config_entries.ConfigEntry = object

    class ConfigFlow:
        def __init_subclass__(cls, *, domain=None, **kwargs):
            super().__init_subclass__(**kwargs)

    fake_config_entries.ConfigFlow = ConfigFlow
    fake_config_entries.OptionsFlowWithReload = type("OptionsFlowWithReload", (), {})
    fake_core = types.ModuleType("homeassistant.core")
    fake_core.HomeAssistant = object
    fake_core.callback = lambda function: function
    fake_exceptions = types.ModuleType("homeassistant.exceptions")
    fake_exceptions.ConfigEntryAuthFailed = type("ConfigEntryAuthFailed", (Exception,), {})
    fake_exceptions.HomeAssistantError = type("HomeAssistantError", (Exception,), {})
    fake_helpers = types.ModuleType("homeassistant.helpers")
    fake_config_validation = types.ModuleType("homeassistant.helpers.config_validation")
    fake_helpers.config_validation = fake_config_validation
    fake_devices = types.ModuleType("homeassistant.helpers.device_registry")
    fake_entities = types.ModuleType("homeassistant.helpers.entity_registry")
    fake_devices.async_get = lambda hass: hass.device_registry
    fake_devices.async_entries_for_config_entry = lambda registry, entry_id: []
    fake_entities.async_get = lambda hass: hass.entity_registry
    fake_entities.async_entries_for_config_entry = lambda registry, entry_id: []
    fake_helpers.device_registry = fake_devices
    fake_helpers.entity_registry = fake_entities
    fake_aiohttp_client = types.ModuleType("homeassistant.helpers.aiohttp_client")
    fake_aiohttp_client.async_get_clientsession = lambda *args, **kwargs: None
    fake_helpers.aiohttp_client = fake_aiohttp_client
    fake_selector = types.ModuleType("homeassistant.helpers.selector")

    class SelectorConfig:
        def __init__(self, **kwargs):
            self.config = kwargs

    class Selector:
        def __init__(self, config=None):
            self.config = config

    fake_selector.BooleanSelector = Selector
    fake_selector.NumberSelector = Selector
    fake_selector.NumberSelectorConfig = SelectorConfig
    fake_selector.NumberSelectorMode = SimpleNamespace(BOX="box")
    fake_selector.SelectOptionDict = dict
    fake_selector.SelectSelector = Selector
    fake_selector.SelectSelectorConfig = SelectorConfig
    fake_selector.SelectSelectorMode = SimpleNamespace(DROPDOWN="dropdown", LIST="list")
    fake_selector.TextSelector = Selector
    fake_selector.TextSelectorConfig = SelectorConfig
    fake_selector.TextSelectorType = SimpleNamespace(PASSWORD="password")
    fake_entity_platform = types.ModuleType("homeassistant.helpers.entity_platform")
    fake_entity_platform.AddEntitiesCallback = object
    fake_update_coordinator = types.ModuleType("homeassistant.helpers.update_coordinator")

    class DataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, hass, _logger, **_kwargs):
            self.hass = hass

    fake_update_coordinator.DataUpdateCoordinator = DataUpdateCoordinator
    fake_update_coordinator.CoordinatorEntity = DataUpdateCoordinator
    fake_update_coordinator.UpdateFailed = type("UpdateFailed", (Exception,), {})
    fake_devices.ChildDeviceInfo = dict
    fake_devices.DeviceInfo = dict

    fake_components = types.ModuleType("homeassistant.components")
    fake_sensor = types.ModuleType("homeassistant.components.sensor")
    fake_sensor.SensorDeviceClass = SimpleNamespace(
        DATA_RATE="data_rate",
        DATA_SIZE="data_size",
        DURATION="duration",
        SIGNAL_STRENGTH="signal_strength",
        TEMPERATURE="temperature",
        TIMESTAMP="timestamp",
    )
    fake_sensor.SensorEntity = type("SensorEntity", (), {})
    fake_sensor.SensorStateClass = SimpleNamespace(
        MEASUREMENT="measurement", TOTAL_INCREASING="total_increasing"
    )
    fake_binary_sensor = types.ModuleType("homeassistant.components.binary_sensor")
    fake_binary_sensor.BinarySensorDeviceClass = SimpleNamespace(
        CONNECTIVITY="connectivity", PROBLEM="problem"
    )
    fake_binary_sensor.BinarySensorEntity = type("BinarySensorEntity", (), {})
    fake_components.sensor = fake_sensor
    fake_components.binary_sensor = fake_binary_sensor
    fake_const = types.ModuleType("homeassistant.const")
    fake_const.CONF_PASSWORD = "password"
    fake_const.EntityCategory = SimpleNamespace(DIAGNOSTIC="diagnostic")
    fake_const.UnitOfDataRate = SimpleNamespace(MEGABITS_PER_SECOND="Mbit/s")
    fake_const.UnitOfInformation = SimpleNamespace(BYTES="B", MEGABYTES="MB")
    fake_const.UnitOfTemperature = SimpleNamespace(CELSIUS="°C")
    fake_const.UnitOfTime = SimpleNamespace(DAYS="d", SECONDS="s")
    sys.modules.update(
        {
            "homeassistant": fake_ha,
            "homeassistant.components": fake_components,
            "homeassistant.components.sensor": fake_sensor,
            "homeassistant.components.binary_sensor": fake_binary_sensor,
            "homeassistant.config_entries": fake_config_entries,
            "homeassistant.const": fake_const,
            "homeassistant.core": fake_core,
            "homeassistant.exceptions": fake_exceptions,
            "homeassistant.helpers": fake_helpers,
            "homeassistant.helpers.aiohttp_client": fake_aiohttp_client,
            "homeassistant.helpers.config_validation": fake_config_validation,
            "homeassistant.helpers.device_registry": fake_devices,
            "homeassistant.helpers.entity_registry": fake_entities,
            "homeassistant.helpers.entity_platform": fake_entity_platform,
            "homeassistant.helpers.selector": fake_selector,
            "homeassistant.helpers.update_coordinator": fake_update_coordinator,
        }
    )

if "voluptuous" not in sys.modules and importlib.util.find_spec("voluptuous") is None:
    fake_voluptuous = types.ModuleType("voluptuous")
    fake_voluptuous.Schema = lambda value: value
    fake_voluptuous.Required = lambda value, **kwargs: value
    fake_voluptuous.Optional = lambda value, **kwargs: value
    fake_voluptuous.Exclusive = lambda value, group, **kwargs: value
    sys.modules["voluptuous"] = fake_voluptuous


_install_homeassistant_stubs()
_COORDINATOR = importlib.import_module(f"{_PACKAGE_NAME}.coordinator")
KeeneticCoordinator = _COORDINATOR.KeeneticCoordinator
_SENSOR = importlib.import_module(f"{_PACKAGE_NAME}.sensor")
_BINARY_SENSOR = importlib.import_module(f"{_PACKAGE_NAME}.binary_sensor")
_CONFIG_FLOW = importlib.import_module(f"{_PACKAGE_NAME}.config_flow")


class CoordinatorInterfaceIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_id_keyed_sms_emits_once_after_initial_snapshot(self):
        events = []
        coordinator = object.__new__(KeeneticCoordinator)
        coordinator.hass = SimpleNamespace(bus=SimpleNamespace(
            async_fire=lambda event, payload: events.append((event, payload))))
        coordinator.entry = SimpleNamespace(entry_id="entry-1")
        coordinator._sms_initialized = set()
        coordinator._sms_fingerprints = {}
        first = {"messages": {"sim-1": {"read": False, "parts": 1,
                                          "total-parts": 1, "text": "old"}}}
        new = {"messages": {**first["messages"], "sim-2": {"read": False,
                 "parts": 1, "total-parts": 1, "text": "new"}}}
        coordinator._process_sms("UsbLte0", first)
        coordinator._process_sms("UsbLte0", new)
        coordinator._process_sms("UsbLte0", new)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][1]["sms_id"], "sim-2")

    async def test_sms_capability_survives_transient_poll_error(self):
        coordinator = object.__new__(KeeneticCoordinator)
        coordinator._sms_capable = set()
        coordinator._sms_initialized = set()
        coordinator._sms_fingerprints = {}
        coordinator.hass = SimpleNamespace(bus=SimpleNamespace(async_fire=lambda *_args: None))
        responses = iter([{"messages": {}}, _COORDINATOR.RciCommandError("unsupported")])

        async def modem_optional(endpoint, _method, _params):
            if endpoint == "sms/list":
                response = next(responses)
                if isinstance(response, Exception):
                    raise response
                return response
            return None

        coordinator._modem_optional = modem_optional
        interface = {"id": "UsbLte0", "type": "UsbLte", "state": "up"}
        first = await coordinator._poll_modems([interface], {"UsbLte0"})
        second = await coordinator._poll_modems([interface], {"UsbLte0"})
        self.assertTrue(first["UsbLte0"]["sms_available"])
        self.assertTrue(second["UsbLte0"]["sms_available"])
        self.assertNotIn("sms", second["UsbLte0"])

    async def test_unsupported_cdc_modem_has_no_sms_capability(self):
        coordinator = object.__new__(KeeneticCoordinator)
        coordinator._sms_capable = set()

        async def modem_optional(endpoint, _method, _params):
            if endpoint == "sms/list":
                raise _COORDINATOR.RciCommandError("SMS is not supported")
            return None

        coordinator._modem_optional = modem_optional
        modem = {"id": "CdcEthernet0", "type": "CdcEthernet", "state": "down"}
        result = await coordinator._poll_modems([modem], {"CdcEthernet0"})
        self.assertIs(result["CdcEthernet0"]["sms_available"], False)
        self.assertNotIn("sms", result["CdcEthernet0"])

    async def test_unread_sms_sensor_counts_only_confirmed_records(self):
        entry = SimpleNamespace(unique_id="router")
        good = {"modems": {"UsbLte0": {"interface_info": {}, "sms_available": True,
                "sms": {"messages": {"sim-1": {"read": False},
                                      "sim-2": {"read": True}}}}}}
        spec = next(spec for spec in _SENSOR._modem_specs(entry, good)
                    if spec.translation_key == "unread_sms")
        self.assertEqual(spec.value(good), 1)
        good["modems"]["UsbLte0"].pop("sms")
        self.assertIsNone(spec.value(good))
        unsupported = {"modems": {"CdcEthernet0": {"interface_info": {},
                                            "sms_available": False}}}
        self.assertNotIn("unread_sms", {spec.translation_key for spec in
                         _SENSOR._modem_specs(entry, unsupported)})

    async def test_initial_refresh_does_not_wait_for_optional_ipsec_log(self):
        coordinator = object.__new__(KeeneticCoordinator)
        coordinator.hass = SimpleNamespace(loop=asyncio.get_running_loop())
        coordinator.entry = SimpleNamespace(
            entry_id="entry-1", unique_id="router", options={}
        )
        coordinator.api = SimpleNamespace()
        coordinator._version_id = None
        coordinator.data = None
        coordinator._optional_until = {}
        coordinator._modem_optional_until = {}
        coordinator._ipsec_diagnostics_until = 0.0
        coordinator._ipsec_diagnostics_summary = None
        coordinator._client_seen = {}
        coordinator._client_seen_store = None
        coordinator._wan_initialized = False
        coordinator._previous_default_interface = None
        coordinator._sms_initialized = set()
        coordinator._sms_fingerprints = {}
        coordinator._sms_capable = set()

        async def api_get(endpoint):
            if endpoint == "show/version":
                return {
                    "title": "KeeneticOS",
                    "release": "5.1.7",
                    "ndw": {"components": ["ipsec"]},
                }
            if endpoint == "show/system":
                return {}
            raise AssertionError(endpoint)

        async def optional(_endpoint, _key, _params=None):
            return None

        async def unexpected_ipsec_poll(_version):
            raise AssertionError("optional IPsec log must not block first refresh")

        coordinator.api.async_get = api_get
        coordinator._optional = optional
        coordinator._poll_ipsec_diagnostics = unexpected_ipsec_poll

        with patch.object(_COORDINATOR, "cleanup_dynamic_registry", return_value=set()):
            data = await coordinator._async_update_data()

        self.assertIsNone(data["ipsec_diagnostics"])

    async def test_stats_and_modem_requests_use_router_interface_id(self):
        coordinator = object.__new__(KeeneticCoordinator)
        coordinator._sms_capable = set()
        calls = []

        async def optional(endpoint, key, params=None):
            calls.append((endpoint, key, params))
            return {"statistic": {"rxbytes": 7}}

        async def modem_optional(endpoint, method, params):
            calls.append((endpoint, method, params))
            return None

        coordinator._optional = optional
        coordinator._modem_optional = modem_optional
        interface = {
            "id": "UsbLte0",
            "interface-name": "Friendly LTE label",
            "description": "Cellular modem",
            "type": "USB",
            "connected": "yes",
        }

        stats = await coordinator._poll_interface_stats([interface], {"UsbLte0"})
        modems = await coordinator._poll_modems([interface], {"UsbLte0"})

        self.assertEqual(stats[0]["statistic"], {"rxbytes": 7})
        self.assertIn("UsbLte0", modems)
        self.assertEqual(modems["UsbLte0"]["interface_info"]["interface-name"], "Friendly LTE label")
        self.assertEqual(calls[0][2], {"name": "UsbLte0"})
        self.assertTrue(all(call[2].get("name", call[2].get("interface")) == "UsbLte0" for call in calls[1:]))
        self.assertIn(("sms/list", "GET", {"interface": "UsbLte0"}), calls)

    async def test_stats_skip_interfaces_outside_entity_policy(self):
        coordinator = object.__new__(KeeneticCoordinator)
        calls = []

        async def optional(endpoint, key, params=None):
            calls.append((endpoint, key, params))
            return {"statistic": {"rxbytes": 7}}

        coordinator._optional = optional
        interfaces = [
            {"id": "WifiMaster0", "type": "WifiMaster", "connected": "yes"},
            {"id": "GigabitEthernet0", "type": "GigabitEthernet", "connected": "yes"},
            {"id": "ISP", "type": "PPPoE", "connected": "yes"},
        ]

        stats = await coordinator._poll_interface_stats(interfaces, {"ISP"})

        self.assertEqual(calls, [("show/interface/stat", "interface_stats_ISP", {"name": "ISP"})])
        self.assertNotIn("statistic", stats[0])
        self.assertNotIn("statistic", stats[1])
        self.assertEqual(stats[2]["statistic"], {"rxbytes": 7})

    def test_interface_identity_ignores_friendly_name_and_rejects_invalid_id(self):
        self.assertEqual(
            KeeneticCoordinator._interface_id(
                {"id": "Wireguard0", "interface-name": "Friendly VPN"}
            ),
            "Wireguard0",
        )
        self.assertIsNone(KeeneticCoordinator._interface_id({"interface-name": "Friendly VPN"}))
        self.assertIsNone(KeeneticCoordinator._interface_id({"id": True}))


class WifiTemperatureEntitySpecTests(unittest.TestCase):
    def test_router_exposes_unique_gigabit_port_status_sensors(self):
        entry = SimpleNamespace(unique_id="router")
        data = {
            "interface_records": [
                {
                    "id": "GigabitEthernet0",
                    "type": "GigabitEthernet",
                    "port": {
                        "1": {
                            "id": "GigabitEthernet0/0",
                            "type": "Port",
                            "label": "1",
                            "link": "down",
                        },
                        "2": {
                            "id": "GigabitEthernet0/1",
                            "type": "Port",
                            "label": "2",
                            "link": "up",
                            "speed": "1000",
                            "duplex": "full",
                        },
                    },
                },
                {
                    "id": "GigabitEthernet0/Vlan1",
                    "type": "Vlan",
                    "link": "up",
                },
                {"id": "Ethernet0/0", "type": "Port", "link": "up"},
                # The live response also exposes these records at the top level.
                {
                    "id": "GigabitEthernet0/1",
                    "type": "Port",
                    "label": "2",
                    "link": "up",
                    "speed": "1000",
                    "duplex": "full",
                },
            ],
            "routes": [],
        }

        specs = [
            spec
            for spec in _SENSOR._root_specs(entry, data)
            if spec.translation_key == "port_status"
        ]

        self.assertEqual(
            [spec.unique_id for spec in specs],
            [
                "router_port_GigabitEthernet0/0",
                "router_port_GigabitEthernet0/1",
            ],
        )
        self.assertEqual(specs[0].translation_placeholders, {"port": "1"})
        self.assertEqual(specs[0].value(data), "down")
        self.assertEqual(specs[1].value(data), "up")
        self.assertEqual(specs[1].attributes(data), {"speed": 1000, "duplex": "full"})
        self.assertEqual(specs[1].entity_category, "diagnostic")

        missing = {"interface_records": [], "routes": []}
        self.assertIsNone(specs[1].value(missing))

    def test_router_temperature_uses_one_observed_wifi_master(self):
        entry = SimpleNamespace(unique_id="router")
        data = {
            "interface_records": [
                {"id": "WifiMaster0", "type": "WifiMaster", "temperature": "37"}
            ],
            "routes": [],
        }

        specs = _SENSOR._root_specs(entry, data)
        temperature = next(spec for spec in specs if spec.unique_id.endswith("_temperature"))

        self.assertEqual(temperature.value(data), 37)
        self.assertEqual(temperature.unit, "°C")
        self.assertEqual(temperature.device_class, "temperature")
        self.assertEqual(temperature.state_class, "measurement")
        self.assertEqual(temperature.translation_key, "router_temperature")

    def test_root_specs_expose_semantic_wan_rates_without_totals(self):
        entry = SimpleNamespace(unique_id="router")
        data = {
            "routes": [
                {"destination": "0.0.0.0/0", "interface": "ISP", "metric": 1}
            ],
            "interface_records": [
                {
                    "id": "Wan0",
                    "interface-name": "ISP",
                    "type": "PPPoE",
                    "connected": "yes",
                    "statistic": {"rxbytes": 100, "txbytes": 200},
                },
                {
                    "id": "WifiMaster0",
                    "type": "WifiMaster",
                    "connected": "yes",
                    "statistic": {"rxbytes": 999, "txbytes": 999},
                },
            ],
        }

        specs = _SENSOR._root_specs(entry, data)
        rate_specs = [spec for spec in specs if spec.rate_only]

        self.assertEqual(
            {spec.translation_key for spec in rate_specs},
            {"wan_received_rate", "wan_sent_rate"},
        )
        self.assertEqual(
            {spec.rate_counter(data).source for spec in rate_specs},
            {"Wan0"},
        )
        self.assertNotIn("rxbytes", {spec.unique_id.rsplit("_", 1)[-1] for spec in specs})

    def test_uptime_keeps_seconds_and_suggests_days(self):
        entry = SimpleNamespace(unique_id="router")
        observed_at = datetime.datetime(2026, 10, 29, 12, tzinfo=datetime.timezone.utc)
        data = {
            "observed_at": observed_at,
            "system": {"uptime": "190800"},
            "interface_records": [],
            "routes": [],
        }

        uptime = next(
            spec for spec in _SENSOR._root_specs(entry, data) if spec.translation_key == "uptime"
        )

        self.assertEqual(uptime.value(data), 190800)
        self.assertEqual(uptime.unit, "s")
        self.assertEqual(uptime.suggested_unit, "d")
        self.assertEqual(uptime.device_class, "duration")
        self.assertEqual(uptime.entity_category, "diagnostic")
        self.assertEqual(
            uptime.attributes(data)["last_boot"],
            observed_at - datetime.timedelta(seconds=190800),
        )

    def test_mesh_uptime_and_memory_match_router_presentation(self):
        entry = SimpleNamespace(unique_id="router")
        data = {
            "mesh": {
                "member": [
                    {
                        "mac": "aa:bb:cc:dd:ee:ff",
                        "system": {"uptime": "190800", "memory": "111004/262144"},
                    }
                ]
            }
        }

        specs = _SENSOR._mesh_specs(entry, data)
        uptime = next(spec for spec in specs if spec.translation_key == "mesh_uptime")
        memory = next(spec for spec in specs if spec.translation_key == "memory_used")

        self.assertEqual(uptime.value(data), 190800)
        self.assertEqual(uptime.suggested_unit, "d")
        self.assertEqual(memory.value(data), 42.3)

    def test_child_traffic_uses_bytes_with_megabyte_display_suggestion(self):
        entry = SimpleNamespace(unique_id="router")
        data = {
            "interface_records": [
                {
                    "id": "Wireguard0",
                    "type": "Wireguard",
                    "state": "up",
                    "connected": "yes",
                    "statistic": {"rxbytes": 1000, "txbytes": 2000},
                }
            ],
            "routes": [],
        }

        counters = [
            spec
            for spec in _SENSOR._interface_specs(entry, data)
            if spec.device_class == "data_size"
        ]

        self.assertEqual(len(counters), 2)
        self.assertTrue(all(spec.unit == "B" for spec in counters))
        self.assertTrue(all(spec.suggested_unit == "MB" for spec in counters))

    def test_selected_client_specs_cover_expanded_card_and_remain_static(self):
        entry = SimpleNamespace(
            unique_id="router",
            options={
                "clients": ["00:11:22:33:44:55"],
                "policy_clients": ["00:11:22:33:44:66"],
            },
        )
        data = {
            "hotspot": {
                "host": [
                    {
                        "mac": "00:11:22:33:44:55",
                        "name": "Phone",
                        "interface": {"id": "WifiMaster0/AccessPoint0"},
                        "registered": "yes",
                        "link": "up",
                        "rxbytes": 100,
                        "txbytes": 200,
                    }
                ]
            },
            "client_seen": {
                "00:11:22:33:44:55": {
                    "first_seen": "2026-10-29T10:00:00+00:00",
                    "last_seen": "2026-10-29T10:01:00+00:00",
                }
            },
        }

        sensor_specs = _SENSOR._client_specs(entry, data)
        binary_specs = _BINARY_SENSOR._client_specs(entry, data)

        self.assertEqual(len(sensor_specs), 2 * 13)
        self.assertEqual(len(binary_specs), 4)
        self.assertTrue(all(spec.translation_placeholders is None for spec in sensor_specs))
        self.assertTrue(all(spec.translation_placeholders is None for spec in binary_specs))
        self.assertEqual(
            {spec.translation_key for spec in sensor_specs},
            {
                "client_connection_type",
                "client_ip",
                "client_port",
                "client_wifi_band",
                "client_wifi_mode",
                "client_first_seen",
                "client_last_seen",
                "client_link_speed",
                "client_rssi",
                "client_uptime",
                "client_received_data",
                "client_sent_data",
                "client_tx_rate",
            },
        )
        self.assertEqual(
            next(spec for spec in sensor_specs if spec.translation_key == "client_received_data").suggested_unit,
            "MB",
        )
        self.assertEqual(
            next(spec for spec in sensor_specs if spec.translation_key == "client_first_seen").value(data).isoformat(),
            "2026-10-29T10:00:00+00:00",
        )
        self.assertEqual(
            {spec.translation_key for spec in binary_specs},
            {"client_dhcp_registered", "client_link_status"},
        )
        self.assertTrue(
            next(spec for spec in binary_specs if spec.translation_key == "client_link_status").value(data)
        )
        offline = {
            **data,
            "hotspot": {"host": [{"mac": "00:11:22:33:44:55", "active": "no", "link": "down"}]},
        }
        rx = next(spec for spec in sensor_specs if spec.translation_key == "client_received_data")
        first_seen = next(spec for spec in sensor_specs if spec.translation_key == "client_first_seen")
        self.assertIsNone(rx.value(offline))
        self.assertIsNotNone(first_seen.value(offline))

    def test_mesh_backhaul_speed_is_not_created_without_numeric_source(self):
        entry = SimpleNamespace(unique_id="router")
        data = {
            "mesh": {"member": [{"mac": "aa:bb:cc:dd:ee:ff", "backhaul": {}}]}
        }

        specs = _SENSOR._mesh_specs(entry, data)

        self.assertNotIn("mesh_backhaul_speed", {spec.translation_key for spec in specs})

    def test_disconnected_modem_still_has_a_registered_connected_entity(self):
        entry = SimpleNamespace(unique_id="router")
        data = {
            "modems": {
                "UsbLte1": {
                    "interface_info": {
                        "id": "UsbLte1",
                        "type": "UsbLte",
                        "state": "down",
                    }
                }
            }
        }

        specs = _BINARY_SENSOR._modem_specs(entry, data)

        self.assertIn("modem_connected", {spec.translation_key for spec in specs})
        connected = next(spec for spec in specs if spec.translation_key == "modem_connected")
        self.assertIsNone(connected.value(data))

    def test_rate_rebaselines_on_source_change_or_counter_reset(self):
        sample = _SENSOR.CounterSample

        self.assertEqual(
            _SENSOR._rate_from_samples(sample("Wan0", 100), sample("Wan0", 300), 2),
            0.0008,
        )
        self.assertIsNone(
            _SENSOR._rate_from_samples(sample("Wan0", 100), sample("Wan1", 300), 2)
        )
        self.assertIsNone(
            _SENSOR._rate_from_samples(sample("Wan0", 300), sample("Wan0", 100), 2)
        )


class DiagnosticEntitySpecTests(unittest.TestCase):
    def test_rsrq_and_sinr_use_signal_strength_device_class(self):
        entry = SimpleNamespace(unique_id="router")
        data = {
            "modems": {
                "UsbLte0": {
                    "interface_info": {
                        "id": "UsbLte0",
                        "type": "UsbLte",
                        "rsrq": -12,
                        "cinr": 14,
                    }
                }
            }
        }

        specs = {
            spec.translation_key: spec
            for spec in _SENSOR._modem_specs(entry, data)
        }

        self.assertEqual(specs["modem_rsrq"].device_class, "signal_strength")
        self.assertEqual(specs["modem_sinr"].device_class, "signal_strength")

    def test_reference_diagnostic_sensors_use_sanitized_coordinator_summaries(self):
        entry = SimpleNamespace(unique_id="router")
        data = {
            "dns_proxy": {"status": "ok", "failed_requests": 4},
            "ipsec_diagnostics": {"status": "warning", "out_of_memory_count": 2},
        }

        specs = _SENSOR._diagnostic_specs(entry, data)

        self.assertEqual(
            {spec.translation_key for spec in specs},
            {
                "dns_proxy_status",
                "dns_proxy_failed_requests",
                "ipsec_vici_status",
                "ipsec_vici_out_of_memory",
            },
        )
        self.assertEqual(
            {spec.translation_key: spec.value(data) for spec in specs},
            {
                "dns_proxy_status": "ok",
                "dns_proxy_failed_requests": 4,
                "ipsec_vici_status": "warning",
                "ipsec_vici_out_of_memory": 2,
            },
        )

    def test_usb_storage_state_uses_mounted_live_media(self):
        data = {
            "usb": {
                "device": {
                    "0": {"subsystem": "media"},
                    "1": {"subsystem": "network"},
                }
            },
            "media": {
                "Media0": {
                    "state": "ACTIVE",
                    "partition": [
                        {"state": "MOUNTED", "total": "1000", "free": "400"},
                    ],
                }
            },
        }

        self.assertTrue(_BINARY_SENSOR._usb_storage_detected(data))
        self.assertEqual(
            _BINARY_SENSOR._usb_storage_attributes(data),
            {
                "storage_device_count": 1,
                "media_count": 1,
                "volume_count": 1,
                "mounted_volume_count": 1,
                "mounted_total_bytes": 1000,
                "mounted_free_bytes": 400,
            },
        )

    def test_usb_storage_state_requires_a_known_storage_device(self):
        media = {
            "Media0": {
                "state": "ACTIVE",
                "partition": [{"state": "MOUNTED", "total": "1000", "free": "400"}],
            }
        }

        self.assertFalse(
            _BINARY_SENSOR._usb_storage_detected(
                {"usb": {"device": []}, "media": media}
            )
        )
        self.assertIsNone(_BINARY_SENSOR._usb_storage_detected({"media": media}))


class EntityPolicySpecTests(unittest.TestCase):
    def test_raw_internal_interfaces_do_not_create_entities(self):
        entry = SimpleNamespace(unique_id="router")
        data = {
            "routes": [
                {"destination": "0.0.0.0/0", "interface": "ISP", "metric": 1}
            ],
            "interface_records": [
                {"id": "WifiMaster0", "type": "WifiMaster", "connected": "yes"},
                {
                    "id": "GigabitEthernet0",
                    "type": "GigabitEthernet",
                    "connected": "yes",
                },
                {"id": "ISP", "type": "PPPoE", "connected": "yes"},
                {
                    "id": "UsbLte0",
                    "type": "USB",
                    "connected": "yes",
                    "statistic": {"rxbytes": 1},
                },
                {
                    "id": "Wireguard0",
                    "type": "Wireguard",
                    "connected": "yes",
                    "statistic": {"txbytes": 2},
                },
            ],
        }

        sensor_ids = {
            spec.unique_id for spec in _SENSOR._interface_specs(entry, data)
        }
        binary_ids = {
            spec.unique_id for spec in _BINARY_SENSOR._interface_specs(entry, data)
        }

        self.assertTrue(sensor_ids)
        self.assertTrue(all("_interface_" not in unique_id for unique_id in sensor_ids))
        self.assertTrue(
            all(
                "WifiMaster" not in unique_id and "GigabitEthernet" not in unique_id
                for unique_id in sensor_ids | binary_ids
            )
        )
        self.assertEqual(binary_ids, {"router_vpn_Wireguard0_connected"})


class WifiOptionsFlowTests(unittest.IsolatedAsyncioTestCase):
    def test_manual_client_macs_are_normalized_and_invalid_values_rejected(self):
        self.assertEqual(
            _CONFIG_FLOW._normalized_mac_values(
                ["AA-BB-CC-DD-EE-FF", "aa:bb:cc:dd:ee:ff"]
            ),
            ["aa:bb:cc:dd:ee:ff"],
        )
        with self.assertRaises(ValueError):
            _CONFIG_FLOW._normalized_mac_values(["not-a-mac"])

    def test_access_point_options_use_ssid_and_friendly_name(self):
        rows = _CONFIG_FLOW._wifi_access_points(
            {
                "interfaces": {
                    "interface": {
                        "WifiMaster0/AccessPoint0": {
                            "id": "WifiMaster0/AccessPoint0",
                            "type": "AccessPoint",
                            "state": "up",
                            "ssid": "Home Wi-Fi",
                        },
                        "WifiMaster0/AccessPoint1": {
                            "id": "WifiMaster0/AccessPoint1",
                            "type": "AccessPoint",
                            "state": "down",
                            "interface-name": "GuestWiFi",
                        },
                    }
                }
            }
        )

        self.assertEqual(
            rows,
            [
                {"id": "WifiMaster0/AccessPoint0", "label": "Home Wi-Fi", "enabled": True},
                {"id": "WifiMaster0/AccessPoint1", "label": "GuestWiFi", "enabled": False},
            ],
        )

    async def test_saving_wifi_options_only_posts_changed_access_points(self):
        calls = []

        async def async_call(endpoint, data):
            calls.append((endpoint, data))

        entry = SimpleNamespace(
            entry_id="entry",
            options={"wifi_enabled_access_points": ["WifiMaster0/AccessPoint0"]},
        )
        flow = _CONFIG_FLOW.KeeneticOptionsFlow()
        flow.config_entry = entry
        flow.hass = SimpleNamespace(
            data={
                "keenetic_control": {
                    "entry": SimpleNamespace(coordinator=SimpleNamespace(async_call=async_call))
                }
            }
        )
        rows = [
            {"id": "WifiMaster0/AccessPoint0", "label": "Home Wi-Fi", "enabled": True},
            {"id": "WifiMaster0/AccessPoint1", "label": "GuestWiFi", "enabled": True},
            {"id": "WifiMaster1/AccessPoint0", "label": "Home Wi-Fi 5G", "enabled": False},
        ]

        await flow._async_apply_wifi_access_points(
            {"WifiMaster0/AccessPoint0", "WifiMaster1/AccessPoint0"}, rows
        )

        self.assertEqual(
            calls,
            [
                ("interface/down", {"name": "WifiMaster0/AccessPoint1"}),
                ("interface/up", {"name": "WifiMaster1/AccessPoint0"}),
            ],
        )


class WireguardPeerEntitySpecTests(unittest.TestCase):
    def test_specs_are_created_once_per_peer_and_bound_to_each_interface(self):
        model = importlib.import_module(f"{_PACKAGE_NAME}.model")
        interfaces = {
            "interface": [
                {
                    "id": "Wireguard0",
                    "type": "Wireguard",
                    "description": "Home VPN",
                    "wireguard": {
                        "peer": [
                            {
                                "public-key": "private-to-parser-phone-key",
                                "description": "Phone",
                                "online": "yes",
                                "rxbytes": 5,
                                "txbytes": 8,
                            }
                        ]
                    },
                },
                {
                    "id": "Wireguard1",
                    "type": "Wireguard",
                    "description": "Work VPN",
                    "wireguard": {
                        "peer": [
                            {
                                "public-key": "private-to-parser-laptop-key",
                                "description": "Laptop",
                                "online": "no",
                                "rxbytes": 2,
                                "txbytes": 3,
                            }
                        ]
                    },
                },
            ]
        }
        inventories = model.parse_wireguard_interface_peers(interfaces)
        data = {"wireguard_peer_inventories": inventories}
        entry = SimpleNamespace(unique_id="router", entry_id="entry")

        sensor_specs = _SENSOR._wireguard_peer_specs(entry, data)
        binary_specs = _BINARY_SENSOR._wireguard_peer_specs(entry, data)

        self.assertEqual(len(sensor_specs), 8)
        self.assertEqual(len({spec.unique_id for spec in sensor_specs}), 8)
        self.assertEqual(len(binary_specs), 2)
        self.assertEqual(len({spec.unique_id for spec in binary_specs}), 2)
        self.assertEqual(
            {spec.translation_placeholders["peer"] for spec in sensor_specs}, {"Phone", "Laptop"}
        )
        self.assertEqual(
            {spec.translation_placeholders["peer"] for spec in binary_specs}, {"Phone", "Laptop"}
        )
        self.assertEqual({spec.device_name for spec in sensor_specs}, {"Home VPN", "Work VPN"})
        self.assertEqual({spec.device_name for spec in binary_specs}, {"Home VPN", "Work VPN"})
        self.assertEqual({spec.value(data) for spec in binary_specs}, {True, False})
        self.assertNotIn("private-to-parser", str([spec.unique_id for spec in sensor_specs]))
        counter_specs = [spec for spec in sensor_specs if spec.device_class == "data_size"]
        handshake_specs = [
            spec for spec in sensor_specs if spec.translation_key == "wireguard_peer_handshake"
        ]
        self.assertTrue(all(spec.suggested_unit == "MB" for spec in counter_specs))
        self.assertTrue(all(spec.suggested_precision == 0 for spec in handshake_specs))


if __name__ == "__main__":
    unittest.main()
