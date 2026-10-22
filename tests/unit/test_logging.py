import logging

from app.core.logging import RedactingFilter, configure_logging, redact_text, redact_value


def test_redact_text_masks_tokens_and_secrets() -> None:
    raw = "access_token=abc123 client_secret=shh code=oauthcode token=xyz"
    redacted = redact_text(raw)
    assert "abc123" not in redacted
    assert "shh" not in redacted
    assert "oauthcode" not in redacted
    assert "xyz" not in redacted
    assert "[REDACTED]" in redacted


def test_redact_value_masks_sensitive_keys() -> None:
    payload = {
        "access_token": "tok_live",
        "nested": {"client_secret": "super-secret"},
        "ok": "provider-github",
    }
    redacted = redact_value(payload)
    assert redacted["access_token"] == "[REDACTED]"
    assert redacted["nested"]["client_secret"] == "[REDACTED]"
    assert redacted["ok"] == "provider-github"


def test_log_filter_redacts_message(caplog) -> None:
    configure_logging("INFO")
    logger = logging.getLogger("aggregator.test")
    with caplog.at_level(logging.INFO, logger="aggregator.test"):
        logger.info("fetched access_token=super-secret-value")
    assert "super-secret-value" not in caplog.text
    assert "[REDACTED]" in caplog.text


def test_redacting_filter_handles_args() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="payload %s",
        args=("access_token=secret-token",),
        exc_info=None,
    )
    assert RedactingFilter().filter(record) is True
    assert record.args == ("access_token=[REDACTED]",)
    assert "secret-token" not in str(record.args)
