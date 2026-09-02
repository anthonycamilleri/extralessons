"""Django email backend for Zoho ZeptoMail's sending API.

ZeptoMail also offers an SMTP relay, which Django could use with zero code.
The API is preferred because:

  * every send is one HTTPS request over a pooled connection, so a batch of
    150 notifications is not 150 TLS handshakes (the SMTP backend needs an
    explicitly shared connection to avoid that; here the requests.Session is
    that connection);
  * failures come back as structured JSON — a status code and a ZeptoMail
    error code — rather than an SMTP reply to parse, so "this address is
    malformed, stop retrying" and "ZeptoMail is having a moment, try later"
    are distinguishable;
  * ZeptoMail's request id is captured on the message, so a delivery in the
    admin can be traced in ZeptoMail's own processed-emails log.

Configuration (all settings, all environment-driven):

  ZEPTOMAIL_SEND_MAIL_TOKEN   the Mail Agent's "Send Mail Token" (secret)
  ZEPTOMAIL_API_URL           https://api.zeptomail.eu/v1.1/email for accounts
                              on the EU data centre (the default here),
                              https://api.zeptomail.com/v1.1/email otherwise
  ZEPTOMAIL_BOUNCE_ADDRESS    optional; a bounce address configured on the
                              Mail Agent
  EMAIL_TIMEOUT               per-request timeout, shared with the SMTP backend

The sender domain must be verified in ZeptoMail (SPF + DKIM records), and
DEFAULT_FROM_EMAIL must be on it, or every send fails with a 400.

Reference: https://www.zoho.com/zeptomail/help/api/email-sending.html
"""
import base64
import logging
from email.utils import getaddresses, parseaddr

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)

# The message header under which the backend records ZeptoMail's request id
# after a successful send. It is not an RFC 5322 Message-ID — ZeptoMail assigns
# that itself and does not return it — but it is the handle ZeptoMail's own
# logs are searched by.
REQUEST_ID_HEADER = "X-ZeptoMail-Request-Id"

# Status codes that mean "the message itself is wrong": a malformed address,
# an unverified sender, a missing subject. Retrying the same payload will fail
# the same way, so the notifier should give up on it at once. Everything else
# (401/403 from a rotated token, 429, 5xx, a network error) is worth a retry —
# a fixed token or a recovered service lets the queued row go out.
PERMANENT_STATUSES = frozenset({400, 404, 413, 415, 422})


class ZeptoMailError(Exception):
    """A send that ZeptoMail rejected or that never reached it."""

    def __init__(self, message, *, status=None, code="", request_id="", permanent=False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.request_id = request_id
        self.permanent = permanent


class ZeptoMailBackend(BaseEmailBackend):
    def __init__(
        self,
        fail_silently=False,
        *,
        api_url=None,
        token=None,
        bounce_address=None,
        timeout=None,
        **kwargs,
    ):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_url = api_url or settings.ZEPTOMAIL_API_URL
        self.token = token if token is not None else settings.ZEPTOMAIL_SEND_MAIL_TOKEN
        self.bounce_address = (
            bounce_address
            if bounce_address is not None
            else getattr(settings, "ZEPTOMAIL_BOUNCE_ADDRESS", "")
        )
        self.timeout = timeout if timeout is not None else settings.EMAIL_TIMEOUT
        self.session = None

    # --- Connection lifecycle -------------------------------------------------
    # Django's contract: open() returns True if it opened something new that
    # send_messages() must close again, False if a connection was already open
    # (the caller owns it). The notifier holds one backend open for a batch.

    def open(self):
        if self.session is not None:
            return False
        if not self.token:
            if self.fail_silently:
                return False
            raise ImproperlyConfigured(
                "ZEPTOMAIL_SEND_MAIL_TOKEN is not set; the ZeptoMail email backend "
                "cannot send. Set it, or point EMAIL_BACKEND elsewhere."
            )
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Zoho-enczapikey {self.token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )
        return True

    def close(self):
        if self.session is None:
            return
        try:
            self.session.close()
        finally:
            self.session = None

    # --- Sending --------------------------------------------------------------

    def send_messages(self, email_messages):
        if not email_messages:
            return 0
        new_connection = self.open()
        if self.session is None:
            # fail_silently with no token: swallow, as the SMTP backend does
            # when its connection fails.
            return 0
        sent = 0
        try:
            for message in email_messages:
                if self._send(message):
                    sent += 1
        finally:
            if new_connection:
                self.close()
        return sent

    def _send(self, message):
        if not message.recipients():
            return False
        payload = build_payload(message, bounce_address=self.bounce_address)
        try:
            response = self.session.post(self.api_url, json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            return self._fail(ZeptoMailError(f"ZeptoMail request failed: {exc}"), message)

        if response.status_code >= 400:
            return self._fail(error_from_response(response), message)

        request_id = ""
        try:
            request_id = response.json().get("request_id", "") or ""
        except ValueError:
            pass
        message.extra_headers[REQUEST_ID_HEADER] = request_id
        return True

    def _fail(self, error, message):
        if not self.fail_silently:
            raise error
        logger.warning(
            "ZeptoMail send to %s failed silently: %s", message.recipients(), error
        )
        return False


# --- Payload ------------------------------------------------------------------


def _address(value):
    """'Name <addr>' or 'addr' -> ZeptoMail's {"address": ..., "name": ...}."""
    name, addr = parseaddr(value)
    entry = {"address": addr or value}
    if name:
        entry["name"] = name
    return entry


def _recipients(values):
    return [{"email_address": _address(v)} for v in _expand(values)]


def _expand(values):
    # A single EmailMessage field may hold "a@x, b@y" in one string.
    return [
        f"{name} <{addr}>" if name else addr
        for name, addr in getaddresses(values)
        if addr
    ]


def build_payload(message, *, bounce_address=""):
    """Translate a Django EmailMessage into ZeptoMail's request body.

    Plain body becomes `textbody`; a text/html alternative (as attached by
    EmailMultiAlternatives) becomes `htmlbody`; a message whose content_subtype
    is html sends its body as `htmlbody` only. Attachments are inlined base64,
    which is what the API wants for anything under its size limit.
    """
    payload = {
        "from": _address(message.from_email),
        "to": _recipients(message.to),
        "subject": message.subject,
    }
    if message.cc:
        payload["cc"] = _recipients(message.cc)
    if message.bcc:
        payload["bcc"] = _recipients(message.bcc)
    if message.reply_to:
        payload["reply_to"] = [_address(v) for v in _expand(message.reply_to)]
    if bounce_address:
        payload["bounce_address"] = bounce_address

    if message.content_subtype == "html":
        payload["htmlbody"] = message.body
    else:
        payload["textbody"] = message.body
    for content, mimetype in getattr(message, "alternatives", ()):
        if mimetype == "text/html" and "htmlbody" not in payload:
            payload["htmlbody"] = content

    attachments = []
    for attachment in message.attachments:
        # Django allows either a (filename, content, mimetype) triple or a
        # MIMEBase instance; only the triple is supported here, the form the
        # application actually produces.
        if not isinstance(attachment, (tuple, list)):
            raise ZeptoMailError(
                "MIMEBase attachments are not supported by the ZeptoMail backend",
                permanent=True,
            )
        filename, content, mimetype = attachment
        if isinstance(content, str):
            content = content.encode(message.encoding or settings.DEFAULT_CHARSET)
        attachments.append(
            {
                "name": filename,
                "mime_type": mimetype or "application/octet-stream",
                "content": base64.b64encode(content).decode("ascii"),
            }
        )
    if attachments:
        payload["attachments"] = attachments

    headers = {k: v for k, v in message.extra_headers.items() if k != REQUEST_ID_HEADER}
    if headers:
        payload["mime_headers"] = headers
    return payload


def error_from_response(response):
    """Build a ZeptoMailError from a non-2xx response.

    ZeptoMail's error body is {"error": {"code": "TM_3201", "message": ...,
    "details": [{"code": "GE_102", "message": ..., "target": "subject"}],
    "request_id": ...}}. Fold the details into the message so a failed row in
    the admin says *which* field ZeptoMail objected to.
    """
    code = ""
    request_id = ""
    text = response.text[:500]
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        error = body["error"]
        code = str(error.get("code", ""))
        request_id = str(error.get("request_id", ""))
        parts = [str(error.get("message", "")).strip()]
        for detail in error.get("details") or []:
            if not isinstance(detail, dict):
                continue
            piece = " ".join(
                str(detail[k]) for k in ("code", "message") if detail.get(k)
            )
            if detail.get("target"):
                piece += f" (field: {detail['target']})"
            parts.append(piece)
        text = "; ".join(p for p in parts if p)
    return ZeptoMailError(
        f"ZeptoMail rejected the message (HTTP {response.status_code}"
        + (f", {code}" if code else "")
        + f"): {text}",
        status=response.status_code,
        code=code,
        request_id=request_id,
        permanent=response.status_code in PERMANENT_STATUSES,
    )
