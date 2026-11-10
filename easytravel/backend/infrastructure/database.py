"""Explicit psycopg connection scope. No implicit environment loading or migrations."""
from contextlib import asynccontextmanager
from collections.abc import Mapping
import os

import psycopg
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, SecretStr, field_validator


class DatabaseUnavailable(RuntimeError):
    """Safe diagnostic; raw driver details must not reach public responses/logs."""


class DatabaseSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)
    url: SecretStr

    @field_validator("url")
    @classmethod
    def explicit_target(cls, value):
        try:
            config = conninfo_to_dict(value.get_secret_value())
        except psycopg.ProgrammingError:
            raise ValueError("invalid database connection configuration") from None
        if (not all(config.get(k) for k in ("host", "port", "dbname", "user", "password"))
                or config["dbname"] in {"postgres", "template0", "template1", "easytravel_env00_test"}
                or any(k in config for k in ("service", "servicefile", "options", "hostaddr"))):
            raise ValueError("explicit dedicated database required; experiment targets are forbidden")
        return value


def load_database_settings(environ: Mapping[str, str] | None = None) -> DatabaseSettings:
    source = os.environ if environ is None else environ
    value = source.get("EASYTRAVEL_DATABASE_URL")
    if not value:
        raise ValueError("EASYTRAVEL_DATABASE_URL is required")
    return DatabaseSettings(url=value)


@asynccontextmanager
async def connection(settings: DatabaseSettings):
    dsn = make_conninfo(settings.url.get_secret_value(), connect_timeout=5,
                        application_name="easytravel-backend",
                        options="-c search_path=pg_catalog -c statement_timeout=10000 -c lock_timeout=3000")
    try:
        conn = await psycopg.AsyncConnection.connect(dsn, autocommit=True, row_factory=dict_row)
    except psycopg.OperationalError:
        raise DatabaseUnavailable("Database connection failed") from None
    try:
        yield conn
    except psycopg.OperationalError:
        raise DatabaseUnavailable("Database operation failed") from None
    finally:
        await conn.close()
