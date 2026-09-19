# A third audience, for the families of a class that is over or cancelled:
# cancelling a class cancels every place in it, so "everyone with a live
# place" reaches nobody there. Choices only — no stored value changes.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0013_broadcast_audience"),
    ]

    operations = [
        migrations.AlterField(
            model_name="broadcast",
            name="audience",
            field=models.CharField(
                choices=[
                    ("EVERYONE", "Everyone with a live place"),
                    ("WAITLIST", "Waiting list only"),
                    (
                        "EVER_REGISTERED",
                        "Everyone who ever had a place, cancelled places included",
                    ),
                ],
                default="EVERYONE",
                max_length=20,
            ),
        ),
    ]
