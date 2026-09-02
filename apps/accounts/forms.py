from django import forms
from django.contrib.auth.forms import SetPasswordForm, UserCreationForm

from .models import SCHOOL_CLASS_CHOICES, Child, GuardianInvite, User


class SignupForm(UserCreationForm):
    class Meta:
        model = User
        fields = ["email", "first_name", "last_name", "phone_e164"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["first_name"].required = True
        self.fields["last_name"].required = True

    def save(self, commit=True):
        user = super().save(commit=False)
        user.role = User.Role.PARENT
        if commit:
            user.save()
        return user


class ProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "phone_e164", "notify_email", "notify_whatsapp"]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("notify_whatsapp") and not cleaned.get("phone_e164"):
            self.add_error(
                "phone_e164", "A phone number is required for WhatsApp notifications."
            )
        return cleaned


class ChildForm(forms.ModelForm):
    class Meta:
        model = Child
        fields = [
            "first_name",
            "last_name",
            "school_class",
            "date_of_birth",
            "may_leave_alone",
            "notes",
        ]
        widgets = {"date_of_birth": forms.DateInput(attrs={"type": "date"})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Optional on the model so existing rows migrate cleanly; parents
        # always have to tell us the class.
        field = self.fields["school_class"]
        field.required = True
        field.choices = [("", "Choose a class…")] + list(SCHOOL_CLASS_CHOICES)


class GuardianInviteForm(forms.ModelForm):
    class Meta:
        model = GuardianInvite
        fields = ["email"]
        labels = {"email": "Co-parent's email address"}


class PasswordChangeForm(SetPasswordForm):
    """Change password for a logged-in user, without asking for the old one.

    Being logged in is the proof of identity here — parents rarely remember the
    password a browser filled in for them, and the "forgotten password" email
    flow already lets anyone with the inbox set a new one anyway. Django's
    SetPasswordForm is exactly this form; the subclass only exists so templates
    and tests have a stable name and the labels read as a change, not a reset.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["new_password1"].label = "New password"
        self.fields["new_password2"].label = "New password again"
