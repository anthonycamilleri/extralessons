"""The contact address gets a default, because the contact form delivers to it.

A blank address hides the form, so an existing site (where the field was
optional and never filled in) is given the default too rather than quietly
losing the new page.
"""
from django.db import migrations, models

from apps.accounts import defaults


def fill_blank_contact_email(apps, schema_editor):
    SiteConfig = apps.get_model("accounts", "SiteConfig")
    SiteConfig.objects.filter(contact_email="").update(
        contact_email=defaults.CONTACT_EMAIL
    )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0002_child_class_and_terms"),
    ]

    operations = [
        migrations.AlterField(
            model_name="siteconfig",
            name="contact_email",
            field=models.EmailField(
                blank=True,
                default="info@esljparents.eu",
                help_text="Shown to parents as the school contact address, and where the public contact form delivers. Empty hides the contact form.",
                max_length=254,
            ),
        ),
        # Nothing to undo: an address the school can edit is not worth blanking
        # again on a downgrade.
        migrations.RunPython(fill_blank_contact_email, migrations.RunPython.noop),
    ]
