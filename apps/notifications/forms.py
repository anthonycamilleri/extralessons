"""The rich-text message field shared by both announcement composers."""
from django import forms
from django.core.exceptions import ValidationError
from django.urls import reverse

from . import richtext


class RichTextWidget(forms.Textarea):
    """A textarea that static/js/richtext.js turns into a Quill editor.

    The textarea carries the HTML and is what the form posts; the script hides
    it and writes the editor's content back on submit. Without JavaScript the
    textarea itself is the (raw HTML) editor, and the server-side clean-up is
    the real boundary either way.
    """

    class Media:
        css = {"all": ["vendor/quill/quill.snow.css", "css/richtext.css"]}
        js = ["vendor/quill/quill.js", "js/richtext.js"]

    def use_required_attribute(self, initial):
        # A hidden required control makes the browser refuse to submit the
        # form with a message it cannot show; the field's own validation covers it.
        return False

    def get_context(self, name, value, attrs):
        attrs = {**(attrs or {}), "data-richtext": "1"}
        # Reversed at render time: the widget module must not import URLs.
        attrs["data-upload-url"] = reverse("announcement_image_upload")
        return super().get_context(name, value, attrs)


class RichTextField(forms.CharField):
    """Cleaned announcement HTML; refuses a message with nothing in it."""

    widget = RichTextWidget

    def __init__(self, **kwargs):
        kwargs.setdefault("label", "Message")
        kwargs.setdefault("strip", False)
        # Not "required" in CharField's sense: an empty editor and one holding
        # only blank lines should fail the same way, with the same words.
        kwargs.setdefault("required", False)
        super().__init__(**kwargs)

    def clean(self, value):
        value = super().clean(value)
        html = richtext.clean_html(value or "")
        if richtext.is_blank(html):
            raise ValidationError("Write a message.", code="blank")
        return html
