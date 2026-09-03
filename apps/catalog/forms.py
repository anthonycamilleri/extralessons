from django import forms


class ContactForm(forms.Form):
    """The public contact form.

    A form, not a ModelForm: nothing a stranger types is stored. The message
    goes into the notification outbox (queue_contact_message) and lives in its
    delivery record, which is where the office looks for it anyway.
    """

    name = forms.CharField(max_length=120, label="Your name")
    email = forms.EmailField(label="Your email address")
    subject = forms.CharField(max_length=150, label="What is it about?")
    message = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 8}),
        max_length=4000,
        label="Your message",
    )
    # Honeypot: browsers never fill a hidden field, bots fill everything. A
    # filled one is answered with the same thank-you page and nothing is sent,
    # so a bot learns nothing from the difference.
    website = forms.CharField(
        required=False,
        widget=forms.HiddenInput(attrs={"autocomplete": "off", "tabindex": "-1"}),
        label="Leave this field empty",
    )

    def clean_subject(self):
        """Collapse whitespace: the subject becomes an email header, and a
        newline in a header is either a mistake or an injection attempt.
        Django refuses to send such a message at all, which would leave the
        message stuck in the outbox retrying forever."""
        return " ".join(self.cleaned_data["subject"].split())

    @property
    def looks_automated(self):
        return bool(self.cleaned_data.get("website"))
