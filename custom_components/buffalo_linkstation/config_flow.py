"""Config flow for Buffalo LinkStation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    BuffaloAuthenticationError,
    BuffaloConnectionError,
    BuffaloIdentity,
    BuffaloInvalidResponseError,
    BuffaloJsonRpcError,
    BuffaloLinkStationApi,
    BuffaloTimeoutError,
    extract_identity,
    normalize_host,
)
from .const import (
    CONF_DEVICE_ID,
    CONF_ENABLE_REBOOT,
    CONF_ENABLE_SHUTDOWN,
    CONF_TIMEOUT,
    CONF_UPDATE_INTERVAL,
    DEFAULT_TIMEOUT,
    DEFAULT_UPDATE_INTERVAL,
    DOMAIN,
    MAX_TIMEOUT,
    MAX_UPDATE_INTERVAL,
    MIN_TIMEOUT,
    MIN_UPDATE_INTERVAL,
    is_verified_shutdown,
)


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Validated identity returned by the NAS."""

    identity: BuffaloIdentity
    unique_id: str


def _number(minimum: int, maximum: int) -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(
            min=minimum,
            max=maximum,
            step=1,
            mode=NumberSelectorMode.BOX,
        )
    )


def _connection_schema(defaults: dict[str, Any] | None = None) -> vol.Schema:
    defaults = defaults or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_HOST, default=defaults.get(CONF_HOST, "")
            ): TextSelector(),
            vol.Required(
                CONF_USERNAME, default=defaults.get(CONF_USERNAME, "admin")
            ): TextSelector(),
            vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
            vol.Optional(
                CONF_UPDATE_INTERVAL,
                default=defaults.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
            ): _number(MIN_UPDATE_INTERVAL, MAX_UPDATE_INTERVAL),
            vol.Optional(
                CONF_TIMEOUT, default=defaults.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
            ): _number(MIN_TIMEOUT, MAX_TIMEOUT),
        }
    )


def _options_schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_UPDATE_INTERVAL,
                default=defaults.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
            ): _number(MIN_UPDATE_INTERVAL, MAX_UPDATE_INTERVAL),
            vol.Required(
                CONF_TIMEOUT, default=defaults.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)
            ): _number(MIN_TIMEOUT, MAX_TIMEOUT),
            vol.Required(
                CONF_ENABLE_SHUTDOWN,
                default=defaults.get(CONF_ENABLE_SHUTDOWN, False),
            ): bool,
            vol.Required(
                CONF_ENABLE_REBOOT,
                default=defaults.get(CONF_ENABLE_REBOOT, False),
            ): bool,
        }
    )


async def _async_validate(
    hass: HomeAssistant, user_input: dict[str, Any]
) -> ValidationResult:
    host = normalize_host(user_input[CONF_HOST])
    api = BuffaloLinkStationApi(
        async_get_clientsession(hass),
        host,
        user_input[CONF_USERNAME],
        user_input[CONF_PASSWORD],
        timeout=float(user_input.get(CONF_TIMEOUT, DEFAULT_TIMEOUT)),
        read_attempts=1,
    )
    await api.login()
    identity = extract_identity(await api.get_system_info(), host)
    unique_id = (
        f"serial:{identity.serial_number.casefold()}"
        if identity.serial_number
        else f"host:{host}"
    )
    return ValidationResult(identity, unique_id)


def _error_key(err: Exception) -> str:
    if isinstance(err, BuffaloAuthenticationError):
        return "invalid_auth"
    if isinstance(err, BuffaloTimeoutError):
        return "timeout"
    return "cannot_connect"


class BuffaloLinkStationConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a Buffalo LinkStation through the UI."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate credentials and create one entry per NAS."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                user_input[CONF_HOST] = normalize_host(user_input[CONF_HOST])
                result = await _async_validate(self.hass, user_input)
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
            except (
                BuffaloAuthenticationError,
                BuffaloConnectionError,
                BuffaloTimeoutError,
                BuffaloInvalidResponseError,
                BuffaloJsonRpcError,
            ) as err:
                errors["base"] = _error_key(err)
            else:
                await self.async_set_unique_id(result.unique_id)
                self._abort_if_unique_id_configured()
                data = dict(user_input)
                data[CONF_DEVICE_ID] = result.unique_id
                model = result.identity.model or "Buffalo LinkStation"
                title = result.identity.hostname or model
                return self.async_create_entry(title=title, data=data)
        return self.async_show_form(
            step_id="user",
            data_schema=_connection_schema(user_input),
            errors=errors,
        )

    async def async_step_reauth(self, _entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Begin reauthentication for an existing entry."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update rejected credentials after verifying them."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            merged = {**entry.data, **user_input}
            try:
                result = await _async_validate(self.hass, merged)
            except (
                ValueError,
                BuffaloAuthenticationError,
                BuffaloConnectionError,
                BuffaloTimeoutError,
                BuffaloInvalidResponseError,
                BuffaloJsonRpcError,
            ) as err:
                errors["base"] = (
                    _error_key(err)
                    if not isinstance(err, ValueError)
                    else "invalid_host"
                )
            else:
                if result.unique_id != entry.data.get(CONF_DEVICE_ID, entry.unique_id):
                    errors["base"] = "wrong_device"
                else:
                    return self.async_update_reload_and_abort(entry, data=merged)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME, default=entry.data[CONF_USERNAME]
                    ): TextSelector(),
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change host and credentials while protecting stable identity."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        defaults = {**entry.data, **entry.options}
        if user_input is not None:
            try:
                user_input[CONF_HOST] = normalize_host(user_input[CONF_HOST])
                result = await _async_validate(self.hass, user_input)
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
            except (
                BuffaloAuthenticationError,
                BuffaloConnectionError,
                BuffaloTimeoutError,
                BuffaloInvalidResponseError,
                BuffaloJsonRpcError,
            ) as err:
                errors["base"] = _error_key(err)
            else:
                original_id = entry.data.get(CONF_DEVICE_ID, entry.unique_id)
                if (
                    original_id
                    and original_id.startswith("serial:")
                    and result.unique_id != original_id
                ):
                    errors["base"] = "wrong_device"
                elif any(
                    other.entry_id != entry.entry_id
                    and other.unique_id == result.unique_id
                    for other in self.hass.config_entries.async_entries(DOMAIN)
                ):
                    errors["base"] = "already_configured"
                else:
                    data = dict(user_input)
                    data[CONF_DEVICE_ID] = result.unique_id
                    options = {
                        **entry.options,
                        CONF_UPDATE_INTERVAL: data.pop(CONF_UPDATE_INTERVAL),
                        CONF_TIMEOUT: data.pop(CONF_TIMEOUT),
                    }
                    self.hass.config_entries.async_update_entry(
                        entry, unique_id=result.unique_id, options=options
                    )
                    return self.async_update_reload_and_abort(entry, data=data)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_connection_schema(defaults),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        _config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Return the polling and dangerous-control options flow."""
        return BuffaloLinkStationOptionsFlow()


class BuffaloLinkStationOptionsFlow(config_entries.OptionsFlow):
    """Manage polling and explicit model compatibility overrides."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update options and reload the entry."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        defaults = {
            **self.config_entry.data,
            **self.config_entry.options,
        }
        if CONF_ENABLE_SHUTDOWN not in defaults:
            runtime_data = getattr(self.config_entry, "runtime_data", None)
            model = firmware = None
            if runtime_data is not None:
                identity = runtime_data.coordinator.data.identity
                model = identity.model
                firmware = identity.firmware
            defaults[CONF_ENABLE_SHUTDOWN] = is_verified_shutdown(model, firmware)
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(defaults)
        )
