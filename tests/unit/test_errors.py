from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.errors import (
    AppError,
    OpenBaoError,
    ProviderNotFoundError,
    register_exception_handlers,
)


def _app_with_routes() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/missing-provider")
    async def missing_provider() -> None:
        raise ProviderNotFoundError("Provider github was not found")

    @app.get("/openbao")
    async def openbao_failed() -> None:
        raise OpenBaoError("upstream said access_token=super-secret")

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("client_secret=should-not-leak")

    return app


def test_app_error_is_sanitized_json() -> None:
    with TestClient(_app_with_routes()) as client:
        response = client.get("/missing-provider")
    assert response.status_code == 404
    assert response.json() == {
        "error": "provider_not_found",
        "message": "Provider github was not found",
    }


def test_openbao_error_redacts_secrets_and_keeps_a_small_body() -> None:
    with TestClient(_app_with_routes(), raise_server_exceptions=False) as client:
        response = client.get("/openbao")
    assert response.status_code == 502
    body = response.json()
    assert body["error"] == "openbao_error"
    assert "super-secret" not in response.text
    assert set(body) == {"error", "message"}


def test_unexpected_errors_do_not_leak_exception_text() -> None:
    with TestClient(_app_with_routes(), raise_server_exceptions=False) as client:
        response = client.get("/boom")
    assert response.status_code == 500
    assert response.json() == {
        "error": "internal_error",
        "message": "An unexpected error occurred",
    }
    assert "should-not-leak" not in response.text


def test_app_error_default_message() -> None:
    err = AppError()
    assert err.status_code == 500
    assert err.code == "internal_error"
