"""Explicit process configuration without loading experiment credentials."""

import os
from collections.abc import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError


class ConfigurationError(ValueError):
    """Safe startup diagnostic, containing field names but never input values."""


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    environment: Literal["local", "test"]


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    source = os.environ if environ is None else environ
    prefix = "EASYTRAVEL_APP_"
    values = {key[len(prefix):].lower(): value for key, value in source.items()
              if key.startswith(prefix)}
    try:
        return Settings.model_validate(values)
    except ValidationError as exc:
        fields = sorted({str(error["loc"][0]) for error in exc.errors()})
        raise ConfigurationError(
            "Invalid application configuration: " + ", ".join(fields)
            + ". Set EASYTRAVEL_APP_ENVIRONMENT to local or test; "
            "other EASYTRAVEL_APP_* variables are unsupported."
        ) from None
