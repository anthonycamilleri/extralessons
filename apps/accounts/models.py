import secrets

import markdown
from django.conf import settings
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.utils.safestring import mark_safe

from . import defaults


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


def school_class_choices():
    """P1–P5 and S1–S7, each with an English and a Slovenian section.

    Grouped so the form shows two <optgroup>s; the stored value is the short
    code parents and providers use every day (P3E, S5S).
    """
    sections = [("E", "English"), ("S", "Slovenian")]

    def cycle(prefix, years):
        return [
            (f"{prefix}{year}{code}", f"{prefix}{year}{code} · {label} section")
            for year in years
            for code, label in sections
        ]

    return [
        ("Primary", cycle("P", range(1, 6))),
        ("Secondary", cycle("S", range(1, 8))),
    ]


SCHOOL_CLASS_CHOICES = school_class_choices()
SCHOOL_CLASS_CODES = [code for _group, options in SCHOOL_CLASS_CHOICES for code, _ in options]


class Child(models.Model):
    guardians = models.ManyToManyField(
        settings.AUTH_USER_MODEL, through="Guardian", related_name="children"
    )
    first_name = models.CharField(max_length=100)
    last_name = models.CharField("surname", max_length=100)
    date_of_birth = models.DateField()
    school_class = models.CharField(
        "class",
        max_length=4,
        choices=SCHOOL_CLASS_CHOICES,
        blank=True,
        help_text="The child's class this school year, e.g. P3E or S2S.",
    )
    may_leave_alone = models.BooleanField(
        "may go home on their own",
        default=False,
        help_text="Tick if this child is authorised to leave an activity unaccompanied. "
        "Providers only act on what is recorded here, never on verbal instructions.",
    )
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

    @property
    def display_name(self):
        """Name with the class code, for rosters and pickers: "Lena Parent (P3E)"."""
        return f"{self.full_name} ({self.school_class})" if self.school_class else self.full_name


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
    sender_name = models.CharField(
        max_length=200,
        default=defaults.SENDER_NAME,
        help_text="Who signs the emails parents receive, e.g. “European School PTA”.",
    )
    contact_email = models.EmailField(
        blank=True,
        default=defaults.CONTACT_EMAIL,
        help_text="Shown to parents as the school contact address, and where the "
        "public contact form delivers. Empty hides the contact form.",
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
    terms_markdown = models.TextField(
        "terms and conditions",
        blank=True,
        default=defaults.TERMS_MARKDOWN,
        help_text="Markdown. Shown at /terms/ and linked from the navigation; parents must "
        "confirm they have read it before registering. Leave empty to hide it.",
    )

    class Meta:
        verbose_name = "site configuration"
        verbose_name_plural = "site configuration"

    def __str__(self):
        return "Site configuration"

    def save(self, *args, **kwargs):
        self.pk = 1  # enforce singleton
        super().save(*args, **kwargs)

    @property
    def has_terms(self):
        return bool(self.terms_markdown.strip())

    @property
    def terms_html(self):
        """The terms rendered to HTML. Admin-authored, so trusted as-is."""
        return mark_safe(
            markdown.markdown(self.terms_markdown, extensions=["extra", "sane_lists"])
        )

    @classmethod
    def get(cls):
        # Deliberately uncached: it's one primary-key query, and caching it
        # per-process made admin changes (signup toggle, offer TTL) apply
        # inconsistently across gunicorn/notifier processes.
        config, _ = cls.objects.get_or_create(pk=1)
        return config
