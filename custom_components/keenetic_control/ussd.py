"""Helpers for completing asynchronous USSD requests."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any


async def async_complete_ussd(
    send: Callable[[float], Awaitable[Any]],
    poll: Callable[[float], Awaitable[Any]],
    *,
    timeout: float,
    poll_interval: float = 1,
) -> Any:
    """Send one USSD request and poll its delayed result until the shared deadline."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    async def within_deadline(request: Callable[[float], Awaitable[Any]]) -> Any:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        return await asyncio.wait_for(request(remaining), timeout=remaining)

    result = await within_deadline(send)
    while isinstance(result, Mapping) and result.get("continued") is True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise asyncio.TimeoutError
        await asyncio.sleep(min(poll_interval, remaining))
        result = await within_deadline(poll)
    return result
