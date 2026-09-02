"""The ZeptoMail email backend: payload shape, connection reuse, error mapping.

No network: `requests.Session.post` is patched and handed canned responses in
ZeptoMail's documented shapes.
"""
import base64
import json
from unittest import mock

import pytest
import requests
from django.core import mail
from django.core.exceptions import ImproperlyConfigured
from django.core.mail import EmailMessage, EmailMultiAlternatives
from django.test import override_settings

from apps.notifications.backends.zeptomail import (
    REQUEST_ID_HEADER,
    ZeptoMailBackend,
    ZeptoMailError,
    build_payload,
)
from apps.notifications.channels.base import ChannelError
from apps.notifications.channels.email import EmailAdapter

TOKEN = "Zoho-test-token"
API_URL = "https://api.zeptomail.eu/v1.1/email"

SUCCESS = {
    "data": [{"code": "EM_104", "additional_info": [], "message": "Email request received"}],
    "message": "OK",
    "request_id": "req-123",
    "object": "email",
}
BAD_REQUEST = {
    "error": {
        "code": "TM_3201",
        "details": [
            {"code": "GE_102", "message": "Mandatory Field 'subject' was set as Empty Value.", "target": "subject"}
        ],
        "message": "Mandatory Field 'subject' was set as Empty Value.",
        "request_id": "req-err",
    }
}


def fake_response(status, body):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(body).encode()
    response.headers["Content-Type"] = "application/json"
    return response


@pytest.fixture
def post():
    with mock.patch.object(requests.Session, "post") as patched:
        patched.return_value = fake_response(201, SUCCESS)
        yield patched


def backend(**kwargs):
    kwargs.setdefault("api_url", API_URL)
    kwargs.setdefault("token", TOKEN)
    return ZeptoMailBackend(**kwargs)


# --- Payload ------------------------------------------------------------------


def test_payload_maps_addresses_bodies_and_reply_to():
    message = EmailMultiAlternatives(
        subject="Chess Club: place confirmed",
        body="Plain text",
        from_email="School Activities <notifications@example.com>",
        to=["Paula <paula@example.com>", "second@example.com"],
        cc=["office@example.com"],
        bcc=["archive@example.com"],
        reply_to=["Office <office@example.com>"],
        headers={"X-Campaign": "autumn"},
    )
    message.attach_alternative("<p>HTML</p>", "text/html")

    payload = build_payload(message, bounce_address="bounce@bounce.example.com")

    assert payload["from"] == {"address": "notifications@example.com", "name": "School Activities"}
    assert payload["to"] == [
        {"email_address": {"address": "paula@example.com", "name": "Paula"}},
        {"email_address": {"address": "second@example.com"}},
    ]
    assert payload["cc"] == [{"email_address": {"address": "office@example.com"}}]
    assert payload["bcc"] == [{"email_address": {"address": "archive@example.com"}}]
    assert payload["reply_to"] == [{"address": "office@example.com", "name": "Office"}]
    assert payload["subject"] == "Chess Club: place confirmed"
    assert payload["textbody"] == "Plain text"
    assert payload["htmlbody"] == "<p>HTML</p>"
    assert payload["bounce_address"] == "bounce@bounce.example.com"
    assert payload["mime_headers"] == {"X-Campaign": "autumn"}
    assert "attachments" not in payload


def test_payload_plain_message_has_no_html_and_no_optional_keys():
    message = EmailMessage("Subject", "Body", "from@example.com", ["to@example.com"])
    payload = build_payload(message)
    assert payload == {
        "from": {"address": "from@example.com"},
        "to": [{"email_address": {"address": "to@example.com"}}],
        "subject": "Subject",
        "textbody": "Body",
    }


def test_payload_html_content_subtype_sends_htmlbody_only():
    message = EmailMessage("S", "<b>hi</b>", "from@example.com", ["to@example.com"])
    message.content_subtype = "html"
    payload = build_payload(message)
    assert payload["htmlbody"] == "<b>hi</b>"
    assert "textbody" not in payload


def test_payload_encodes_attachments_as_base64():
    message = EmailMessage("S", "B", "from@example.com", ["to@example.com"])
    message.attach("timetable.pdf", b"%PDF-1.4 fake", "application/pdf")
    message.attach("notes.txt", "hello", "text/plain")
    payload = build_payload(message)
    assert payload["attachments"] == [
        {
            "name": "timetable.pdf",
            "mime_type": "application/pdf",
            "content": base64.b64encode(b"%PDF-1.4 fake").decode(),
        },
        {"name": "notes.txt", "mime_type": "text/plain", "content": base64.b64encode(b"hello").decode()},
    ]


def test_payload_splits_comma_separated_recipients():
    message = EmailMessage("S", "B", "from@example.com", ["a@example.com, B <b@example.com>"])
    payload = build_payload(message)
    assert [r["email_address"]["address"] for r in payload["to"]] == ["a@example.com", "b@example.com"]


# --- Sending ------------------------------------------------------------------


def test_send_posts_json_with_token_header_and_records_request_id(post):
    message = EmailMessage("S", "B", "from@example.com", ["to@example.com"])
    conn = backend(timeout=7)

    assert conn.send_messages([message]) == 1

    post.assert_called_once()
    (url,), kwargs = post.call_args
    assert url == API_URL
    assert kwargs["timeout"] == 7
    assert kwargs["json"]["to"] == [{"email_address": {"address": "to@example.com"}}]
    assert message.extra_headers[REQUEST_ID_HEADER] == "req-123"
    # The session is gone again: send_messages opened it, so it closes it.
    assert conn.session is None


def test_session_carries_authorization_header():
    conn = backend()
    assert conn.open() is True
    try:
        assert conn.session.headers["Authorization"] == f"Zoho-enczapikey {TOKEN}"
        assert conn.session.headers["Content-Type"] == "application/json"
        assert conn.open() is False  # already open: caller owns it
    finally:
        conn.close()
    assert conn.session is None


@pytest.mark.parametrize(
    "raw",
    [TOKEN, f"Zoho-enczapikey {TOKEN}", f"  zoho-enczapikey   {TOKEN}\n"],
)
def test_token_pasted_with_the_dashboard_prefix_still_authenticates(raw):
    conn = backend(token=raw)
    conn.open()
    try:
        assert conn.session.headers["Authorization"] == f"Zoho-enczapikey {TOKEN}"
    finally:
        conn.close()


def test_batch_reuses_one_session_when_caller_holds_it_open(post):
    conn = backend()
    conn.open()
    session = conn.session
    messages = [EmailMessage("S", "B", "from@example.com", [f"{i}@example.com"]) for i in range(3)]
    assert conn.send_messages(messages) == 3
    assert conn.session is session  # not closed: we opened it, we close it
    assert post.call_count == 3
    conn.close()


def test_request_id_header_is_not_echoed_back_as_a_mime_header(post):
    message = EmailMessage("S", "B", "from@example.com", ["to@example.com"])
    backend().send_messages([message])
    backend().send_messages([message])  # second send of the same object
    assert "mime_headers" not in post.call_args.kwargs["json"]


def test_message_without_recipients_is_skipped(post):
    message = EmailMessage("S", "B", "from@example.com", [])
    assert backend().send_messages([message]) == 0
    post.assert_not_called()


def test_missing_token_is_a_configuration_error():
    with pytest.raises(ImproperlyConfigured):
        backend(token="").send_messages([EmailMessage("S", "B", "f@example.com", ["t@example.com"])])


def test_missing_token_with_fail_silently_sends_nothing():
    conn = backend(token="", fail_silently=True)
    assert conn.send_messages([EmailMessage("S", "B", "f@example.com", ["t@example.com"])]) == 0


# --- Errors -------------------------------------------------------------------


def test_bad_request_is_permanent_and_names_the_field(post):
    post.return_value = fake_response(400, BAD_REQUEST)
    with pytest.raises(ZeptoMailError) as excinfo:
        backend().send_messages([EmailMessage("", "B", "f@example.com", ["t@example.com"])])
    err = excinfo.value
    assert err.permanent is True
    assert err.status == 400
    assert err.code == "TM_3201"
    assert err.request_id == "req-err"
    assert "GE_102" in str(err) and "field: subject" in str(err)


@pytest.mark.parametrize("status", [401, 429, 500, 503])
def test_auth_rate_limit_and_server_errors_are_transient(post, status):
    post.return_value = fake_response(status, {"error": {"code": "X", "message": "nope"}})
    with pytest.raises(ZeptoMailError) as excinfo:
        backend().send_messages([EmailMessage("S", "B", "f@example.com", ["t@example.com"])])
    assert excinfo.value.permanent is False
    assert excinfo.value.status == status


def test_non_json_error_body_is_still_reported(post):
    response = requests.Response()
    response.status_code = 502
    response._content = b"<html>Bad gateway</html>"
    post.return_value = response
    with pytest.raises(ZeptoMailError) as excinfo:
        backend().send_messages([EmailMessage("S", "B", "f@example.com", ["t@example.com"])])
    assert "Bad gateway" in str(excinfo.value)
    assert excinfo.value.permanent is False


def test_network_failure_is_transient(post):
    post.side_effect = requests.ConnectionError("boom")
    with pytest.raises(ZeptoMailError) as excinfo:
        backend().send_messages([EmailMessage("S", "B", "f@example.com", ["t@example.com"])])
    assert excinfo.value.permanent is False


def test_fail_silently_swallows_errors_and_counts_zero(post):
    post.return_value = fake_response(500, {"error": {"code": "X", "message": "nope"}})
    conn = backend(fail_silently=True)
    assert conn.send_messages([EmailMessage("S", "B", "f@example.com", ["t@example.com"])]) == 0


# --- Integration with Django's mail API and the notifications adapter ----------


@override_settings(
    EMAIL_BACKEND="apps.notifications.backends.zeptomail.ZeptoMailBackend",
    ZEPTOMAIL_SEND_MAIL_TOKEN=TOKEN,
    ZEPTOMAIL_API_URL=API_URL,
)
def test_django_send_mail_goes_through_the_backend(post):
    assert mail.send_mail("S", "B", "from@example.com", ["to@example.com"]) == 1
    (url,), kwargs = post.call_args
    assert url == API_URL
    assert kwargs["json"]["subject"] == "S"


@override_settings(
    EMAIL_BACKEND="apps.notifications.backends.zeptomail.ZeptoMailBackend",
    ZEPTOMAIL_SEND_MAIL_TOKEN=TOKEN,
    ZEPTOMAIL_API_URL=API_URL,
)
def test_email_adapter_maps_permanent_rejections(post):
    post.return_value = fake_response(400, BAD_REQUEST)
    notification = mock.Mock(
        recipient_email="parent@example.com", rendered_subject="S", rendered_body="B"
    )
    with pytest.raises(ChannelError) as excinfo:
        EmailAdapter().send(notification)
    assert excinfo.value.permanent is True


@override_settings(
    EMAIL_BACKEND="apps.notifications.backends.zeptomail.ZeptoMailBackend",
    ZEPTOMAIL_SEND_MAIL_TOKEN=TOKEN,
    ZEPTOMAIL_API_URL=API_URL,
)
def test_email_adapter_returns_zeptomail_request_id(post):
    notification = mock.Mock(
        recipient_email="parent@example.com", rendered_subject="S", rendered_body="B"
    )
    assert EmailAdapter().send(notification) == "req-123"
