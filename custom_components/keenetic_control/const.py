"""Constants for the Keenetic Control integration."""

from datetime import timedelta

DOMAIN = "keenetic_control"
PLATFORMS = [
    "binary_sensor",
    "button",
    "device_tracker",
    "number",
    "select",
    "sensor",
    "switch",
]

DEFAULT_ADDRESS = "http://192.168.1.1"
DEFAULT_PORT = 80
DEFAULT_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 1
DEFAULT_TIMEOUT = 15
USSD_TIMEOUT = 60
UPDATE_INTERVAL = timedelta(seconds=DEFAULT_SCAN_INTERVAL)

CONF_ADDRESS = "address"
CONF_PORT = "port"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_ALL_CLIENTS = "all_clients"
CONF_CLIENTS = "clients"
CONF_ALL_POLICIES = "all_policies"
CONF_POLICY_CLIENTS = "policy_clients"
CONF_PORT_FORWARD_SWITCHES = "port_forward_switches"
CONF_WIFI_ENABLED_ACCESS_POINTS = "wifi_enabled_access_points"
CONF_SMS_TARGET = "sms_target"
CONF_ALLOW_MUTATING_RCI = "allow_mutating_rci"
CONF_BACKUP_BEFORE_UPDATE = "backup_before_update"
CONF_BACKUP_TYPES = "backup_types"

BACKUP_CONFIG = "config"
BACKUP_FIRMWARE = "firmware"

EVENT_WAN_CHANGED = f"{DOMAIN}_wan_changed"
EVENT_SMS = f"{DOMAIN}_sms"

DATA_CLIENTS = "clients"
DATA_ENTRIES = "entries"
DATA_PLATFORMS = "platforms"

