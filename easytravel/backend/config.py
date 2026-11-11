"""Explicit process configuration without loading experiment credentials."""

import os
import json
import re
from collections.abc import Mapping
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError, model_validator


class ConfigurationError(ValueError):
    """Safe startup diagnostic, containing field names but never input values."""


class LocalIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)
    actor: str
    token: SecretStr

    @model_validator(mode="after")
    def valid(self):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", self.actor):
            raise ValueError("invalid local actor")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", self.token.get_secret_value()):
            raise ValueError("use a server-generated token with at least 256 bits of entropy")
        return self


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, hide_input_in_errors=True)

    environment: Literal["local", "test"]
    local_business_enabled: bool = False
    local_identities: tuple[LocalIdentity, ...] = ()
    local_allowed_hosts: tuple[str, ...] = ("127.0.0.1:8000", "localhost:8000", "[::1]:8000")
    local_allowed_origins: tuple[str, ...] = ("http://127.0.0.1:5173", "http://127.0.0.1:8000")

    @model_validator(mode="after")
    def local_only(self):
        if self.local_business_enabled and not self.local_identities:
            raise ValueError("enabled local business APIs require identities")
        tokens = [identity.token.get_secret_value() for identity in self.local_identities]
        if len(set(tokens)) != len(tokens) or len({i.actor for i in self.local_identities}) != len(self.local_identities):
            raise ValueError("local identities must be unique")
        if not self.local_allowed_hosts or not self.local_allowed_origins:
            raise ValueError("explicit local hosts and origins required")
        for value in self.local_allowed_hosts:
            url = urlsplit("http://" + value)
            if (url.hostname not in {"127.0.0.1", "localhost", "::1"} or url.port is None
                    or url.username or url.password or url.path or url.query or url.fragment):
                raise ValueError("only explicit loopback hosts allowed")
        for value in self.local_allowed_origins:
            url = urlsplit(value)
            if (url.scheme not in {"http", "https"} or url.hostname not in {"127.0.0.1", "localhost", "::1"}
                    or url.port is None or url.username or url.password or url.path or url.query or url.fragment):
                raise ValueError("only explicit loopback origins allowed")
        return self


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    source = os.environ if environ is None else environ
    prefix = "EASYTRAVEL_APP_"
    values = {key[len(prefix):].lower(): value for key, value in source.items()
              if key.startswith(prefix)}
    try:
        if "local_business_enabled" in values:
            if values["local_business_enabled"] not in {"true", "false"}:
                raise ConfigurationError("Invalid application configuration: local_business_enabled")
            values["local_business_enabled"] = values["local_business_enabled"] == "true"
        for field in ("local_identities", "local_allowed_hosts", "local_allowed_origins"):
            if field in values:
                try:
                    parsed = json.loads(values[field])
                    if not isinstance(parsed, list):
                        raise ValueError()
                    values[field] = tuple(LocalIdentity.model_validate(v) for v in parsed) if field == "local_identities" else tuple(parsed)
                except (ValueError, TypeError):
                    raise ConfigurationError("Invalid application configuration: " + field) from None
        return Settings.model_validate(values)
    except ValidationError as exc:
        fields = sorted({str(error["loc"][0]) if error["loc"] else "local_identity_policy" for error in exc.errors()})
        raise ConfigurationError(
            "Invalid application configuration: " + ", ".join(fields)
            + ". Set EASYTRAVEL_APP_ENVIRONMENT to local or test; local business APIs require explicit local identity configuration."
        ) from None
