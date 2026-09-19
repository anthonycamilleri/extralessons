# Announcements can now be addressed to the waiting list alone. Rows sent
# before the choice existed went to everyone, which the default records.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0012_instructor_invite"),
    ]

    operations = [
        migrations.AddField(
            model_name="broadcast",
            name="audience",
            field=models.CharField(
                choices=[
                    ("EVERYONE", "Everyone with a live place"),
                    ("WAITLIST", "Waiting list only"),
                ],
                default="EVERYONE",
                max_length=20,
            ),
        ),
    ]
