"""Typed per-entry runtime shared by platforms and actions."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .api import KeeneticError
from .model import has_sms_service

if TYPE_CHECKING:
    from .api import KeeneticApi
    from .coordinator import KeeneticCoordinator

type KeeneticConfigEntry = ConfigEntry[Runtime]


class Runtime:
    """Objects shared by all entities and actions for one router."""

    def __init__(
        self,
        *,
        entry: KeeneticConfigEntry,
        api: KeeneticApi,
        coordinator: KeeneticCoordinator,
    ) -> None:
        self.entry = entry
        self.api = api
        self.coordinator = coordinator
        # Delayed USSD replies belong to the router's shared HTTP session.
        self.ussd_lock = asyncio.Lock()

    @property
    def sms_modems(self) -> set[str]:
        return {
            name
            for name, details in self.coordinator.data.get("modems", {}).items()
            if has_sms_service(details)
        }

    async def _send_sms(self, interface: str, message: str, recipient: str) -> None:
        if (
            interface not in self.sms_modems
            or "sms" not in self.coordinator.data["modems"][interface]
        ):
            raise ServiceValidationError(
                "The selected SMS modem is currently unavailable"
            )
        if not message.strip():
            raise ServiceValidationError("SMS message cannot be empty")
        try:
            await self.api.async_post(
                "sms/send",
                {"interface": interface, "to": recipient, "message": message},
            )
        except KeeneticError as err:
            raise HomeAssistantError(
                "The SMS send result is not confirmed; the router may have accepted the request"
            ) from err
