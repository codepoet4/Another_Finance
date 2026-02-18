"""Config flow for iCloud Garage Automation."""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    CONF_AVG_SPEED_KPH,
    CONF_GARAGE_DOOR,
    CONF_HOME_ZONE,
    CONF_MOTION_SENSOR,
    CONF_MY_DEVICE,
    CONF_MY_NOTIFY,
    CONF_NOTIFICATION_TARGET,
    CONF_WIFE_DEVICE,
    CONF_WIFE_NOTIFY,
    DEFAULT_AVG_SPEED_KPH,
    DEFAULT_HOME_ZONE,
    DOMAIN,
)

_MY_DEVICE_DEFAULT = "device_tracker.rpip"
_WIFE_DEVICE_DEFAULT = "device_tracker.shyiphonexs"


class ICloudGarageConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle user-initiated setup of iCloud Garage Automation."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> config_entries.FlowResult:
        """Present the setup form and create the entry on submission."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Basic validation: ensure chosen entities actually exist
            for field, entity_id in (
                (CONF_MY_DEVICE, user_input.get(CONF_MY_DEVICE, "")),
                (CONF_WIFE_DEVICE, user_input.get(CONF_WIFE_DEVICE, "")),
                (CONF_GARAGE_DOOR, user_input.get(CONF_GARAGE_DOOR, "")),
                (CONF_MOTION_SENSOR, user_input.get(CONF_MOTION_SENSOR, "")),
            ):
                if not entity_id:
                    errors[field] = "required"

            if not errors:
                # Prevent duplicate entries
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()

                return self.async_create_entry(
                    title="iCloud Garage Automation",
                    data=user_input,
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_MY_DEVICE,
                    description={"suggested_value": _MY_DEVICE_DEFAULT},
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="device_tracker")
                ),
                vol.Required(
                    CONF_WIFE_DEVICE,
                    description={"suggested_value": _WIFE_DEVICE_DEFAULT},
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="device_tracker")
                ),
                vol.Required(CONF_GARAGE_DOOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="cover")
                ),
                vol.Required(CONF_MOTION_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["binary_sensor", "sensor"]
                    )
                ),
                # Alert notify service (your iPhone only), e.g. notify.mobile_app_rpip
                vol.Required(CONF_NOTIFICATION_TARGET): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT
                    )
                ),
                # Per-phone location-request notify services.
                # Leave blank to auto-derive from device_tracker name:
                #   device_tracker.rpip  →  notify.mobile_app_rpip
                vol.Optional(CONF_MY_NOTIFY): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT
                    )
                ),
                vol.Optional(CONF_WIFE_NOTIFY): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT
                    )
                ),
                vol.Optional(
                    CONF_HOME_ZONE,
                    description={"suggested_value": DEFAULT_HOME_ZONE},
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="zone")
                ),
                vol.Optional(
                    CONF_AVG_SPEED_KPH,
                    default=DEFAULT_AVG_SPEED_KPH,
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=10,
                        max=130,
                        step=5,
                        unit_of_measurement="km/h",
                        mode=selector.NumberSelectorMode.SLIDER,
                    )
                ),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "my_default": _MY_DEVICE_DEFAULT,
                "wife_default": _WIFE_DEVICE_DEFAULT,
            },
        )

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> "ICloudGarageOptionsFlow":
        """Return the options flow handler."""
        return ICloudGarageOptionsFlow(config_entry)


class ICloudGarageOptionsFlow(config_entries.OptionsFlow):
    """Allow editing configuration after initial setup."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> config_entries.FlowResult:
        """Show options form pre-filled with current values."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._entry.data

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_MY_DEVICE,
                    default=current.get(CONF_MY_DEVICE, _MY_DEVICE_DEFAULT),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="device_tracker")
                ),
                vol.Required(
                    CONF_WIFE_DEVICE,
                    default=current.get(CONF_WIFE_DEVICE, _WIFE_DEVICE_DEFAULT),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="device_tracker")
                ),
                vol.Required(
                    CONF_GARAGE_DOOR,
                    default=current.get(CONF_GARAGE_DOOR, ""),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="cover")
                ),
                vol.Required(
                    CONF_MOTION_SENSOR,
                    default=current.get(CONF_MOTION_SENSOR, ""),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(
                        domain=["binary_sensor", "sensor"]
                    )
                ),
                vol.Required(
                    CONF_NOTIFICATION_TARGET,
                    default=current.get(CONF_NOTIFICATION_TARGET, ""),
                ): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT
                    )
                ),
                vol.Optional(
                    CONF_MY_NOTIFY,
                    default=current.get(CONF_MY_NOTIFY, ""),
                ): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT
                    )
                ),
                vol.Optional(
                    CONF_WIFE_NOTIFY,
                    default=current.get(CONF_WIFE_NOTIFY, ""),
                ): selector.TextSelector(
                    selector.TextSelectorConfig(
                        type=selector.TextSelectorType.TEXT
                    )
                ),
                vol.Optional(
                    CONF_HOME_ZONE,
                    default=current.get(CONF_HOME_ZONE, DEFAULT_HOME_ZONE),
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="zone")
                ),
                vol.Optional(
                    CONF_AVG_SPEED_KPH,
                    default=current.get(CONF_AVG_SPEED_KPH, DEFAULT_AVG_SPEED_KPH),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=10,
                        max=130,
                        step=5,
                        unit_of_measurement="km/h",
                        mode=selector.NumberSelectorMode.SLIDER,
                    )
                ),
            }
        )

        return self.async_show_form(step_id="init", data_schema=schema)
