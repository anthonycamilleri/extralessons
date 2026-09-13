"""Forms for the provider dashboard's instructor pages.

Three jobs, three forms: a manager creating an instructor's account, a
manager choosing which classes an instructor takes, and the instructor
(or their manager) filling in the profile parents see and uploading the
certificate of police conduct the school checks.
"""
from django import forms
from PIL import Image, UnidentifiedImageError

from apps.accounts.models import User
from apps.catalog.models import ActivityClass, Instructor

CERTIFICATE_MAX_BYTES = 10 * 1024 * 1024
CERTIFICATE_TYPES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}


def validate_certificate(uploaded):
    """A PDF or a picture, and really one: the extension is what the browser
    says, the first bytes are what the file is."""
    if uploaded.size > CERTIFICATE_MAX_BYTES:
        raise forms.ValidationError(
            f"That file is too big (limit {CERTIFICATE_MAX_BYTES // (1024 * 1024)} MB)."
        )
    name = uploaded.name.lower()
    extension = next((ext for ext in CERTIFICATE_TYPES if name.endswith(ext)), None)
    if extension is None:
        raise forms.ValidationError("Upload the certificate as a PDF, JPG or PNG file.")
    head = uploaded.read(8)
    uploaded.seek(0)
    if extension == ".pdf":
        if not head.startswith(b"%PDF"):
            raise forms.ValidationError("That file is not a PDF we can read.")
    else:
        try:
            Image.open(uploaded).verify()
        except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
            raise forms.ValidationError("That file is not a picture we can read.")
        finally:
            uploaded.seek(0)
    # The storage records the type it is told; a browser's guess is replaced
    # by the one the extension implies, which is what the download view sends.
    uploaded.content_type = CERTIFICATE_TYPES[extension]


class InstructorAccountForm(forms.Form):
    """A manager adds an instructor: an account is created, or an existing
    provider account is linked, and the classes are assigned in one go."""

    email = forms.EmailField(
        label="Email address",
        help_text="Their login. A new account gets an email with a link to choose a password.",
    )
    first_name = forms.CharField(max_length=150, label="First name")
    last_name = forms.CharField(max_length=150, label="Surname")
    classes = forms.ModelMultipleChoiceField(
        queryset=ActivityClass.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Classes they teach",
        help_text="You can change this at any time.",
    )

    def __init__(self, provider, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.provider = provider
        self.fields["classes"].queryset = assignable_classes(provider)
        self.existing_user = None

    def clean_email(self):
        email = User.objects.normalize_email(self.cleaned_data["email"]).lower()
        user = User.objects.filter(email__iexact=email).first()
        if user is not None:
            if user.role != User.Role.PROVIDER:
                raise forms.ValidationError(
                    "That address belongs to a parent or school account. Instructors "
                    "need their own login: use a different email address."
                )
            if Instructor.objects.filter(provider=self.provider, user=user).exists():
                raise forms.ValidationError(
                    f"{user.get_full_name() or email} is already one of your instructors."
                )
            self.existing_user = user
        return email


def assignable_classes(provider):
    """The classes an instructor can be put on: the provider's, in terms that
    have not ended. Archived and cancelled classes are history."""
    return (
        provider.classes.exclude(
            status__in=[ActivityClass.Status.ARCHIVED, ActivityClass.Status.CANCELLED]
        )
        .select_related("term")
        .order_by("-term__start_date", "weekday", "start_time", "title")
    )


class InstructorClassesForm(forms.Form):
    classes = forms.ModelMultipleChoiceField(
        queryset=ActivityClass.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Classes they teach",
    )

    def __init__(self, instructor, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.instructor = instructor
        self.fields["classes"].queryset = assignable_classes(instructor.provider)
        if not self.is_bound:
            self.initial["classes"] = list(instructor.classes.values_list("pk", flat=True))

    def save(self):
        # Only the assignable classes are touched: a class that has since been
        # archived keeps its instructors as a record of who taught it.
        assignable = set(self.fields["classes"].queryset.values_list("pk", flat=True))
        keep = set(self.instructor.classes.exclude(pk__in=assignable).values_list("pk", flat=True))
        chosen = {cls.pk for cls in self.cleaned_data["classes"]}
        self.instructor.classes.set(keep | chosen)


class InstructorProfileForm(forms.ModelForm):
    """The instructor's own page: name, a few lines, a picture, the certificate.

    The name lives on the account, not the profile, so it is edited here but
    saved to the user. The certificate is handled apart from the other fields
    (Instructor.replace_certificate) because uploading a new one has to clear
    the school's check of the old one.
    """

    first_name = forms.CharField(max_length=150, label="First name")
    last_name = forms.CharField(max_length=150, label="Surname")
    conduct_certificate = forms.FileField(
        required=False,
        label="Certificate of police conduct",
        help_text="PDF, JPG or PNG, up to 10 MB. Uploading a new file replaces the "
        "current one, and the school office checks it again.",
        validators=[validate_certificate],
    )
    conduct_certificate_issued_on = forms.DateField(
        required=False,
        label="Certificate issued on",
        widget=forms.DateInput(attrs={"type": "date"}),
    )

    field_order = [
        "first_name",
        "last_name",
        "bio",
        "photo",
        "conduct_certificate",
        "conduct_certificate_issued_on",
    ]

    class Meta:
        model = Instructor
        fields = ["bio", "photo"]
        widgets = {"bio": forms.Textarea(attrs={"rows": 6})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        user = self.instance.user
        self.fields["first_name"].initial = user.first_name
        self.fields["last_name"].initial = user.last_name
        self.fields["conduct_certificate_issued_on"].initial = (
            self.instance.conduct_certificate_issued_on
        )

    def clean(self):
        cleaned = super().clean()
        issued = cleaned.get("conduct_certificate_issued_on")
        if cleaned.get("conduct_certificate") is None and issued is not None:
            if not self.instance.has_certificate:
                self.add_error(
                    "conduct_certificate_issued_on",
                    "Upload the certificate itself as well as its date.",
                )
        return cleaned

    def save(self, commit=True):
        instructor = super().save(commit=False)
        user = instructor.user
        user.first_name = self.cleaned_data["first_name"]
        user.last_name = self.cleaned_data["last_name"]
        if commit:
            user.save(update_fields=["first_name", "last_name"])
            instructor.save()
            certificate = self.cleaned_data.get("conduct_certificate")
            issued = self.cleaned_data.get("conduct_certificate_issued_on")
            if certificate is not None:
                instructor.replace_certificate(certificate, issued_on=issued)
            elif instructor.has_certificate and issued != instructor.conduct_certificate_issued_on:
                instructor.conduct_certificate_issued_on = issued
                instructor.save(update_fields=["conduct_certificate_issued_on"])
        return instructor
