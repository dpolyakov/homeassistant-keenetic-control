"""Tests for delayed USSD completion and timeout handling."""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path

if "aiohttp" not in sys.modules and importlib.util.find_spec("aiohttp") is None:
    aiohttp = types.ModuleType("aiohttp")

    class ClientError(Exception):
        pass

    class ClientTimeout:
        def __init__(self, total=None):
            self.total = total

    aiohttp.ClientError = ClientError
    aiohttp.ClientSession = object
    aiohttp.ClientTimeout = ClientTimeout
    sys.modules["aiohttp"] = aiohttp

PACKAGE_PATH = Path(__file__).parents[1] / "custom_components" / "keenetic_control"
PACKAGE_NAME = "custom_components.keenetic_control"
if PACKAGE_NAME not in sys.modules:
    package = types.ModuleType(PACKAGE_NAME)
    package.__path__ = [str(PACKAGE_PATH)]
    sys.modules[PACKAGE_NAME] = package

from custom_components.keenetic_control.ussd import async_complete_ussd  # noqa: E402


class UssdCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_immediate_result_does_not_poll(self):
        calls = []

        async def send(timeout):
            calls.append(("post", timeout))
            return {"response": "balance"}

        async def poll(timeout):
            calls.append(("get", timeout))
            return {"response": "unexpected"}

        result = await async_complete_ussd(send, poll, timeout=2, poll_interval=0)

        self.assertEqual(result, {"response": "balance"})
        self.assertEqual([method for method, _timeout in calls], ["post"])

    async def test_delayed_result_posts_once_and_polls_until_complete(self):
        calls = []
        responses = [{"continued": True}, {"continued": True}, {"response": "balance"}]

        async def send(timeout):
            calls.append(("post", timeout))
            return responses.pop(0)

        async def poll(timeout):
            calls.append(("get", timeout))
            return responses.pop(0)

        result = await async_complete_ussd(send, poll, timeout=2, poll_interval=0)

        self.assertEqual(result, {"response": "balance"})
        self.assertEqual([method for method, _timeout in calls], ["post", "get", "get"])
        self.assertGreaterEqual(calls[0][1], calls[-1][1])

    async def test_shared_timeout_covers_initial_post(self):
        calls = []

        async def send(timeout):
            calls.append("post")
            await asyncio.sleep(0.02)
            return {"response": "late"}

        async def poll(_timeout):
            calls.append("get")
            return {"response": "unexpected"}

        with self.assertRaises(asyncio.TimeoutError):
            await async_complete_ussd(send, poll, timeout=0.005)

        self.assertEqual(calls, ["post"])

    async def test_shared_timeout_covers_delayed_polling(self):
        calls = []

        async def send(_timeout):
            calls.append("post")
            return {"continued": True}

        async def poll(_timeout):
            calls.append("get")
            return {"continued": True}

        with self.assertRaises(asyncio.TimeoutError):
            await async_complete_ussd(send, poll, timeout=0.005)

        self.assertEqual(calls, ["post"])


if __name__ == "__main__":
    unittest.main()
