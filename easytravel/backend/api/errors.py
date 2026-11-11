from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from easytravel.backend.domain.errors import (
    BusinessConflict, BusinessError, ResourceNotFound, CommandInProgress,
    DependencyUnavailable, IdempotencyKeyReused,
)


class LocalApiError(Exception):
    """Only static, allowlisted diagnostics can be returned to the client."""
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code


LOCAL_MESSAGES = {
    "authentication_required": "A valid local development credential is required.",
    "origin_not_allowed": "Request origin is not allowed.",
    "local_only_api": "Business APIs require an enabled trusted loopback environment.",
    "invalid_request": "Request validation failed.",
    "business_dependency_unavailable": "Business dependency is unavailable.",
}


async def local_error_handler(request: Request, exc: LocalApiError):
    if exc.code not in LOCAL_MESSAGES or exc.status not in {401, 403, 422, 503}:
        return error_response(500, "internal_error", "Internal server error.")
    return error_response(exc.status, exc.code, LOCAL_MESSAGES[exc.code])


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


def error_response(status: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status, content=body.model_dump())


async def business_error_handler(request: Request, exc: BusinessError) -> JSONResponse:
    if isinstance(exc, DependencyUnavailable):
        return error_response(503, "business_dependency_unavailable", "Business dependency is unavailable.")
    if isinstance(exc, IdempotencyKeyReused):
        return error_response(409, "idempotency_key_reused", "Idempotency key was used for a different request.")
    if isinstance(exc, CommandInProgress):
        response = error_response(409, "command_in_progress", "Command is still in progress; retry with the same key.")
        response.headers["Retry-After"] = "1"
        return response
    if isinstance(exc, ResourceNotFound):
        return error_response(404, "resource_not_found", "Resource not found.")
    if isinstance(exc, BusinessConflict):
        return error_response(409, "business_conflict", "Operation conflicts with current state.")
    return error_response(500, "internal_error", "Internal server error.")


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # Pydantic errors can contain the raw body and validator exception text.
    return error_response(422, "invalid_request", "Request validation failed.")


async def http_error_handler(request: Request, exc: HTTPException) -> JSONResponse:
    # Do not echo arbitrary exception detail or headers supplied by adapters.
    return error_response(exc.status_code, "http_error", "HTTP request failed.")


async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
    return error_response(500, "internal_error", "Internal server error.")


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(LocalApiError, local_error_handler)
    app.add_exception_handler(BusinessError, business_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(HTTPException, http_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
