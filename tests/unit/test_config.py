from pydantic import SecretStr

from app.config import Settings


def test_settings_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.openbao_addr == "http://127.0.0.1:8200"
    assert settings.openbao_mount == "oauth2"
    assert settings.redirect_url.endswith("/callback")
    assert settings.http_timeout_seconds == 5.0
    assert isinstance(settings.openbao_token, SecretStr)


def test_openbao_token_is_not_in_repr() -> None:
    settings = Settings(openbao_token=SecretStr("root-token-value"), _env_file=None)
    assert "root-token-value" not in repr(settings)
    assert settings.openbao_token.get_secret_value() == "root-token-value"
