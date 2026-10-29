"""Transport contract tests using a small fake HTTP session."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path


if importlib.util.find_spec("aiohttp") is None:
    aiohttp = types.ModuleType("aiohttp")

    class ClientError(Exception):
        pass

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

    aiohttp.ClientError = ClientError
    aiohttp.ClientSession = object
    aiohttp.ClientTimeout = ClientTimeout
    aiohttp.CookieJar = CookieJar
    sys.modules["aiohttp"] = aiohttp

if "yarl" not in sys.modules and importlib.util.find_spec("yarl") is None:
    yarl = types.ModuleType("yarl")
    yarl.URL = str
    sys.modules["yarl"] = yarl

_PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "keenetic_control"
_PACKAGE_NAME = "custom_components.keenetic_control"
if _PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(_PACKAGE_NAME)
    package.__path__ = [str(_PACKAGE_PATH)]
    sys.modules[_PACKAGE_NAME] = package

from custom_components.keenetic_control.api import (  # noqa: E402
    CannotConnect,
    InvalidAuth,
    KeeneticApi,
    RciCommandError,
    UnsupportedResponse,
)


class _Response:
    def __init__(self, status, payload=None, *, headers=None, content_type="application/json"):
        self.status = status
        self.payload = payload
        self.headers = headers or {"Content-Type": content_type}
        self.cookies = {}
        self.url = "http://router"

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def read(self):
        return b"router body must not leak"

    async def text(self):
        return self.payload if isinstance(self.payload, str) else ""

    async def json(self, content_type=None):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class _Session:
    def __init__(self, responses, auth_responses=None):
        self.responses = list(responses)
        self.auth_responses = list(auth_responses or [_Response(200)])
        self.calls = []
        self.auth_calls = []

    @staticmethod
    def _response(response, url):
        if isinstance(response, Exception):
            raise response
        response.url = url
        return response

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self._response(self.responses.pop(0), url)

    def get(self, url, **kwargs):
        self.auth_calls.append(("GET", url, kwargs))
        return self._response(self.auth_responses.pop(0), url)

    def post(self, url, **kwargs):
        self.auth_calls.append(("POST", url, kwargs))
        return self._response(self.auth_responses.pop(0), url)


class KeeneticApiTests(unittest.IsolatedAsyncioTestCase):
    def _client(self, responses, *, auth_responses=None):
        session = _Session(responses, auth_responses)
        api = KeeneticApi(session, "http://router", 80, "admin", "secret", timeout=7)
        api._uri_with_query = lambda url, params: "/rci/" + url.rsplit("/", 1)[-1]
        return api, session

    async def test_keenetic_challenge_authentication_hashes_credentials_and_sends_once(self):
        api, session = self._client(
            [_Response(200, {"status": "ok"})],
            auth_responses=[
                _Response(401, headers={"X-NDM-Realm": "Keenetic", "X-NDM-Challenge": "abc"}),
                _Response(200),
            ],
        )

        result = await api.async_post("sms/send", {"name": "UsbQmi0", "to": "+79990000000", "message": "hello"})

        self.assertEqual(result, {"status": "ok"})
        self.assertEqual(len(session.auth_calls), 2)
        self.assertEqual(session.auth_calls[0][0], "GET")
        self.assertEqual(session.auth_calls[1][0], "POST")
        self.assertEqual(session.auth_calls[1][2]["json"]["login"], "admin")
        self.assertEqual(session.calls[0][2]["json"], {"name": "UsbQmi0", "to": "+79990000000", "message": "hello"})

    async def test_rejected_credentials_and_http_error_do_not_expose_response_body(self):
        api, _session = self._client(
            [],
            auth_responses=[
                _Response(401, headers={"X-NDM-Realm": "Keenetic", "X-NDM-Challenge": "abc"}),
                _Response(401),
            ],
        )
        with self.assertRaises(InvalidAuth) as caught:
            await api.async_get("show/version")
        self.assertNotIn("router body", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))

        api, _session = self._client([_Response(500)], auth_responses=[_Response(200)])
        with self.assertRaises(RciCommandError) as caught:
            await api.async_get("show/version")
        self.assertNotIn("router body", str(caught.exception))

    async def test_malformed_json_and_unsupported_content_type_are_not_success(self):
        api, _session = self._client([_Response(200, ValueError("bad json"))])
        with self.assertRaises(UnsupportedResponse):
            await api.async_get("show/version")

        api, _session = self._client([_Response(200, "html", content_type="text/html")])
        with self.assertRaises(UnsupportedResponse):
            await api.async_get("show/version")

    async def test_numeric_rci_error_is_sanitized(self):
        api, _session = self._client([_Response(200, {"status": "error", "code": 7, "message": "+79990000000 secret"})])
        with self.assertRaises(RciCommandError) as caught:
            await api.async_post("interface/up", {"name": "WifiMaster0"})
        self.assertIn("code 7", str(caught.exception))
        self.assertNotIn("79990000000", str(caught.exception))
        self.assertNotIn("secret", str(caught.exception))

    async def test_transport_error_becomes_cannot_connect(self):
        api, _session = self._client([asyncio.TimeoutError()])
        with self.assertRaises(CannotConnect):
            await api.async_get("show/version")

    async def test_get_accepts_a_per_request_timeout(self):
        api, session = self._client([_Response(200, {"response": "done"})])

        result = await api.async_get("ussd/send", timeout=2.5)

        self.assertEqual(result, {"response": "done"})
        self.assertEqual(session.calls[0][2]["timeout"].total, 2.5)

    async def test_show_commands_use_the_documented_rci_batch_resource(self):
        api, session = self._client([_Response(200, [{"interface": {"stat": {"rxbytes": 12}}}])])

        result = await api.async_get("show/interface/stat", params={"name": "WifiMaster0"})

        self.assertEqual(result, {"rxbytes": 12})
        self.assertEqual(session.calls[0][0], "POST")
        self.assertTrue(session.calls[0][1].endswith("/rci/show"))
        self.assertEqual(
            session.calls[0][2]["json"],
            [{"interface": {"name": "WifiMaster0", "stat": {}}}],
        )

    async def test_parse_can_return_plain_text_for_read_only_log_command(self):
        log_text = "IpSec::Vici::Stats: out of memory"
        api, session = self._client(
            [_Response(200, log_text, content_type="text/plain")]
        )

        result = await api.async_post("parse", "show log", allow_text=True)

        self.assertEqual(result, log_text)
        self.assertEqual(session.calls[0][2]["json"], "show log")

    async def test_show_version_uses_a_single_nested_command(self):
        api, session = self._client([_Response(200, [{"version": {"release": "5.0"}}])])

        result = await api.async_get("show/version")

        self.assertEqual(result, {"release": "5.0"})
        self.assertEqual(session.calls[0][0], "POST")
        self.assertEqual(session.calls[0][2]["json"], [{"version": {}}])

    async def test_show_response_accepts_a_single_numeric_key_object(self):
        interface_record = {"WifiMaster0": {"id": "WifiMaster0", "type": "WifiMaster"}}
        api, _session = self._client([_Response(200, {"0": {"interface": interface_record}})])

        result = await api.async_get("show/interface")

        self.assertEqual(result, interface_record)

    async def test_connect_accepts_the_documented_version_batch_response(self):
        api, _session = self._client([_Response(200, [{"version": {"model": "Keenetic", "release": "5.0"}}])])

        result = await api.async_connect()

        self.assertEqual(result, {"model": "Keenetic", "release": "5.0"})

    async def test_show_response_must_match_the_requested_command(self):
        api, _session = self._client([_Response(200, [{"system": {"uptime": 3}}])])

        with self.assertRaises(UnsupportedResponse):
            await api.async_get("show/version")

    async def test_show_request_rejects_traversal_before_sending(self):
        api, session = self._client([])

        with self.assertRaises(ValueError):
            await api.async_get("show/../auth")

        self.assertEqual(session.calls, [])

    async def test_endpoint_rejects_path_traversal_before_sending(self):
        api, session = self._client([])
        with self.assertRaises(ValueError):
            await api.async_get("../auth")
        self.assertEqual(session.calls, [])


if __name__ == "__main__":
    unittest.main()
