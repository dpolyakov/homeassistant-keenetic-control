"""Small async RCI client with optional Keenetic challenge authentication."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from aiohttp import ClientError, ClientSession, ClientTimeout, CookieJar
from yarl import URL

from .const import DEFAULT_TIMEOUT

_LOGGER = logging.getLogger(__name__)
_SAFE_ENDPOINT_RE = re.compile(r"^[A-Za-z0-9_.\-/*#+]+$")


def _transport_error_detail(error: BaseException) -> str:
    """Return a safe OS/TLS reason without request URLs, headers, or payloads."""
    candidates = (
        getattr(error, "os_error", None),
        getattr(error, "certificate_error", None),
        error.__cause__,
        error.__context__,
    )
    for candidate in candidates:
        if candidate is None:
            continue
        for attribute in ("strerror", "verify_message"):
            detail = getattr(candidate, attribute, None)
            if isinstance(detail, str) and detail:
                return detail
    return type(error).__name__


class KeeneticError(Exception):
    """Base class for sanitized integration errors."""


class CannotConnect(KeeneticError):
    """The configured host did not answer in time."""


class InvalidAuth(KeeneticError):
    """The router rejected authentication."""


class UnsupportedResponse(KeeneticError):
    """The response is not a supported RCI response."""


class RciCommandError(KeeneticError):
    """The router returned an RCI error."""


class KeeneticApi:
    """Client for authenticated and unauthenticated RCI access."""

    def __init__(
        self,
        session: ClientSession,
        address: str,
        port: int,
        username: str,
        password: str,
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        self._session = session
        self._router_url = f"{address.rstrip('/')}:{int(port)}"
        self._base_url = f"{self._router_url}/rci"
        self._username = username
        self._password = password
        self._ssl = False
        self._timeout = ClientTimeout(total=timeout)
        self._cookie_jar = CookieJar(unsafe=True)
        self._auth_lock = asyncio.Lock()

    async def async_get(
        self, endpoint: str, *, params: Mapping[str, Any] | None = None, timeout: int | float | None = None
    ) -> Any:
        """Read an RCI resource."""
        show_path = endpoint.strip("/")
        if show_path == "show" or show_path.startswith("show/"):
            if not self._username and not self._password:
                # The local port-forwarding method exposes show resources as GET paths.
                return await self._request("GET", endpoint, params=params, timeout=timeout)
            if not self._username or not self._password:
                raise InvalidAuth("Provide both credentials or leave both empty for proxy access")
            result = await self._request(
                "POST", "show", data=self._show_request(endpoint, params), timeout=timeout
            )
            return self._unwrap_show_response(result, endpoint)
        return await self._request("GET", endpoint, params=params, timeout=timeout)

    async def async_post(
        self,
        endpoint: str,
        data: Any = None,
        *,
        timeout: int | float | None = None,
        allow_text: bool = False,
    ) -> Any:
        """Call an RCI action or update a setting; never retry a submitted body."""
        return await self._request(
            "POST", endpoint, data=data, timeout=timeout, allow_text=allow_text
        )

    async def async_delete(
        self,
        endpoint: str,
        data: Any = None,
        *,
        timeout: int | float | None = None,
    ) -> Any:
        """Reset one RCI setting without retrying the submitted body."""
        return await self._request("DELETE", endpoint, data=data, timeout=timeout)

    async def async_connect(self) -> dict[str, Any]:
        """Validate auth, transport and the version response before config is saved."""
        result = await self.async_get("show/version")
        if not isinstance(result, dict) or not any(key in result for key in ("model", "hw_id", "release", "title")):
            raise UnsupportedResponse("The host did not return a supported Keenetic version response")
        return result

    @staticmethod
    def _show_request(endpoint: str, params: Mapping[str, Any] | None) -> list[dict[str, Any]]:
        """Map a documented CLI show path to its nested RCI command object."""
        path = endpoint.strip("/")
        if not _SAFE_ENDPOINT_RE.fullmatch(path) or ".." in path:
            raise ValueError("Invalid RCI endpoint")
        parts = path.split("/")
        if len(parts) < 2 or parts[0] != "show":
            raise ValueError("A show command is required")

        result: dict[str, Any] = {}
        current = result
        for part in parts[1:-1]:
            child: dict[str, Any] = {}
            current[part] = child
            current = child
        current[parts[-1]] = {}

        if params:
            # `show interface <name> ...` scopes the interface name over child commands.
            target = result[parts[1]] if parts[1] == "interface" else current[parts[-1]]
            target.update(params)
        return [result]

    @staticmethod
    def _unwrap_show_response(response: Any, endpoint: str) -> Any:
        """Extract the sole requested command from the documented RCI batch response."""
        if isinstance(response, Mapping) and set(response) == {"0"}:
            records = [response["0"]]
        else:
            records = response if isinstance(response, list) else None
        if records is None or len(records) != 1 or not isinstance(records[0], Mapping):
            raise UnsupportedResponse("The host returned an unsupported RCI show response")

        value: Any = records[0]
        for part in endpoint.strip("/").split("/")[1:]:
            if not isinstance(value, Mapping) or part not in value:
                raise UnsupportedResponse("The host returned an incomplete RCI show response")
            value = value[part]
        return value

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: Any = None,
        timeout: int | float | None = None,
        allow_text: bool = False,
    ) -> Any:
        path = endpoint.strip("/")
        if not path or not _SAFE_ENDPOINT_RE.fullmatch(path) or ".." in path:
            raise ValueError("Invalid RCI endpoint")
        url = f"{self._base_url}/{quote(path, safe='/-._')}"
        try:
            await self._authenticate()
            request_kwargs: dict[str, Any] = {
                "params": params,
                "timeout": ClientTimeout(total=timeout) if timeout is not None else self._timeout,
                "ssl": self._ssl,
                "allow_redirects": False,
                "cookies": self._cookie_jar.filter_cookies(URL(url)),
            }
            if data is not None:
                request_kwargs["json"] = data
            async with self._session.request(method, url, **request_kwargs) as response:
                self._cookie_jar.update_cookies(response.cookies, response.url)
                if response.status == 401:
                    await response.read()
                    _LOGGER.warning(
                        "Keenetic RCI request %s %s returned HTTP 401",
                        method,
                        path,
                    )
                    if not self._username and not self._password:
                        raise InvalidAuth("The router requires credentials or an unauthenticated proxy")
                    raise InvalidAuth("The router rejected the configured credentials")
                return await self._read_response(response, allow_text=allow_text)
        except asyncio.TimeoutError as err:
            _LOGGER.warning("Keenetic RCI request %s %s timed out", method, path)
            raise CannotConnect("Timed out while contacting the router") from err
        except ClientError as err:
            _LOGGER.warning(
                "Keenetic RCI request %s %s at %s failed (%s): %s",
                method,
                path,
                self._base_url,
                type(err).__name__,
                _transport_error_detail(err),
            )
            raise CannotConnect("Could not contact the router") from err

    async def _authenticate(self) -> None:
        """Use Keenetic's challenge login and retain its per-router session cookie."""
        if not self._username and not self._password:
            return
        if not self._username or not self._password:
            raise InvalidAuth("Provide both credentials or leave both empty for proxy access")

        async with self._auth_lock:
            auth_url = f"{self._router_url}/auth"
            async with self._session.get(
                auth_url,
                timeout=self._timeout,
                ssl=self._ssl,
                allow_redirects=False,
                cookies=self._cookie_jar.filter_cookies(URL(auth_url)),
            ) as response:
                self._cookie_jar.update_cookies(response.cookies, response.url)
                if response.status != 401:
                    await response.read()
                    if 200 <= response.status < 300:
                        return
                    raise RciCommandError(f"The router returned HTTP {response.status} during authentication")
                realm = response.headers.get("X-NDM-Realm")
                challenge = response.headers.get("X-NDM-Challenge")
                await response.read()

            if not realm or not challenge:
                _LOGGER.warning("Keenetic auth endpoint returned 401 without an X-NDM challenge")
                raise InvalidAuth("The router returned an incomplete authentication challenge")

            password_hash = hashlib.md5(
                f"{self._username}:{realm}:{self._password}".encode("utf-8")
            ).hexdigest()
            response_password = hashlib.sha256(
                f"{challenge}{password_hash}".encode("utf-8")
            ).hexdigest()
            async with self._session.post(
                auth_url,
                json={"login": self._username, "password": response_password},
                timeout=self._timeout,
                ssl=self._ssl,
                allow_redirects=False,
                cookies=self._cookie_jar.filter_cookies(URL(auth_url)),
            ) as response:
                self._cookie_jar.update_cookies(response.cookies, response.url)
                await response.read()
                if response.status == 401:
                    _LOGGER.warning("Keenetic rejected credentials during challenge authentication")
                    raise InvalidAuth("The router rejected the configured credentials")
                if not 200 <= response.status < 300:
                    raise RciCommandError(
                        f"The router returned HTTP {response.status} during authentication"
                    )

    @staticmethod
    async def _read_response(response: Any, *, allow_text: bool = False) -> Any:
        if response.status == 401:
            await response.read()
            raise InvalidAuth("The router rejected the configured credentials")
        if response.status in (404, 405):
            await response.read()
            raise UnsupportedResponse(f"The router does not expose this RCI resource (HTTP {response.status})")
        if response.status >= 400:
            await response.read()
            raise RciCommandError(f"The router returned HTTP {response.status}")

        content_type = response.headers.get("Content-Type", "").lower()
        if "json" not in content_type and response.status != 204:
            if allow_text:
                return await response.text()
            await response.read()
            raise UnsupportedResponse("The host did not return JSON from the RCI interface")
        if response.status == 204:
            return None
        try:
            result = await response.json(content_type=None)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as err:
            if allow_text:
                return await response.text()
            raise UnsupportedResponse("The router returned invalid JSON") from err
        code = KeeneticApi._rci_error_code(result)
        if code is not None:
            raise RciCommandError(f"The router rejected the command (code {code})")
        return result

    @staticmethod
    def _rci_error_code(value: Any) -> str | None:
        if isinstance(value, Mapping):
            status = value.get("status")
            if isinstance(status, str) and status.lower() == "error":
                code = value.get("code")
                return str(code) if code is not None and str(code).isdigit() else "unknown"
            for child in value.values():
                code = KeeneticApi._rci_error_code(child)
                if code is not None:
                    return code
        elif isinstance(value, list):
            for child in value:
                code = KeeneticApi._rci_error_code(child)
                if code is not None:
                    return code
        return None
