from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import redact_text


class AppError(Exception):
    status_code = 500
    code = "internal_error"

    def __init__(self, message: str = "An unexpected error occurred") -> None:
        super().__init__(message)
        self.message = message


class ProviderNotFoundError(AppError):
    status_code = 404
    code = "provider_not_found"


class ProviderAlreadyExistsError(AppError):
    status_code = 409
    code = "provider_already_exists"


class InvalidOAuthStateError(AppError):
    status_code = 400
    code = "invalid_oauth_state"


class RequestNotFoundError(AppError):
    status_code = 404
    code = "request_not_found"


class OpenBaoError(AppError):
    status_code = 502
    code = "openbao_error"

    def __init__(self, message: str = "OpenBao request failed") -> None:
        super().__init__(message)


class OpenBaoUnavailableError(OpenBaoError):
    status_code = 503
    code = "openbao_unavailable"

    def __init__(self, message: str = "OpenBao is unavailable") -> None:
        super().__init__(message)


class OpenBaoNotFoundError(OpenBaoError):
    status_code = 404
    code = "openbao_not_found"

    def __init__(self, message: str = "OpenBao resource not found") -> None:
        super().__init__(message)


def error_body(code: str, message: str) -> dict[str, str]:
    return {"error": code, "message": message}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(exc.code, redact_text(exc.message)),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, StarletteHTTPException):
            return await http_exception_handler(request, exc)
        if isinstance(exc, RequestValidationError):
            return await request_validation_exception_handler(request, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("internal_error", "An unexpected error occurred"),
        )
