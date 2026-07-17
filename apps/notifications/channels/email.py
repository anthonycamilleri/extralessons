import smtplib

from django.conf import settings
from django.core.mail import EmailMessage

from .base import ChannelError


class EmailAdapter:
    """Send via Django's configured email backend (SMTP in prod, console in dev)."""

    def send(self, notification):
        if not notification.recipient_email:
            raise ChannelError("No email address for recipient", permanent=True)
        message = EmailMessage(
            subject=notification.rendered_subject,
            body=notification.rendered_body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[notification.recipient_email],
        )
        try:
            message.send(fail_silently=False)
        except smtplib.SMTPRecipientsRefused as exc:
            raise ChannelError(str(exc), permanent=True) from exc
        except Exception as exc:
            raise ChannelError(str(exc)) from exc
        return message.extra_headers.get("Message-Id", "")
