"""Regression checks for redacted, observed RCI response shapes."""

import importlib.util
import json
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).parents[1]
_SPEC = importlib.util.spec_from_file_location(
    "keenetic_fixture_model", _ROOT / "custom_components/keenetic_control/model.py"
)
assert _SPEC is not None and _SPEC.loader is not None
_MODEL = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _MODEL
_SPEC.loader.exec_module(_MODEL)


def fixture(name: str):
    return json.loads((_ROOT / "tests/fixtures/rci" / name).read_text())


class ObservedRciShapesTests(unittest.TestCase):
    def test_internet_checker_booleans_and_captive_alias(self):
        status = _MODEL.parse_internet_status(fixture("internet.json"))
        self.assertIs(status["reliable"], True)
        self.assertIs(status["host-accessible"], True)
        self.assertIs(status["gateway-accessible"], True)

    def test_keyed_usb_media_and_mounted_partition(self):
        devices = _MODEL.parse_usb_storage_devices(fixture("usb.json"))
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0]["subsystem"], "media")
        self.assertEqual(
            _MODEL.parse_usb_media_status(fixture("media.json")),
            {
                "available": True,
                "media_count": 1,
                "volume_count": 1,
                "mounted_volume_count": 1,
                "mounted_total_bytes": 2048,
                "mounted_free_bytes": 1024,
            },
        )

    def test_heterogeneous_hotspot_rows(self):
        response = fixture("hotspot.json")
        for index, row in enumerate(response["host"], start=1):
            row["mac"] = f"02:00:00:00:00:{index:02x}"
        self.assertEqual(_MODEL.wifi_client_count(response), 2)
        self.assertEqual(
            _MODEL.parse_hotspot_client(response["host"][0])["connection_type"],
            "Wi-Fi",
        )
        self.assertEqual(
            _MODEL.parse_hotspot_client(response["host"][2])["connection_type"],
            "Ethernet",
        )


if __name__ == "__main__":
    unittest.main()
