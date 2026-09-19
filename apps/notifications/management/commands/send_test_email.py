"""Send one email through the configured backend, and fail loudly if it cannot.

The deploy pipeline runs this as a job on the estate after migrations, so a
broken mail set-up (wrong SMTP credentials, a blocked port, a sender domain the
provider will not accept) fails the deploy before the new image serves traffic,
instead of surfacing days later as an outbox full of retries.

    manage.py send_test_email                 # to ADMIN_EMAIL
    manage.py send_test_email --to you@example.com --tag 61d6293

Nothing is stored: no Notification row, no delivery-log entry. The message
names the backend, host and From address it went through, so the recipient can
tell which configuration produced it.
"""
from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone


class Command(BaseCommand):
    help = "Send a test email through the configured backend; exit non-zero if it fails."

    def add_arguments(self, parser):
        parser.add_argument("--to", help="Recipient; defaults to ADMIN_EMAIL.")
        parser.add_argument(
            "--tag", default="", help="Deploy identifier to put in the subject (image tag, SHA)."
        )

    def handle(self, *args, to, tag, **options):
        recipient = (to or getattr(settings, "ADMIN_EMAIL", "") or "").strip()
        if not recipient:
            raise CommandError("No recipient: pass --to or set ADMIN_EMAIL.")

        backend = settings.EMAIL_BACKEND
        via = backend.rsplit(".", 1)[-1]
        if "smtp" in backend:
            via = f"SMTP {settings.EMAIL_HOST}:{settings.EMAIL_PORT}"
            via += " STARTTLS" if settings.EMAIL_USE_TLS else ""
            via += f" as {settings.EMAIL_HOST_USER}" if settings.EMAIL_HOST_USER else ""
        elif "zeptomail" in backend:
            via = f"ZeptoMail API {settings.ZEPTOMAIL_API_URL}"

        site = getattr(settings, "SITE_URL", "") or "the site"
        subject = f"Email check{f' for deploy {tag}' if tag else ''} — {site}"
        body = (
            "This message was sent by `manage.py send_test_email` to confirm that "
            "the application can send email.\n\n"
            f"Site:     {site}\n"
            f"Deploy:   {tag or '(not given)'}\n"
            f"Sent at:  {timezone.now():%Y-%m-%d %H:%M %Z}\n"
            f"Via:      {via}\n"
            f"Backend:  {backend}\n"
            f"From:     {settings.DEFAULT_FROM_EMAIL}\n"
            f"To:       {recipient}\n\n"
            "If you were not expecting it, a deploy of the site just ran; nothing "
            "needs doing."
        )
        message = EmailMessage(
            subject=subject,
            body=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[recipient],
        )
        try:
            with get_connection(fail_silently=False) as connection:
                sent = connection.send_messages([message])
        except Exception as exc:  # smtplib.*, socket errors, backend errors
            raise CommandError(f"Sending via {via} failed: {exc}") from exc
        if not sent:
            raise CommandError(f"The backend ({via}) accepted nothing.")
        self.stdout.write(f"Sent a test email to {recipient} via {via}.")
