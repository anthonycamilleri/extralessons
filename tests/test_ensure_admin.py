"""`manage.py ensure_admin`: the deploy-time admin bootstrap."""
import io
from unittest import mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm
from django.core import mail
from django.core.management import call_command

User = get_user_model()
pytestmark = pytest.mark.django_db

EMAIL = "head@school.test"


def run(*args):
    out, err = io.StringIO(), io.StringIO()
    call_command("ensure_admin", *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


@pytest.fixture(autouse=True)
def configured(settings):
    settings.ADMIN_EMAIL = EMAIL
    settings.SITE_URL = "https://activities.example.com"
    settings.DEFAULT_FROM_EMAIL = "School Activities <noreply@example.com>"


def test_creates_a_superuser_and_emails_a_set_password_link():
    out, err = run()

    user = User.objects.get(email=EMAIL)
    assert user.is_staff and user.is_superuser and user.is_active
    assert user.role == User.Role.ADMIN
    assert user.has_usable_password()  # random and unknown, but resettable
    assert "Created admin account" in out and "Sent a set-password link" in out
    assert err == ""

    assert len(mail.outbox) == 1
    message = mail.outbox[0]
    assert message.to == [EMAIL]
    assert message.from_email == "School Activities <noreply@example.com>"
    assert "https://activities.example.com/accounts/password-reset/" in message.body


def test_is_idempotent():
    run()
    mail.outbox.clear()
    out, _ = run()
    assert User.objects.filter(email=EMAIL).count() == 1
    assert "already exists" in out
    assert mail.outbox == []


def test_promotes_an_existing_account_without_emailing():
    User.objects.create_user(EMAIL, "secret", role=User.Role.PARENT)
    out, _ = run()
    user = User.objects.get(email=EMAIL)
    assert user.is_staff and user.is_superuser and user.role == User.Role.ADMIN
    assert user.check_password("secret")  # their password is left alone
    assert "Promoted" in out
    assert mail.outbox == []


def test_send_reset_flag_emails_an_existing_admin():
    run()
    mail.outbox.clear()
    run("--send-reset")
    assert len(mail.outbox) == 1


def test_no_email_flag():
    run("--no-email")
    assert User.objects.filter(email=EMAIL).exists()
    assert mail.outbox == []


def test_email_override_wins_over_setting():
    run("--email", "Other@School.test")
    assert User.objects.filter(email="Other@school.test").exists()


def test_nothing_to_do_without_an_address(settings):
    settings.ADMIN_EMAIL = ""
    out, _ = run()
    assert "not set" in out
    assert User.objects.count() == 0


def test_email_failure_does_not_fail_the_deploy():
    with mock.patch.object(PasswordResetForm, "save", side_effect=OSError("smtp down")):
        out, err = run()
    assert User.objects.filter(email=EMAIL).exists()
    assert "Created admin account" in out
    assert "Could not email" in err and "Forgotten your password" in err
