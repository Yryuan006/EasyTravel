from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from easytravel.backend.domain.errors import (
    BusinessConflict, BusinessError, ResourceNotFound,
)


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorDetail


def error_response(status: int, code: str, message: str) -> JSONResponse:
    body = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status, content=body.model_dump())


async def business_error_handler(request: Request, exc: BusinessError) -> JSONResponse:
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
    app.add_exception_handler(BusinessError, business_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(HTTPException, http_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
