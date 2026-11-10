"""Check cookie isolation against a real local HTTP server, including hostname cookies."""

import hashlib
import socket
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.common import MockConfigEntry
from yarl import URL

from custom_components.keenetic_control.const import DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
def hass_config_dir(hass_tmp_config_dir):
    (Path(hass_tmp_config_dir) / "custom_components" / DOMAIN).symlink_to(
        Path(__file__).parents[2] / "custom_components" / DOMAIN,
        target_is_directory=True,
    )
    return hass_tmp_config_dir


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1"])
async def test_entry_cookies_are_isolated_and_survive_only_their_session(
    hass, socket_enabled, host
):
    logins = []
    observed = []

    async def auth(request):
        observed.append(dict(request.cookies))
        if request.method == "POST":
            data = await request.json()
            digest = hashlib.md5(f"{data['login']}:router:secret".encode()).hexdigest()
            assert (
                data["password"]
                == hashlib.sha256(f"challenge{digest}".encode()).hexdigest()
            )
            assert request.cookies.get("challenge") == "issued"
            logins.append(data["login"])
            response = web.json_response({})
            response.set_cookie("session_id", data["login"])
            return response
        if request.cookies.get("session_id"):
            return web.json_response({})
        response = web.Response(
            status=401,
            headers={"X-NDM-Realm": "router", "X-NDM-Challenge": "challenge"},
        )
        response.set_cookie("challenge", "issued")
        return response

    async def status(request):
        observed.append(dict(request.cookies))
        return web.json_response({"user": request.cookies.get("session_id")})

    async def probe(request):
        return web.json_response(dict(request.cookies))

    app = web.Application()
    app.router.add_route("*", "/auth", auth)
    app.router.add_get("/rci/status", status)
    app.router.add_get("/probe", probe)
    async with TestServer(app, host="127.0.0.1") as server:
        base = f"http://{host}:{server.port}"
        shared = async_get_clientsession(hass, verify_ssl=False)
        shared.cookie_jar.update_cookies({"unrelated": "private"}, URL(base))
        shared_before = dict(shared.cookie_jar.filter_cookies(URL(base)))
        entries = []
        # Resolve locally and deterministically; HTTP and cookie handling remain real.
        with (
            patch.object(
                shared.connector._resolver,
                "resolve",
                return_value=[
                    {
                        "hostname": host,
                        "host": "127.0.0.1",
                        "port": server.port,
                        "family": socket.AF_INET,
                        "proto": 0,
                        "flags": 0,
                    }
                ],
            ),
            patch(
                "custom_components.keenetic_control.KeeneticCoordinator.async_config_entry_first_refresh"
            ),
            patch.object(hass.config_entries, "async_forward_entry_setups"),
        ):
            for username in ("first", "second"):
                entry = MockConfigEntry(
                    domain=DOMAIN,
                    unique_id=username,
                    version=3,
                    data={
                        "address": f"http://{host}",
                        "port": server.port,
                        "username": username,
                        "password": "secret",
                    },
                )
                entry.add_to_hass(hass)
                assert await hass.config_entries.async_setup(entry.entry_id)
                entries.append(entry)
                assert await entry.runtime_data.api.async_get("status") == {
                    "user": username
                }
            for entry, username in zip(entries, ("first", "second"), strict=True):
                assert await entry.runtime_data.api.async_get("status") == {
                    "user": username
                }
            assert logins == ["first", "second"]
            assert all("unrelated" not in cookies for cookies in observed)
            assert dict(shared.cookie_jar.filter_cookies(URL(base))) == shared_before
            async with shared.get(base + "/probe") as response:
                assert "session_id" not in await response.json()
            first_session = entries[0].runtime_data.api._session
            assert await hass.config_entries.async_reload(entries[0].entry_id)
            assert first_session.closed
            assert await entries[0].runtime_data.api.async_get("status") == {
                "user": "first"
            }
            assert logins == ["first", "second", "first"]
            assert await entries[1].runtime_data.api.async_get("status") == {
                "user": "second"
            }
            for entry in entries:
                session = entry.runtime_data.api._session
                assert await hass.config_entries.async_unload(entry.entry_id)
                assert session.closed
            async with shared.get(base + "/probe") as response:
                assert "session_id" not in await response.json()
