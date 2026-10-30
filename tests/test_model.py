import unittest
import importlib.util
import sys
from pathlib import Path

_MODEL_PATH = Path(__file__).parents[1] / "custom_components" / "keenetic_control" / "model.py"
_MODEL_SPEC = importlib.util.spec_from_file_location("keenetic_model", _MODEL_PATH)
assert _MODEL_SPEC is not None and _MODEL_SPEC.loader is not None
_MODEL = importlib.util.module_from_spec(_MODEL_SPEC)
sys.modules[_MODEL_SPEC.name] = _MODEL
_MODEL_SPEC.loader.exec_module(_MODEL)

(
    complete_sms,
    default_route,
    has_sms_service,
    is_known_vpn_interface_type,
    mesh_member_id,
    normalize_interface_records,
    normalize_records,
    parse_internet_status,
    parse_interface_fields,
    parse_mesh_member,
    parse_modem_interface_fields,
    parse_hotspot_client,
    parse_ping_checks,
    parse_usb_storage_devices,
    parse_memory_used_percent,
    parse_modem_cells,
    parse_number,
    parse_sms_message,
    parse_sms_list_response,
    parse_sms_messages,
    parse_stat_counters,
    parse_system_metrics,
    parse_wireguard_interface_peers,
    parse_wireguard_server_response,
    merge_wireguard_peer_sources,
    parse_usb_media_status,
    parse_version,
    parse_yes_no,
    parse_dns_proxy_diagnostics,
    parse_ipsec_vici_diagnostics,
    parse_lte_data_usage,
    parse_traffic_shape,
    wireguard_peer_id,
    memory_percent,
    select_default_route,
    select_modem_signal,
    select_modem_signal_reading,
    sms_fingerprint,
    wifi_client_count,
) = tuple(getattr(_MODEL, name) for name in (
    "complete_sms",
    "default_route",
    "has_sms_service",
    "is_known_vpn_interface_type",
    "mesh_member_id",
    "normalize_interface_records",
    "normalize_records",
    "parse_internet_status",
    "parse_interface_fields",
    "parse_mesh_member",
    "parse_modem_interface_fields",
    "parse_hotspot_client",
    "parse_ping_checks",
    "parse_usb_storage_devices",
    "parse_memory_used_percent",
    "parse_modem_cells",
    "parse_number",
    "parse_sms_message",
    "parse_sms_list_response",
    "parse_sms_messages",
    "parse_stat_counters",
    "parse_system_metrics",
    "parse_wireguard_interface_peers",
    "parse_wireguard_server_response",
    "merge_wireguard_peer_sources",
    "parse_usb_media_status",
    "parse_version",
    "parse_yes_no",
    "parse_dns_proxy_diagnostics",
    "parse_ipsec_vici_diagnostics",
    "parse_lte_data_usage",
    "parse_traffic_shape",
    "wireguard_peer_id",
    "memory_percent",
    "select_default_route",
    "select_modem_signal",
    "select_modem_signal_reading",
    "sms_fingerprint",
    "wifi_client_count",
))


class YesNoTests(unittest.TestCase):
    def test_yes_no_and_json_booleans_are_known(self):
        self.assertIs(parse_yes_no("yes"), True)
        self.assertIs(parse_yes_no(" NO "), False)
        self.assertIs(parse_yes_no(True), True)
        self.assertIs(parse_yes_no(False), False)
        for value in (None, 1, "true", "false", "unknown", ""):
            with self.subTest(value=value):
                self.assertIsNone(parse_yes_no(value))


class NumberTests(unittest.TestCase):
    def test_safe_numeric_values(self):
        self.assertEqual(parse_number(" 42 "), 42)
        self.assertEqual(parse_number("1.25"), 1.25)
        self.assertEqual(parse_number(0), 0)
        self.assertEqual(parse_number(2.5), 2.5)

    def test_rejects_invalid_and_non_finite_values(self):
        for value in (None, True, False, "", "  ", "abc", "NaN", "inf", float("nan"), float("inf")):
            with self.subTest(value=value):
                self.assertIsNone(parse_number(value))


class DiagnosticParserTests(unittest.TestCase):
    def test_dns_proxy_diagnostics_accepts_single_or_multiple_upstreams(self):
        self.assertEqual(
            parse_dns_proxy_diagnostics(
                {
                    "proxy": {"type": "dns", "active": True},
                    "statistics": {"requests-failed": "3"},
                }
            ),
            {"status": "ok", "failed_requests": 3},
        )
        self.assertEqual(
            parse_dns_proxy_diagnostics(
                {"proxies": [{"active": False}], "stats": {"failed": 0}}
            ),
            {"status": "degraded", "failed_requests": 0},
        )
        self.assertIsNone(parse_dns_proxy_diagnostics({"statistics": {}}))

    def test_ipsec_diagnostics_counts_only_the_reference_marker(self):
        summary = parse_ipsec_vici_diagnostics(
            {
                "message": [
                    "ordinary log entry",
                    "IpSec::Vici::Stats: out of memory",
                    "ipsec::vici::stats: OUT OF MEMORY",
                ]
            }
        )

        self.assertEqual(summary, {"status": "warning", "out_of_memory_count": 2})
        self.assertNotIn("ordinary log entry", str(summary))
        self.assertIsNone(parse_ipsec_vici_diagnostics({"unrelated": "text"}))

    def test_lte_data_usage_converts_only_enabled_known_units(self):
        self.assertEqual(
            parse_lte_data_usage(
                {
                    "enabled": True,
                    "unit": "MB",
                    "value": "2048",
                    "remaining": "1024",
                    "limit": "4096",
                    "threshold": "3072",
                    "days-left": 5,
                    "trigger": {"limit": False, "threshold": "yes"},
                }
            ),
            {
                "used_gb": 2,
                "remaining_gb": 1,
                "limit_gb": 4,
                "threshold_gb": 3,
                "days_left": 5,
                "usage_percent": 50,
                "limit_exceeded": False,
                "threshold_exceeded": True,
            },
        )
        self.assertIsNone(parse_lte_data_usage({"enabled": False, "unit": "GB"}))
        self.assertIsNone(parse_lte_data_usage({"enabled": True, "unit": "blocks"}))

    def test_memory_percent_uses_used_over_total(self):
        self.assertEqual(memory_percent("97592/262144"), 37.2)
        self.assertEqual(parse_memory_used_percent("97592/262144"), 37.2)
        for value in (None, "0/0", "262145/262144", "-1/10", "10/x", "1/2/3"):
            with self.subTest(value=value):
                self.assertIsNone(memory_percent(value))


class InterfaceInventoryTests(unittest.TestCase):
    def test_normalizes_keyed_interface_mapping(self):
        interfaces = {
            "WifiMaster0": {
                "id": "WifiMaster0",
                "type": "WifiMaster",
                "channel": 5,
                "temperature": 54,
            },
            "UsbLte0": {"id": "UsbLte0", "type": "UsbLte", "cinr": "25"},
        }

        self.assertEqual(normalize_interface_records(interfaces), list(interfaces.values()))
        self.assertEqual(
            normalize_interface_records({"interface": interfaces}),
            list(interfaces.values()),
        )

    def test_malformed_or_ambiguous_interface_mappings_are_unknown(self):
        self.assertIsNone(normalize_interface_records({}))
        self.assertIsNone(normalize_interface_records({"UsbLte0": {"type": "UsbLte"}}))
        self.assertIsNone(
            normalize_interface_records(
                [
                    {"id": "WifiMaster0", "type": "WifiMaster"},
                    {"id": "WifiMaster0", "type": "WifiMaster"},
                ]
            )
        )


class ModemInterfaceTests(unittest.TestCase):
    def test_parses_observed_fields_without_assuming_units(self):
        parsed = parse_modem_interface_fields(
            {
                "manufacturer": "Vendor",
                "model": "Module",
                "fw": "1.0",
                "serial": "serial-placeholder",
                "imei": "imei-placeholder",
                "imsi": "imsi-placeholder",
                "iccid": "iccid-placeholder",
                "plugged": "yes",
                "roaming": False,
                "rssi": "-58",
                "rsrp": "-84",
                "rsrq": "-6.5",
                "cinr": "25",
                "temperature": "50",
                "dl-freq": "2649800",
                "ul-freq": 2529800,
                "bandwidth": "20",
                "bssid": "198571808",
                "carrier": {
                    "1": {
                        "active": True,
                        "mobile": "4G",
                        "phy-cell-id": "159",
                        "earfcn": 3048,
                        "band": "7",
                        "dl-freq": 2649800,
                        "ul-freq": 2529800,
                        "bandwidth": "20",
                        "secret": "ignored",
                    }
                },
                "ati": {"manufacturer": "Vendor", "model": "Module", "revision": "A"},
                "unknown-secret": "ignored",
            }
        )

        self.assertEqual(parsed["rssi"], -58)
        self.assertEqual(parsed["rsrq"], -6.5)
        self.assertEqual(parsed["dl-freq"], 2649800)
        self.assertEqual(parsed["ul-freq"], 2529800)
        self.assertEqual(parsed["bandwidth"], 20)
        self.assertEqual(parsed["bssid"], "198571808")
        self.assertIs(parsed["plugged"], True)
        self.assertIs(parsed["roaming"], False)
        self.assertNotIn("unknown-secret", parsed)
        self.assertNotIn("secret", parsed["carrier"]["1"])
        self.assertEqual(parsed["ati"]["revision"], "A")

    def test_malformed_optional_values_stay_absent(self):
        parsed = parse_modem_interface_fields(
            {
                "rssi": "not-a-number",
                "temperature": True,
                "plugged": "unknown",
                "roaming": "unknown",
                "carrier": {"bad": "row"},
            }
        )

        self.assertNotIn("rssi", parsed)
        self.assertNotIn("temperature", parsed)
        self.assertNotIn("plugged", parsed)
        self.assertNotIn("roaming", parsed)
        self.assertNotIn("carrier", parsed)

class ModemSignalTests(unittest.TestCase):
    def test_sinr_is_preferred_to_cinr_even_when_cinr_is_higher(self):
        records = [
            {"channel": 0, "sinr": "-3.5", "cinr": "28"},
            {"channel": 1, "sinr": "7.25", "cinr": "30"},
        ]

        value, record = select_modem_signal(records, "sinr")

        self.assertEqual(value, 7.25)
        self.assertIs(record, records[1])

    def test_cinr_is_used_only_when_no_valid_sinr_is_reported(self):
        records = [
            {"channel": 0, "sinr": "unknown", "cinr": "-1.25"},
            {"channel": 1, "sinr": None, "cinr": "4.5"},
        ]

        value, record = select_modem_signal(records, "sinr")

        self.assertEqual(value, 4.5)
        self.assertIs(record, records[1])

    def test_missing_or_invalid_measurements_remain_unavailable(self):
        self.assertEqual(select_modem_signal([{"sinr": "bad", "cinr": True}], "sinr"), (None, None))

    def test_interface_sinr_is_preferred_to_antenna_cinr(self):
        records = [{"channel": 0, "cinr": "30"}]
        interface_info = {"sinr": "-1.5", "cinr": "28"}

        value, source = select_modem_signal_reading(records, interface_info, "sinr")

        self.assertEqual(value, -1.5)
        self.assertIs(source, interface_info)

    def test_interface_cinr_falls_back_when_sinr_is_absent(self):
        interface_info = {"cinr": "25.5", "rssi": "-58"}

        value, source = select_modem_signal_reading([], interface_info, "sinr")

        self.assertEqual(value, 25.5)
        self.assertIs(source, interface_info)

    def test_interface_signal_values_fall_back_after_antenna_data(self):
        interface_info = {"rssi": "-58"}

        value, source = select_modem_signal_reading([], interface_info, "rssi")

        self.assertEqual(value, -58)
        self.assertIs(source, interface_info)


class DocumentedResponseTests(unittest.TestCase):
    def test_known_vpn_protocol_types_are_case_insensitive_and_secure_stays_ambiguous(self):
        for value in ("OpenVPN", "PPTP", "SSTP", "Wireguard", "ZeroTier"):
            with self.subTest(value=value):
                self.assertTrue(is_known_vpn_interface_type(value))
        for value in ("Secure", "PppTunnel", "Ethernet", None, 3):
            with self.subTest(value=value):
                self.assertFalse(is_known_vpn_interface_type(value))

    def test_mesh_member_id_accepts_documented_cid_fallback(self):
        self.assertEqual(mesh_member_id({"cid": "member-123"}), "member-123")
        self.assertEqual(mesh_member_id({"mac": "aa:bb", "cid": "member-123"}), "aa:bb")
        self.assertEqual(mesh_member_id({"cid": "", "id": "member-456"}), "member-456")
        self.assertIsNone(mesh_member_id({"cid": True}))

    def test_modem_cells_parse_documented_identity_and_rssi(self):
        self.assertEqual(
            parse_modem_cells(
                {"cells": [{"phy-id": "fc", "rssi": "-71"}, {"phy-id": 387, "rssi": -72.5}]}
            ),
            [{"cell_id": "fc", "rssi": -71}, {"cell_id": "387", "rssi": -72.5}],
        )
        self.assertEqual(parse_modem_cells({"cells": []}), [])
        for value in (
            {"cells": [{"phy-id": "fc"}]},
            {"cells": [{"phy-id": "fc", "rssi": "unknown"}]},
            {"cells": [{"rssi": -71}]},
            {"station": []},
        ):
            with self.subTest(value=value):
                self.assertIsNone(parse_modem_cells(value))

    def test_usb_media_status_requires_documented_mount_state(self):
        self.assertEqual(
            parse_usb_media_status(
                {
                    "media": [
                        {
                            "state": "ACTIVE",
                            "partition": [
                                {"state": "MOUNTED", "total": "1000", "free": "400"},
                            ],
                        }
                    ]
                }
            ),
            {
                "available": True,
                "media_count": 1,
                "volume_count": 1,
                "mounted_volume_count": 1,
                "mounted_total_bytes": 1000,
                "mounted_free_bytes": 400,
            },
        )
        self.assertIsNone(parse_usb_media_status({"media": [{"state": "ACTIVE"}]}))
        self.assertIsNone(parse_usb_media_status({"state": "ACTIVE"}))
        self.assertIsNone(
            parse_usb_media_status(
                {"media": [{"state": "ACTIVE", "partition": [{"state": "UNKNOWN"}]}]}
            )
        )

    def test_usb_media_status_accepts_live_keyed_media_shape(self):
        self.assertEqual(
            parse_usb_media_status(
                {
                    "Media0": {
                        "state": "ACTIVE",
                        "partition": [
                            {"state": "MOUNTED", "total": "1942192128", "free": "1693671424"},
                        ],
                    }
                }
            ),
            {
                "available": True,
                "media_count": 1,
                "volume_count": 1,
                "mounted_volume_count": 1,
                "mounted_total_bytes": 1942192128,
                "mounted_free_bytes": 1693671424,
            },
        )

    def test_usb_media_empty_inventory_is_known_unavailable(self):
        self.assertEqual(
            parse_usb_media_status({"media": []}),
            {
                "available": False,
                "media_count": 0,
                "volume_count": 0,
                "mounted_volume_count": 0,
                "mounted_total_bytes": 0,
                "mounted_free_bytes": 0,
            },
        )

    def test_sms_list_requires_the_documented_messages_collection(self):
        self.assertEqual(parse_sms_list_response({"messages": []}), [])
        self.assertEqual(parse_sms_list_response({"messages-count": 0}), [])
        self.assertIsNone(parse_sms_list_response({"status": "ok"}))
        self.assertIsNone(parse_sms_list_response({"messages-count": False}))
        self.assertIsNone(parse_sms_list_response({"messages-count": 1, "messages": {}}))

    def test_only_confirmed_sms_capability_is_accepted(self):
        self.assertTrue(has_sms_service({"sms_available": True}))
        for details in ({"sms_available": False}, {"sms_available": "yes"}, {}):
            with self.subTest(details=details):
                self.assertFalse(has_sms_service(details))

    def test_hotspot_client_parses_only_documented_optional_values(self):
        self.assertEqual(
            parse_hotspot_client(
                {
                    "mac": "AA-BB-CC-DD-EE-FF",
                    "name": "Laptop",
                    "hostname": "fallback",
                    "ip": "192.0.2.12",
                    "interface": {"id": "WifiMaster0/AccessPoint0", "name": "Guest", "description": "Guest Wi-Fi"},
                    "uptime": "42",
                    "rxbytes": "2048",
                    "txbytes": 1024,
                    "link": {"rssi": "-62.5", "txrate": "866.7"},
                    "password": "ignored",
                }
            ),
            {
                "mac": "aa:bb:cc:dd:ee:ff",
                "name": "Laptop",
                "ip": "192.0.2.12",
                "interface": "Guest Wi-Fi",
                "interface_id": "WifiMaster0/AccessPoint0",
                "connection_type": "Wi-Fi",
                "active": None,
                "dhcp_registered": None,
                "link_status": None,
                "link_speed": 866.7,
                "uptime": 42,
                "rxbytes": 2048,
                "txbytes": 1024,
                "rssi": -62.5,
                "txrate": 866.7,
                "wifi_band": None,
                "wifi_mode": None,
            },
        )
        self.assertIsNone(parse_hotspot_client({"mac": "not-a-mac", "rxbytes": "-1"})["mac"])
        self.assertIsNone(parse_hotspot_client({"mac": "00:11:22:33:44:55", "rxbytes": "-1"})["rxbytes"])

    def test_wifi_client_count_excludes_wired_hosts_and_deduplicates(self):
        self.assertEqual(
            wifi_client_count(
                {
                    "host": [
                        {"mac": "00:11:22:33:44:55", "interface": "WifiMaster0/AccessPoint0"},
                        {"mac": "00:11:22:33:44:55", "interface": "WifiMaster0/AccessPoint0"},
                        {"mac": "00:11:22:33:44:66", "interface": "Bridge0"},
                        {"mac": "00:11:22:33:44:77", "interface": "GuestAP", "rssi": "-60"},
                    ]
                }
            ),
            2,
        )
        self.assertEqual(wifi_client_count({"host": []}), 0)
        self.assertEqual(wifi_client_count({"host": [{"mac": "00:11:22:33:44:55"}]}), 0)

    def test_client_diagnostics_and_equal_traffic_shape_are_normalized(self):
        client = parse_hotspot_client(
            {
                "mac": "00:11:22:33:44:55",
                "interface": {"id": "WifiMaster0/AccessPoint0"},
                "registered": "yes",
                "link": "up",
                "speed": "100",
                "txrate": "80",
                "band": "5 GHz",
                "mode": "ax",
            }
        )

        self.assertEqual(client["connection_type"], "Wi-Fi")
        self.assertEqual(client["interface_id"], "WifiMaster0/AccessPoint0")
        self.assertTrue(client["dhcp_registered"])
        self.assertEqual(client["link_status"], "up")
        self.assertEqual(client["link_speed"], 100)
        self.assertEqual(client["wifi_band"], "5 GHz")
        self.assertEqual(
            parse_traffic_shape(
                {
                    "host": [
                        {
                            "mac": "00:11:22:33:44:55",
                            "downstream-rate": 1000,
                            "upstream-rate": 1000,
                        }
                    ]
                }
            ),
            {"00:11:22:33:44:55": 1000},
        )
        self.assertEqual(
            parse_traffic_shape(
                {
                    "host": [
                        {
                            "mac": "00:11:22:33:44:55",
                            "downstream-rate": 1000,
                            "upstream-rate": 900,
                        }
                    ]
                }
            ),
            {},
        )
    def test_wifi_client_count_ignores_live_offline_rows_without_interface(self):
        self.assertEqual(
            wifi_client_count(
                {
                    "host": [
                        {
                            "mac": "00:11:22:33:44:55",
                            "active": False,
                            "link": "down",
                        },
                        {
                            "mac": "00:11:22:33:44:66",
                            "active": True,
                            "interface": {"id": "Bridge0", "name": "Home"},
                            "link": "up",
                            "rssi": -55,
                        },
                        {
                            "mac": "00:11:22:33:44:77",
                            "active": True,
                            "interface": {"id": "Bridge0", "name": "Home"},
                            "link": "up",
                        },
                    ]
                }
            ),
            1,
        )

    def test_normalize_records_distinguishes_empty_from_malformed(self):
        record = {"id": "sim-0"}
        self.assertEqual(normalize_records([record]), [record])
        self.assertEqual(normalize_records({"messages": [record]}, "messages"), [record])
        self.assertEqual(normalize_records([], "messages"), [])
        self.assertEqual(normalize_records({"messages": []}, "messages"), [])
        self.assertIsNone(normalize_records([record, "bad"]))

    def test_version(self):
        self.assertEqual(parse_version({"release": "4.0.7"}), "4.0.7")
        self.assertIsNone(parse_version({"release": " "}))

    def test_system_metrics(self):
        self.assertEqual(
            parse_system_metrics({"cpuload": "2", "memory": "97592/262144", "uptime": "567"}),
            {"cpuload": 2, "memory_percent": memory_percent("97592/262144"), "uptime": 567},
        )
        self.assertEqual(memory_percent("111004/262144"), 42.3)
        self.assertEqual(
            parse_system_metrics({"cpuload": "unknown", "memory": None, "uptime": True}),
            {"cpuload": None, "memory_percent": None, "uptime": None},
        )

    def test_mesh_member_parses_only_documented_metrics(self):
        self.assertEqual(
            parse_mesh_member(
                {
                    "mac": "50:ff:21:69:21:7d",
                    "ip": "192.168.15.42",
                    "model": "Speedster (KN-3310)",
                    "fw": "3.8.2",
                    "associations": "3",
                    "system": {"cpuload": "2", "memory": "97592/262144", "uptime": "567"},
                    "backhaul": {"uplink": "GigabitEthernet0/Vlan1", "speed": "1000"},
                    "license": "must-not-be-exposed",
                }
            ),
            {
                "cpuload": 2,
                "memory_percent": memory_percent("97592/262144"),
                "uptime": 567,
                "associations": 3,
                "backhaul_uplink": "GigabitEthernet0/Vlan1",
                "backhaul_speed": 1000,
                "backhaul_ap": None,
                "backhaul_rssi": None,
                "backhaul_txrate": None,
                "model": "Speedster (KN-3310)",
                "fw": "3.8.2",
                "mac": "50:ff:21:69:21:7d",
                "ip": "192.168.15.42",
            },
        )
        self.assertIsNone(parse_mesh_member({"system": {"memory": "not-a-fraction"}})["memory_percent"])

    def test_mesh_member_parses_os5_backhaul_fields_from_member(self):
        parsed = parse_mesh_member(
            {
                "mac": "50:ff:22:b1:1c:b1",
                "associations": "1",
                "backhaul": {
                    "uplink": "WifiMaster1/WifiStation0",
                    "ap": "WifiMaster1/Backhaul0",
                    "txrate": "720",
                    "rssi": "-61",
                },
            }
        )

        self.assertEqual(parsed["backhaul_ap"], "WifiMaster1/Backhaul0")
        self.assertEqual(parsed["backhaul_rssi"], -61)
        self.assertEqual(parsed["backhaul_txrate"], 720)
        self.assertEqual(parsed["associations"], 1)

    def test_mesh_member_keeps_absent_or_invalid_backhaul_fields_unknown(self):
        for backhaul in ({}, {"ap": 123, "rssi": "unknown", "txrate": True}):
            with self.subTest(backhaul=backhaul):
                parsed = parse_mesh_member({"backhaul": backhaul})
                self.assertIsNone(parsed["backhaul_ap"])
                self.assertIsNone(parsed["backhaul_rssi"])
                self.assertIsNone(parsed["backhaul_txrate"])

    def test_internet_status_keeps_unreliable_values_unknown(self):
        status = parse_internet_status(
            {
                "reliable": "no",
                "gateway-accessible": "yes",
                "dns-accessible": "yes",
                "host-accessible": "unknown",
                "internet": "yes",
            }
        )
        self.assertEqual(
            status,
            {
                "reliable": False,
                "gateway-accessible": True,
                "dns-accessible": True,
                "host-accessible": None,
                "internet": True,
            },
        )

    def test_internet_status_accepts_live_booleans_and_captive_alias(self):
        self.assertEqual(
            parse_internet_status(
                {
                    "enabled": False,
                    "reliable": True,
                    "gateway-accessible": True,
                    "dns-accessible": True,
                    "captive-accessible": True,
                    "internet": True,
                }
            ),
            {
                "reliable": True,
                "gateway-accessible": True,
                "dns-accessible": True,
                "host-accessible": True,
                "internet": True,
            },
        )

    def test_interface_fields_are_whitelisted_and_safely_parsed(self):
        result = parse_interface_fields(
            {
                "id": "GigabitEthernet1",
                "index": "2",
                "type": "Ethernet",
                "connected": "yes",
                "state": "up",
                "mtu": 1500,
                "tx-queue": "1000",
                "address": "192.168.1.1",
                "uptime": "2621",
                "password": "ignored",
            }
        )
        self.assertEqual(
            result,
            {
                "id": "GigabitEthernet1",
                "index": 2,
                "type": "Ethernet",
                "state": "up",
                "connected": True,
                "mtu": 1500,
                "tx-queue": 1000,
                "address": "192.168.1.1",
                "uptime": 2621,
            },
        )

    def test_documented_stat_counters(self):
        self.assertEqual(
            parse_stat_counters({"rxbytes": "5440", "txpackets": 46, "txerrors": -1, "secret": 1}),
            {"rxbytes": 5440, "txpackets": 46},
        )


class PingCheckTests(unittest.TestCase):
    def test_normalizes_configured_interface_results(self):
        self.assertEqual(
            parse_ping_checks(
                {
                    "pingcheck": [
                        {"profile": "primary", "interface": "PPPoE0", "status": "pass"},
                        {"profile": "backup", "interface": "UsbQmi0", "status": "fail"},
                        {"profile": "unassigned", "status": "pass"},
                    ]
                }
            ),
            [
                {"profile": "primary", "interface": "PPPoE0", "status": "pass"},
                {"profile": "backup", "interface": "UsbQmi0", "status": "fail"},
            ],
        )

    def test_unknown_and_malformed_statuses_remain_unknown(self):
        self.assertEqual(
            parse_ping_checks([{"profile": "wan", "interface": "ISP", "status": "unknown"}]),
            [{"profile": "wan", "interface": "ISP", "status": None}],
        )
        self.assertIsNone(parse_ping_checks({"pingcheck": "not a list"}))
        self.assertEqual(parse_ping_checks({"pingcheck": []}), [])


class UsbStorageTests(unittest.TestCase):
    def test_detects_only_documented_storage_subsystems(self):
        self.assertEqual(
            parse_usb_storage_devices(
                {
                    "device": [
                        {"name": "USB0", "label": "private-name", "subsystem": "storage"},
                        {"name": "USB1", "subsystem": "modem"},
                    ]
                }
            ),
            [{"name": "USB0", "label": "private-name", "subsystem": "storage"}],
        )

    def test_distinguishes_empty_from_malformed_inventory(self):
        self.assertEqual(parse_usb_storage_devices({"device": []}), [])
        self.assertIsNone(parse_usb_storage_devices({"device": "unknown"}))
        self.assertIsNone(parse_usb_storage_devices(None))


class RouteSelectionTests(unittest.TestCase):
    def test_selects_lowest_metric_default_route(self):
        result = select_default_route(
            [
                {"destination": "10.0.0.0/8", "gateway": "0.0.0.0", "interface": "Home", "metric": 0},
                {"destination": "0.0.0.0/0", "gateway": "192.168.1.1", "interface": "WAN-A", "metric": "10"},
                {"destination": "0.0.0.0/0", "gateway": "192.168.2.1", "interface": "WAN-B", "metric": 20},
            ]
        )
        self.assertEqual(result.state, "unique")
        self.assertEqual(result.interface, "WAN-A")
        self.assertEqual(result.gateway, "192.168.1.1")
        self.assertEqual(result.metric, 10)
        self.assertEqual(
            default_route(
                [{"destination": "0.0.0.0/0", "gateway": "192.168.1.1", "interface": "WAN-A", "metric": 10}]
            ),
            ("known", "WAN-A"),
        )

    def test_equal_best_metrics_on_different_interfaces_are_ambiguous(self):
        result = select_default_route(
            [
                {"destination": "0.0.0.0/0", "gateway": "192.168.1.1", "interface": "WAN-A", "metric": 10},
                {"destination": "0.0.0.0/0", "gateway": "192.168.2.1", "interface": "WAN-B", "metric": 10},
            ]
        )
        self.assertEqual(result.state, "ambiguous")
        self.assertIsNone(result.interface)
        self.assertEqual(default_route([{"destination": "0.0.0.0/0", "gateway": "a", "interface": "A", "metric": 1}, {"destination": "0.0.0.0/0", "gateway": "b", "interface": "B", "metric": 1}]), ("ambiguous", None))

    def test_same_best_interface_with_different_gateways_stays_interface_unique(self):
        result = select_default_route(
            [
                {"destination": "0.0.0.0/0", "gateway": "192.168.1.1", "interface": "WAN-A", "metric": 10},
                {"destination": "0.0.0.0/0", "gateway": "192.168.1.2", "interface": "WAN-A", "metric": 10},
            ]
        )
        self.assertEqual(result.state, "unique")
        self.assertEqual(result.interface, "WAN-A")
        self.assertIsNone(result.gateway)

    def test_absence_and_malformed_data_are_distinct(self):
        self.assertEqual(select_default_route([]).state, "absent")
        self.assertEqual(default_route([]), ("missing", None))
        self.assertEqual(
            select_default_route([{"destination": "0.0.0.0/0", "gateway": "0.0.0.0", "interface": "WAN", "metric": "bad"}]).state,
            "unknown",
        )
        self.assertEqual(select_default_route(None).state, "unknown")


class SmsTests(unittest.TestCase):
    def test_id_keyed_router_messages_preserve_ids_and_read_state(self):
        messages = parse_sms_list_response({"messages": {
            "sim-1": {"read": False, "parts": 1, "total-parts": 1, "text": "one"},
            "sim-2": {"read": True, "parts": 1, "total-parts": 1, "text": "two"},
        }})
        self.assertEqual([(row["id"], row["read"]) for row in messages],
                         [("sim-1", False), ("sim-2", True)])
        self.assertIsNone(parse_sms_list_response({"messages": {"sim-1": "bad"}}))

    def test_complete_and_partial_messages(self):
        complete = parse_sms_message(
            {"id": "sim-0", "read": "yes", "parts": "2", "total-parts": "2", "text": "complete"}
        )
        self.assertTrue(complete["complete"])
        self.assertIs(complete_sms({"parts": "2", "total-parts": "2"}), True)
        self.assertEqual(complete["text"], "complete")

        partial = parse_sms_message(
            {"id": "sim-1", "read": "no", "parts": 3, "total-parts": 4, "text": "intermediate"}
        )
        self.assertFalse(partial["complete"])
        self.assertIs(complete_sms({"parts": 3, "total-parts": 4}), False)
        self.assertIsNone(partial["text"])

    def test_incomplete_metadata_stays_unknown(self):
        message = parse_sms_message({"id": "sim-2", "parts": 0, "total-parts": 2, "text": "partial"})
        self.assertIsNone(message["complete"])
        self.assertIsNone(complete_sms({"parts": 0, "total-parts": 2}))
        self.assertIsNone(message["text"])

    def test_fingerprint_changes_when_a_reused_id_has_new_complete_content(self):
        first = {"id": "sim-0", "from": "sender", "timestamp": "now", "parts": 1, "total-parts": 1, "read": "no", "text": "first"}
        read = {**first, "read": "yes"}
        reused = {**first, "text": "second"}
        self.assertEqual(sms_fingerprint(first), sms_fingerprint(read))
        self.assertNotEqual(sms_fingerprint(first), sms_fingerprint(reused))
        self.assertIsNone(sms_fingerprint({"from": "sender"}))

    def test_malformed_response_shape_is_not_an_empty_message_list(self):
        with self.assertRaises(TypeError):
            parse_sms_messages({"messages": []})
        with self.assertRaises(TypeError):
            parse_sms_messages(["not a record"])
        self.assertEqual(parse_sms_messages([]), [])


class WireguardServerTests(unittest.TestCase):
    def _response(self, peer):
        return [
            {
                "wireguard-server": {
                    "id": "Wireguard6",
                    "interface-name": "Wireguard6",
                    "type": "Wireguard",
                    "description": "WireGuard VPN Server",
                    "wireguard": {"server-owned": {"peer": [peer] if peer is not None else []}},
                }
            }
        ]

    def test_parses_os5_server_peer_metrics_and_separates_endpoints(self):
        result = parse_wireguard_server_response(
            self._response(
                {
                    "public-key": "example-public-key",
                    "description": "phone",
                    "online": "yes",
                    "rxbytes": "42",
                    "txbytes": 18,
                    "last-handshake": 2147483647,
                    "remote-port": 52000,
                    "remote-endpoint-address": "198.51.100.2",
                }
            )
        )

        self.assertIsNotNone(result)
        peer = result["peers"][0]
        self.assertTrue(peer["online"])
        self.assertEqual(peer["rxbytes"], 42)
        self.assertEqual(peer["txbytes"], 18)
        self.assertIsNone(peer["last_handshake"])
        self.assertEqual(peer["remote_endpoint"], "198.51.100.2")
        self.assertEqual(peer["remote_port"], 52000)
        self.assertNotIn("example-public-key", peer["id"])

    def test_parses_handshake_age_in_seconds(self):
        result = parse_wireguard_server_response(
            self._response(
                {
                    "public-key": "example-public-key",
                    "description": "phone",
                    "online": "yes",
                    "rxbytes": 1,
                    "txbytes": 2,
                    "last-handshake": "75",
                    "remote-endpoint-address": "198.51.100.2",
                }
            )
        )

        self.assertEqual(result["peers"][0]["last_handshake"], 75)

    def test_empty_peer_collection_is_confirmed_but_malformed_is_unknown(self):
        empty = parse_wireguard_server_response(self._response(None))
        malformed = parse_wireguard_server_response({"wireguard-server": "bad"})

        self.assertEqual(empty["peers"], [])
        self.assertIsNone(malformed)

    def test_parses_unwrapped_show_result_and_rejects_missing_peer_inventory(self):
        wrapped = self._response(
            {
                "public-key": "example-public-key",
                "description": "phone",
                "online": "no",
                "rxbytes": 0,
                "txbytes": 0,
                "last-handshake": 2147483647,
                "local-endpoint-address": "0.0.0.0",
                "remote-endpoint-address": "0.0.0.0",
            }
        )[0]["wireguard-server"]

        self.assertEqual(parse_wireguard_server_response(wrapped)["interface_id"], "Wireguard6")
        del wrapped["wireguard"]["server-owned"]["peer"]
        self.assertIsNone(parse_wireguard_server_response(wrapped))


class WireguardInterfacePeerTests(unittest.TestCase):
    def _interfaces(self):
        return {
            "interface": [
                {
                    "id": "Wireguard0",
                    "interface-name": "wg-home",
                    "type": "Wireguard",
                    "description": "Home VPN",
                    "wireguard": {
                        "peer": [
                            {
                                "public-key": "home-phone-key",
                                "description": "Phone",
                                "online": "yes",
                                "rxbytes": "32",
                                "txbytes": "17",
                                "last-handshake": 2147483647,
                                "remote-endpoint-address": "198.51.100.4",
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
                                "public-key": "work-laptop-key",
                                "description": "Laptop",
                                "online": "no",
                                "rxbytes": 0,
                                "txbytes": 0,
                                "last-handshake": "75",
                            }
                        ]
                    },
                },
            ]
        }

    def test_parses_multiple_interface_peers_without_retaining_public_keys(self):
        inventories = parse_wireguard_interface_peers(self._interfaces())

        self.assertEqual(set(inventories), {"Wireguard0", "Wireguard1"})
        home_peer = inventories["Wireguard0"]["peers"][0]
        work_peer = inventories["Wireguard1"]["peers"][0]
        self.assertEqual(inventories["Wireguard0"]["interface_description"], "Home VPN")
        self.assertEqual(home_peer["name"], "Phone")
        self.assertIs(home_peer["online"], True)
        self.assertEqual(home_peer["rxbytes"], 32)
        self.assertEqual(home_peer["txbytes"], 17)
        self.assertIsNone(home_peer["last_handshake"])
        self.assertEqual(home_peer["remote_endpoint"], "198.51.100.4")
        self.assertIs(work_peer["online"], False)
        self.assertEqual(work_peer["last_handshake"], 75)
        self.assertNotIn("home-phone-key", str(inventories))
        self.assertNotIn("work-laptop-key", str(inventories))

    def test_unnamed_peer_uses_full_stable_id(self):
        response = {
            "interface": [
                {
                    "id": "Wireguard0",
                    "type": "Wireguard",
                    "wireguard": {"peer": [{"public-key": "unnamed-peer-key"}]},
                }
            ]
        }

        peer = parse_wireguard_interface_peers(response)["Wireguard0"]["peers"][0]

        self.assertEqual(peer["name"], wireguard_peer_id("unnamed-peer-key"))

    def test_missing_and_malformed_peer_lists_remain_unknown(self):
        response = {
            "interface": [
                {"id": "Wireguard0", "type": "Wireguard", "wireguard": {}},
                {
                    "id": "Wireguard1",
                    "type": "Wireguard",
                    "wireguard": {"peer": [{"description": "missing key"}]},
                },
                {"id": "Wireguard2", "type": "Wireguard", "wireguard": {"peer": []}},
                {
                    "id": "Wireguard3",
                    "type": "Wireguard",
                    "wireguard": {
                        "peer": [
                            {"public-key": "duplicate-key"},
                            {"public-key": "duplicate-key"},
                        ]
                    },
                },
            ]
        }

        inventories = parse_wireguard_interface_peers(response)

        self.assertEqual(set(inventories), {"Wireguard2"})
        self.assertEqual(inventories["Wireguard2"]["peers"], [])
        self.assertIsNone(parse_wireguard_interface_peers({"interface": "malformed"}))

    def test_valid_server_inventory_overrides_only_its_interface(self):
        embedded = parse_wireguard_interface_peers(self._interfaces())
        server = parse_wireguard_server_response(
            {
                "id": "Wireguard0",
                "interface-name": "wg-home",
                "type": "Wireguard",
                "description": "Home VPN server",
                "wireguard": {
                    "server-owned": {
                        "peer": [
                            {
                                "public-key": "home-phone-key",
                                "description": "Server peer",
                                "online": "no",
                                "rxbytes": 3,
                                "txbytes": 4,
                                "last-handshake": 20,
                            }
                        ]
                    }
                },
            }
        )

        merged = merge_wireguard_peer_sources(embedded, server)

        self.assertEqual(len(merged["Wireguard0"]["peers"]), 1)
        self.assertEqual(merged["Wireguard0"]["peers"][0]["name"], "Server peer")
        self.assertEqual(merged["Wireguard1"]["peers"][0]["name"], "Laptop")
        self.assertNotIn("home-phone-key", str(merged))

        invalid_server = {"interface_id": "Wireguard0", "peers": ["malformed"]}
        unchanged = merge_wireguard_peer_sources(embedded, invalid_server)
        self.assertEqual(unchanged["Wireguard0"]["peers"][0]["name"], "Phone")


if __name__ == "__main__":
    unittest.main()
