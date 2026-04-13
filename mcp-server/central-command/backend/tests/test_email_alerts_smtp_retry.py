"""Unit tests for _send_smtp_with_retry (Phase 14 T2 closing — A-spec).

Original round-table audit graded the notifier C+ specifically because
'SMTP retry not tested.' This file closes that gap.

Covers:
  - Single send succeeds → True, no retries
  - Transient SMTP failure on first attempt → retry, succeeds → True
  - Persistent SMTP failure → exhaust max_retries → False
  - Backoff is exponential (2^attempt seconds)
  - Logger emits warning per retry, error on final
  - max_retries parameter is honored
  - OSError (connection-level) treated like SMTPException
"""
from __future__ import annotations

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from unittest.mock import MagicMock, patch

import pytest


def _make_msg() -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["From"] = "test@example.com"
    msg["To"] = "user@example.com"
    msg["Subject"] = "Phase 14 T2 retry test"
    return msg


# ─── Happy path ───────────────────────────────────────────────────


def test_first_attempt_success_returns_true_no_retries():
    from email_alerts import _send_smtp_with_retry
    fake_server = MagicMock()
    fake_smtp = MagicMock()
    fake_smtp.__enter__ = MagicMock(return_value=fake_server)
    fake_smtp.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", return_value=fake_smtp) as smtp_class, \
         patch("time.sleep") as sleeper:
        result = _send_smtp_with_retry(_make_msg(), ["a@b.com"], "test", max_retries=3)

    assert result is True
    # smtplib.SMTP called exactly once on success
    assert smtp_class.call_count == 1
    # No backoff sleep on success
    assert sleeper.call_count == 0
    # Verify the SMTP-protocol calls
    fake_server.starttls.assert_called_once()
    fake_server.login.assert_called_once()
    fake_server.sendmail.assert_called_once()


# ─── Transient failure → retry → success ──────────────────────────


def test_transient_failure_then_success_logs_warning():
    from email_alerts import _send_smtp_with_retry
    fake_server = MagicMock()
    fake_smtp_ctx = MagicMock()
    fake_smtp_ctx.__enter__ = MagicMock(return_value=fake_server)
    fake_smtp_ctx.__exit__ = MagicMock(return_value=False)

    # First call raises SMTPException, second succeeds
    side_effects = [
        smtplib.SMTPException("transient connection reset"),
        fake_smtp_ctx,
    ]
    with patch("smtplib.SMTP", side_effect=side_effects) as smtp_class, \
         patch("time.sleep") as sleeper:
        result = _send_smtp_with_retry(_make_msg(), ["a@b.com"], "test", max_retries=3)

    assert result is True
    assert smtp_class.call_count == 2
    # Backoff slept once (after attempt 0)
    assert sleeper.call_count == 1
    # Backoff = 2^0 = 1 second after first failure
    assert sleeper.call_args_list[0][0][0] == 1


# ─── Persistent failure exhausts retries → False ──────────────────


def test_persistent_failure_returns_false_after_max_retries():
    from email_alerts import _send_smtp_with_retry
    err = smtplib.SMTPException("server permanently unreachable")
    with patch("smtplib.SMTP", side_effect=[err, err, err]) as smtp_class, \
         patch("time.sleep") as sleeper:
        result = _send_smtp_with_retry(_make_msg(), ["a@b.com"], "test", max_retries=3)

    assert result is False
    # All 3 attempts made
    assert smtp_class.call_count == 3
    # 2 sleeps (after attempts 0 and 1; no sleep after the final failed attempt)
    assert sleeper.call_count == 2


def test_exponential_backoff_is_2_to_attempt_power():
    """Verify the 1, 2, 4, 8 pattern for max_retries=4."""
    from email_alerts import _send_smtp_with_retry
    err = smtplib.SMTPException("nope")
    with patch("smtplib.SMTP", side_effect=[err, err, err, err]), \
         patch("time.sleep") as sleeper:
        result = _send_smtp_with_retry(_make_msg(), ["a@b.com"], "test", max_retries=4)

    assert result is False
    sleeps = [c[0][0] for c in sleeper.call_args_list]
    # max_retries=4 → 3 sleeps with 2^0, 2^1, 2^2 = 1, 2, 4
    assert sleeps == [1, 2, 4], (
        f"Backoff sequence wrong: got {sleeps}, expected [1, 2, 4]"
    )


# ─── max_retries parameter honored ────────────────────────────────


def test_max_retries_one_means_no_retry():
    """max_retries=1 → exactly 1 attempt, 0 sleeps, returns False on fail."""
    from email_alerts import _send_smtp_with_retry
    err = smtplib.SMTPException("fail")
    with patch("smtplib.SMTP", side_effect=[err]) as smtp_class, \
         patch("time.sleep") as sleeper:
        result = _send_smtp_with_retry(_make_msg(), ["a@b.com"], "test", max_retries=1)

    assert result is False
    assert smtp_class.call_count == 1
    assert sleeper.call_count == 0


# ─── OSError path (connection-level network failure) ──────────────


def test_oserror_treated_as_retryable_smtp_failure():
    """Connection-level OSError (DNS, refused, timeout) must trigger
    the same retry path as SMTPException."""
    from email_alerts import _send_smtp_with_retry
    fake_server = MagicMock()
    fake_smtp_ctx = MagicMock()
    fake_smtp_ctx.__enter__ = MagicMock(return_value=fake_server)
    fake_smtp_ctx.__exit__ = MagicMock(return_value=False)

    with patch("smtplib.SMTP", side_effect=[
        OSError("connection refused"),
        fake_smtp_ctx,
    ]) as smtp_class, patch("time.sleep"):
        result = _send_smtp_with_retry(_make_msg(), ["a@b.com"], "test", max_retries=3)

    assert result is True
    assert smtp_class.call_count == 2


def test_unrelated_exception_not_caught():
    """A non-SMTP/OSError exception should bubble up — we don't want
    silent swallowing of programming errors."""
    from email_alerts import _send_smtp_with_retry
    with patch("smtplib.SMTP", side_effect=ValueError("typo in code")), \
         patch("time.sleep"):
        with pytest.raises(ValueError, match="typo"):
            _send_smtp_with_retry(_make_msg(), ["a@b.com"], "test", max_retries=3)


# ─── Logging ─────────────────────────────────────────────────────


def test_warning_logged_per_retry(caplog):
    from email_alerts import _send_smtp_with_retry
    err = smtplib.SMTPException("blip")
    fake_server = MagicMock()
    fake_smtp_ctx = MagicMock()
    fake_smtp_ctx.__enter__ = MagicMock(return_value=fake_server)
    fake_smtp_ctx.__exit__ = MagicMock(return_value=False)

    with caplog.at_level(logging.WARNING), \
         patch("smtplib.SMTP", side_effect=[err, err, fake_smtp_ctx]), \
         patch("time.sleep"):
        _send_smtp_with_retry(_make_msg(), ["a@b.com"], "test", max_retries=3)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    # 2 retries → 2 warnings
    assert len(warnings) == 2
    assert "SMTP attempt" in warnings[0].message


def test_error_logged_on_final_failure(caplog):
    from email_alerts import _send_smtp_with_retry
    err = smtplib.SMTPException("permanently broken")

    with caplog.at_level(logging.ERROR), \
         patch("smtplib.SMTP", side_effect=[err, err, err]), \
         patch("time.sleep"):
        _send_smtp_with_retry(_make_msg(), ["a@b.com"], "test", max_retries=3)

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "Failed to send" in errors[0].message
    assert "after 3 attempts" in errors[0].message
