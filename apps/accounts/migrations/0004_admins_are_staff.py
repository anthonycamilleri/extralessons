"""Every school admin works in the Django admin now; make sure the staff flag
agrees for the accounts that already exist (User.save keeps it so from here)."""
from django.db import migrations


def admins_are_staff(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    User.objects.filter(role="ADMIN", is_staff=False).update(is_staff=True)


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0003_contact_email_default"),
    ]

    operations = [
        migrations.RunPython(admins_are_staff, migrations.RunPython.noop),
    ]
