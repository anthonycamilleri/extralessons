"""`manage.py send_test_email`: the deploy-time proof that mail goes out."""
import io
from unittest import mock

import pytest
from django.core import mail
from django.core.management import call_command
from django.core.management.base import CommandError


def run(*args):
    out = io.StringIO()
    call_command("send_test_email", *args, stdout=out)
    return out.getvalue()


def test_sends_to_admin_email_by_default(settings):
    settings.ADMIN_EMAIL = "head@school.test"
    settings.DEFAULT_FROM_EMAIL = "School Activities <noreply@example.com>"
    settings.SITE_URL = "https://activities.example.com"

    out = run("--tag", "abc123")

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == ["head@school.test"]
    assert message.from_email == "School Activities <noreply@example.com>"
    assert "deploy abc123" in message.subject
    assert "https://activities.example.com" in message.body
    assert "locmem" in message.body  # names the backend it went through
    assert "Sent a test email to head@school.test" in out


def test_explicit_recipient_wins(settings):
    settings.ADMIN_EMAIL = "head@school.test"
    run("--to", "ops@example.com")
    assert mail.outbox[0].to == ["ops@example.com"]


def test_no_recipient_is_an_error(settings):
    settings.ADMIN_EMAIL = ""
    with pytest.raises(CommandError, match="No recipient"):
        run()
    assert mail.outbox == []


def test_a_backend_failure_is_a_command_error(settings):
    settings.ADMIN_EMAIL = "head@school.test"
    with mock.patch(
        "django.core.mail.backends.locmem.EmailBackend.send_messages",
        side_effect=ConnectionRefusedError("smtp.tem.scaleway.com:587 refused"),
    ):
        with pytest.raises(CommandError, match="refused"):
            run()


def test_smtp_settings_are_described(settings):
    settings.ADMIN_EMAIL = "head@school.test"
    settings.EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    settings.EMAIL_HOST = "smtp.tem.scaleway.com"
    settings.EMAIL_PORT = 587
    settings.EMAIL_USE_TLS = True
    settings.EMAIL_HOST_USER = "project-id"
    with mock.patch(
        "django.core.mail.backends.smtp.EmailBackend.send_messages", return_value=1
    ) as send, mock.patch("django.core.mail.backends.smtp.EmailBackend.open"), mock.patch(
        "django.core.mail.backends.smtp.EmailBackend.close"
    ):
        out = run()
    assert "SMTP smtp.tem.scaleway.com:587 STARTTLS as project-id" in out
    (batch,), _ = send.call_args
    assert "smtp.tem.scaleway.com:587" in batch[0].body
