from django import forms
from django.contrib.auth.forms import UserCreationForm

from .models import Child, GuardianInvite, User


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
        fields = ["first_name", "last_name", "date_of_birth", "notes"]
        widgets = {"date_of_birth": forms.DateInput(attrs={"type": "date"})}


class GuardianInviteForm(forms.ModelForm):
    class Meta:
        model = GuardianInvite
        fields = ["email"]
        labels = {"email": "Co-parent's email address"}
