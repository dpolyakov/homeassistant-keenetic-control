"""Static icon metadata checks for user-facing entity semantics."""

from __future__ import annotations

import json
import unittest
from pathlib import Path


ICONS_PATH = (
    Path(__file__).parents[1]
    / "custom_components"
    / "keenetic_control"
    / "icons.json"
)
TRANSLATIONS_DIR = ICONS_PATH.parent / "translations"


CLIENT_TRANSLATION_SECTIONS = {
    "binary_sensor": ("client_dhcp_registered", "client_link_status"),
    "button": ("client_clear_bandwidth_limit", "client_wake_on_lan"),
    "select": ("client_policy",),
    "number": ("client_bandwidth_limit",),
    "sensor": (
        "client_connection_type",
        "client_ip",
        "client_port",
        "client_first_seen",
        "client_last_seen",
        "client_link_speed",
        "client_received_data",
        "client_sent_data",
        "client_rssi",
        "client_tx_rate",
        "client_uptime",
        "client_wifi_band",
        "client_wifi_mode",
    ),
}


class IconMetadataTests(unittest.TestCase):
    def test_core_diagnostics_have_contextual_icons(self):
        icons = json.loads(ICONS_PATH.read_text())
        sensors = icons["entity"]["sensor"]

        self.assertEqual(sensors["cpu_load"]["default"], "mdi:cpu-64-bit")
        self.assertEqual(sensors["memory_used"]["default"], "mdi:memory")
        self.assertEqual(sensors["uptime"]["default"], "mdi:clock-start")
        self.assertEqual(sensors["firmware_version"]["default"], "mdi:book-information-variant")
        self.assertEqual(sensors["default_route_ip"]["default"], "mdi:ip")
        self.assertEqual(sensors["wireguard_peer_remote_endpoint"]["default"], "mdi:ip")
        self.assertEqual(sensors["client_ip"]["default"], "mdi:ip")
        self.assertEqual(
            icons["entity"]["binary_sensor"]["usb_storage_detected"]["default"],
            "mdi:usb-flash-drive-outline",
        )

    def test_client_translations_are_entity_names_without_device_prefix(self):
        for locale in ("en", "ru"):
            translations = json.loads(
                (TRANSLATIONS_DIR / f"{locale}.json").read_text()
            )["entity"]
            for section, keys in CLIENT_TRANSLATION_SECTIONS.items():
                for key in keys:
                    name = translations[section][key]["name"]
                    with self.subTest(locale=locale, section=section, key=key):
                        self.assertNotIn("{client}", name)

            sensor_names = translations["sensor"]
            self.assertEqual(sensor_names["client_received_data"]["name"], "Received" if locale == "en" else "Принято")
            self.assertEqual(sensor_names["client_sent_data"]["name"], "Sent" if locale == "en" else "Отправлено")
            self.assertEqual(sensor_names["client_uptime"]["name"], "Connection time" if locale == "en" else "Время подключения")

    def test_lte_signal_ranges_follow_official_boundaries(self):
        icons = json.loads(ICONS_PATH.read_text())["entity"]["sensor"]

        self.assertEqual(
            icons["modem_rssi"]["range"],
            {
                "-90": "mdi:signal-cellular-outline",
                "-89.999999": "mdi:signal-cellular-1",
                "-85": "mdi:signal-cellular-2",
                "-75": "mdi:signal-cellular-3",
            },
        )
        self.assertEqual(
            icons["modem_rsrp"]["range"],
            {
                "-100": "mdi:signal-cellular-outline",
                "-99.999999": "mdi:signal-cellular-1",
                "-90": "mdi:signal-cellular-2",
                "-80": "mdi:signal-cellular-3",
            },
        )
        self.assertEqual(
            icons["modem_rsrq"]["range"],
            {
                "-20": "mdi:signal-cellular-outline",
                "-19.999999": "mdi:signal-cellular-1",
                "-15": "mdi:signal-cellular-2",
                "-10": "mdi:signal-cellular-3",
            },
        )
        self.assertEqual(
            icons["modem_sinr"]["range"],
            {
                "0": "mdi:signal-cellular-outline",
                "0.000001": "mdi:signal-cellular-1",
                "10": "mdi:signal-cellular-2",
                "20": "mdi:signal-cellular-3",
            },
        )

    def test_lte_fixed_icons_are_existing_mdi_names(self):
        icons = json.loads(ICONS_PATH.read_text())["entity"]

        self.assertEqual(icons["sensor"]["modem_rssi"]["default"], "mdi:signal-cellular-outline")
        self.assertEqual(icons["sensor"]["modem_rsrp"]["default"], "mdi:signal-cellular-outline")
        self.assertEqual(icons["binary_sensor"]["modem_roaming"]["default"], "mdi:signal-variant")


if __name__ == "__main__":
    unittest.main()
