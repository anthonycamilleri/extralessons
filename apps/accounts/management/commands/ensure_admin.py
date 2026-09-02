"""Make sure the school has an admin account, and hand its owner a way in.

Runs as the second half of the pre-deploy command, after ``migrate``, so a
fresh deployment comes up with an administrator and nobody has to open a
shell. Idempotent: on every later deploy it finds the account and does nothing.

    manage.py ensure_admin                       # ADMIN_EMAIL from settings
    manage.py ensure_admin --email you@example.com
    manage.py ensure_admin --send-reset          # resend the set-password link

No password is ever configured or printed. The account is created with a long
random password nobody knows, and the owner receives the same "set your
password" email the login page's *Forgotten your password?* link sends, via the
configured email backend. If that email cannot go out (no ZeptoMail token yet),
the deploy still succeeds and the link can be requested from the login page.
"""
import secrets
from urllib.parse import urlparse

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordResetForm
from django.core.management.base import BaseCommand, CommandError

User = get_user_model()


class Command(BaseCommand):
    help = "Create the school admin account named by ADMIN_EMAIL if it does not exist."

    def add_arguments(self, parser):
        parser.add_argument("--email", help="Override ADMIN_EMAIL.")
        parser.add_argument(
            "--send-reset",
            action="store_true",
            help="Email a set-password link even if the account already existed.",
        )
        parser.add_argument(
            "--no-email", action="store_true", help="Create the account but send nothing."
        )

    def handle(self, *args, email, send_reset, no_email, **options):
        email = (email or getattr(settings, "ADMIN_EMAIL", "") or "").strip()
        if not email:
            self.stdout.write("ADMIN_EMAIL is not set; no admin account to ensure.")
            return
        email = User.objects.normalize_email(email)

        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "role": User.Role.ADMIN,
                "is_staff": True,
                "is_superuser": True,
                "is_active": True,
            },
        )
        if created:
            # A usable-but-unknown password: Django's reset flow refuses to
            # email accounts whose password is marked unusable, and that flow
            # is exactly how the owner is meant to get in.
            user.set_password(secrets.token_urlsafe(32))
            user.save(update_fields=["password"])
            self.stdout.write(self.style.SUCCESS(f"Created admin account {email}."))
        else:
            promoted = []
            for field, value in (("is_staff", True), ("is_superuser", True), ("is_active", True)):
                if getattr(user, field) != value:
                    setattr(user, field, value)
                    promoted.append(field)
            if user.role != User.Role.ADMIN:
                user.role = User.Role.ADMIN
                promoted.append("role")
            if promoted:
                user.save(update_fields=promoted)
                self.stdout.write(f"Promoted existing account {email} to admin ({', '.join(promoted)}).")
            else:
                self.stdout.write(f"Admin account {email} already exists.")

        if no_email or not (created or send_reset):
            return
        try:
            self._send_set_password_email(user)
        except Exception as exc:  # noqa: BLE001 - a deploy must not fail on SMTP weather
            self.stderr.write(
                self.style.WARNING(
                    f"Could not email the set-password link to {email}: {exc}. "
                    "Use 'Forgotten your password?' on the login page once email works."
                )
            )
        else:
            self.stdout.write(f"Sent a set-password link to {email}.")

    def _send_set_password_email(self, user):
        form = PasswordResetForm({"email": user.email})
        if not form.is_valid():
            raise CommandError(f"{user.email} is not a valid email address")
        site = urlparse(settings.SITE_URL)
        if not site.netloc:
            raise CommandError(f"SITE_URL ({settings.SITE_URL!r}) has no hostname to build the link from")
        form.save(
            domain_override=site.netloc,
            use_https=(site.scheme == "https"),
            from_email=settings.DEFAULT_FROM_EMAIL,
        )
