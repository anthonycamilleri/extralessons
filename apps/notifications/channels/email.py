import smtplib

from django.conf import settings
from django.core.mail import EmailMessage

from ..backends.zeptomail import REQUEST_ID_HEADER, ZeptoMailError
from .base import ChannelError


class EmailAdapter:
    """Send via Django's configured email backend.

    In production that is the ZeptoMail API backend (or SMTP, if configured
    that way); in development the console; in tests the in-memory outbox. The
    adapter does not care which — it only translates each backend's idea of
    "this address will never work" into a permanent ChannelError.

    With no `connection`, every message opens and tears down its own session —
    fine for one email, ruinous for a batch, where the TLS handshake dominates.
    The worker passes one open connection for the whole batch, which is the
    difference between a 150-family broadcast taking seconds and taking
    minutes.
    """

    def __init__(self, connection=None):
        self.connection = connection

    def send(self, notification):
        if not notification.recipient_email:
            raise ChannelError("No email address for recipient", permanent=True)
        message = EmailMessage(
            subject=notification.rendered_subject,
            body=notification.rendered_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[notification.recipient_email],
            # Set for contact-form messages, so the office answers the parent
            # who wrote rather than the site's no-reply address.
            reply_to=[notification.reply_to] if notification.reply_to else None,
            connection=self.connection,
        )
        try:
            message.send(fail_silently=False)
        except smtplib.SMTPRecipientsRefused as exc:
            raise ChannelError(str(exc), permanent=True) from exc
        except ZeptoMailError as exc:
            raise ChannelError(str(exc), permanent=exc.permanent) from exc
        except Exception as exc:
            raise ChannelError(str(exc)) from exc
        # Whatever handle the backend left behind for tracing the delivery:
        # ZeptoMail's request id, or a Message-Id set by the caller.
        return (
            message.extra_headers.get(REQUEST_ID_HEADER)
            or message.extra_headers.get("Message-Id", "")
        )
