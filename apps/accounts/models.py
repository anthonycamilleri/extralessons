import secrets

from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create_user(self, email, password, **extra_fields):
        if not email:
            raise ValueError("The email address must be set")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", False)
        extra_fields.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra_fields)

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("role", User.Role.ADMIN)
        return self._create_user(email, password, **extra_fields)


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "School admin"
        PROVIDER = "PROVIDER", "Course provider"
        PARENT = "PARENT", "Parent"

    username = None
    email = models.EmailField("email address", unique=True)
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.PARENT)
    phone_e164 = models.CharField(
        "WhatsApp phone number",
        max_length=20,
        blank=True,
        help_text="International format, e.g. +35699123456",
    )
    notify_email = models.BooleanField(
        "receive email notifications", default=True
    )
    notify_whatsapp = models.BooleanField(
        "receive WhatsApp notifications",
        default=False,
        help_text="Requires a WhatsApp phone number.",
    )

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        full_name = self.get_full_name()
        return f"{full_name} <{self.email}>" if full_name else self.email


class ChildQuerySet(models.QuerySet):
    def for_guardian(self, user):
        """All children the given parent account may manage (single source
        of family scoping — used by dashboards and the public class page)."""
        return self.filter(guardians=user)


class Child(models.Model):
    guardians = models.ManyToManyField(
        settings.AUTH_USER_MODEL, through="Guardian", related_name="children"
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    date_of_birth = models.DateField()
    notes = models.TextField(
        blank=True,
        help_text="Anything providers should know (allergies, medical needs...). "
        "Visible to the providers of classes this child attends.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ChildQuerySet.as_manager()

    class Meta:
        verbose_name_plural = "children"
        ordering = ["first_name", "last_name"]

    def __str__(self):
        return f"{self.first_name} {self.last_name}"

    @property
    def full_name(self):
        return f"{self.first_name} {self.last_name}"


class Guardian(models.Model):
    """Link between a child and a parent account that can manage them."""

    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name="guardian_links")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="guardian_links"
    )
    is_primary = models.BooleanField(default=False, help_text="The account that created the child.")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["child", "user"], name="uniq_guardian_per_child"),
        ]

    def __str__(self):
        return f"{self.user} → {self.child}"


def _invite_token():
    return secrets.token_urlsafe(32)


class GuardianInvite(models.Model):
    """Invitation for a co-parent to gain access to a child."""

    child = models.ForeignKey(Child, on_delete=models.CASCADE, related_name="guardian_invites")
    email = models.EmailField()
    token = models.CharField(max_length=64, unique=True, default=_invite_token)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_guardian_invites"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="accepted_guardian_invites",
    )

    def __str__(self):
        return f"Invite {self.email} → {self.child}"


class SiteConfig(models.Model):
    """Singleton with school-wide settings, editable in the admin."""

    school_name = models.CharField(max_length=200, default="Our School")
    contact_email = models.EmailField(
        blank=True, help_text="Shown to parents as the school contact address."
    )
    catalogue_intro = models.TextField(
        blank=True,
        help_text="Text shown at the top of the public catalogue page.",
    )
    signup_open = models.BooleanField(
        default=True, help_text="Allow parents to create their own accounts."
    )
    offer_ttl_hours = models.PositiveSmallIntegerField(
        default=48,
        help_text="Hours a family has to confirm a waiting-list offer before it expires.",
    )
    notify_admins_new_request = models.BooleanField(
        default=True,
        help_text="Email school admins when a new enrollment request arrives.",
    )
    notify_admins_seat_freed = models.BooleanField(
        default=True,
        help_text="Email school admins when a seat frees up in a class with a waiting list.",
    )

    class Meta:
        verbose_name = "site configuration"
        verbose_name_plural = "site configuration"

    def __str__(self):
        return "Site configuration"

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)

    @classmethod
    def get(cls):
        # Deliberately uncached: it's one primary-key query, and caching it
        # per-process made admin changes (signup toggle, offer TTL) apply
        # inconsistently across gunicorn/notifier processes.
        config, _ = cls.objects.get_or_create(pk=1)
        return config
