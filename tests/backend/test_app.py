"""Offline HTTP boundary tests; no database fixtures or model calls."""

import os
import subprocess
import sys

import pytest
from fastapi import HTTPException
from fastapi.openapi.models import OpenAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel, ValidationError

from easytravel.backend.config import ConfigurationError, Settings, load_settings
from easytravel.backend.domain.errors import BusinessConflict, BusinessError, ResourceNotFound
from easytravel.backend.main import create_app


SECRET = "postgresql://user:private-password@internal/db?api_key=secret"


@pytest.fixture
def app():
    return create_app(Settings(environment="test"))


def test_factory_isolated_configuration(monkeypatch):
    monkeypatch.setenv("EASYTRAVEL_APP_ENVIRONMENT", "local")
    local = create_app()
    test = create_app(Settings(environment="test"))
    assert local is not test
    assert local.state.settings.environment == "local"
    assert test.state.settings.environment == "test"
    assert local.debug is test.debug is False


def test_health(app):
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "alive", "scope": "http_application_liveness",
        "dependencies": {"database": "not_checked", "model": "not_checked", "travel_data": "not_checked"},
    }


def test_openapi_and_docs(app):
    with TestClient(app) as client:
        response = client.get("/openapi.json")
        assert client.get("/docs").status_code == 200
    assert response.status_code == 200
    schema = response.json()
    OpenAPI.model_validate(schema)
    assert schema == app.openapi()
    assert schema["openapi"].startswith("3.1.")
    assert set(schema["paths"]) == {"/api/v1/health", "/api/v1/trips", "/api/v1/trips/{trip_id}",
        "/api/v1/trips/{trip_id}/requirements", "/api/v1/trips/{trip_id}/clarifications/next",
        "/api/v1/trips/{trip_id}/clarifications/{question_id}/answers"}
    assert schema["paths"]["/api/v1/health"]["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HealthResponse"}
    assert "ErrorResponse" in schema["components"]["schemas"]


@pytest.mark.parametrize("error,status,code,message", [
    (ResourceNotFound, 404, "resource_not_found", "Resource not found."),
    (BusinessConflict, 409, "business_conflict", "Operation conflicts with current state."),
    (BusinessError, 500, "internal_error", "Internal server error."),
    (RuntimeError, 500, "internal_error", "Internal server error."),
])
def test_safe_exception_mapping(app, error, status, code, message):
    @app.get("/test-error")
    async def fail():
        raise error(SECRET)

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/test-error")
    assert response.status_code == status
    assert response.json() == {"error": {"code": code, "message": message}}
    assert SECRET not in response.text
    assert "Traceback" not in response.text


def test_safe_http_exception_and_missing_route(app):
    @app.get("/test-http-error")
    async def fail():
        raise HTTPException(403, detail=SECRET)

    with TestClient(app) as client:
        response = client.get("/test-http-error")
        assert response.status_code == 403
        assert response.json() == {"error": {"code": "http_error", "message": "HTTP request failed."}}
        assert client.get("/missing").status_code == 404


class Input(BaseModel):
    count: int


def test_validation_does_not_echo_input(app):
    @app.post("/test-input")
    async def receive(body: Input):
        return body

    with TestClient(app) as client:
        response = client.post("/test-input", json={"count": SECRET})
    assert response.status_code == 422
    assert response.json() == {"error": {"code": "invalid_request", "message": "Request validation failed."}}


@pytest.mark.parametrize("values,field", [
    ({}, "environment"),
    ({"EASYTRAVEL_APP_ENVIRONMENT": ""}, "environment"),
    ({"EASYTRAVEL_APP_ENVIRONMENT": "production"}, "environment"),
    ({"EASYTRAVEL_APP_ENVIRONMENT": SECRET}, "environment"),
    ({"EASYTRAVEL_APP_ENVIRONMENT": "test", "EASYTRAVEL_APP_DEBUG": SECRET}, "debug"),
])
def test_bad_config_fails_explicitly_without_values(values, field):
    with pytest.raises(ConfigurationError) as raised:
        load_settings(values)
    assert field in str(raised.value)
    assert SECRET not in str(raised.value)
    assert raised.value.__suppress_context__


def test_factory_rejects_bad_environment(monkeypatch):
    monkeypatch.setenv("EASYTRAVEL_APP_ENVIRONMENT", "production")
    with pytest.raises(ConfigurationError):
        create_app()


def test_settings_reject_extra_fields_and_mutation():
    with pytest.raises(ValidationError):
        Settings(environment="test", debug=True)
    settings = Settings(environment="test")
    with pytest.raises(ValidationError):
        settings.environment = "local"


def test_import_factory_and_http_need_no_external_adapters():
    # Fresh interpreter blocks network and imports of all experiment/external adapters.
    script = '''
import sys
class BlockAdapters:
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'psycopg', 'langgraph'} or fullname.startswith((
            'easytravel.spike02', 'easytravel.spike03', 'easytravel.m000',
            'easytravel.model_adapter', 'easytravel.application')):
            raise AssertionError('Unexpected adapter import: ' + fullname)
sys.meta_path.insert(0, BlockAdapters())
import socket
original_connect = socket.socket.connect
original_socketpair = socket.socketpair
def no_network(*args, **kwargs):
    raise AssertionError('Unexpected network access')
def event_loop_socketpair(*args, **kwargs):
    # Windows asyncio uses a loopback socketpair for its internal wakeup pipe.
    socket.socket.connect = original_connect
    try:
        return original_socketpair(*args, **kwargs)
    finally:
        socket.socket.connect = no_network
socket.socketpair = event_loop_socketpair
socket.socket.connect = no_network
from easytravel.backend.main import create_app
from easytravel.backend.config import Settings
from fastapi.testclient import TestClient
with TestClient(create_app(Settings(environment='test'))) as client:
    assert client.get('/api/v1/health').status_code == 200
    assert client.get('/openapi.json').status_code == 200
'''
    result = subprocess.run([sys.executable, "-c", script], env={
        key: value for key, value in os.environ.items()
        if not key.startswith(("EASYTRAVEL_", "LANGGRAPH_"))
    }, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
