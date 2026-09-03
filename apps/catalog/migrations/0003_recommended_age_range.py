"""The age range became a recommendation: help text only, no schema change."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("catalog", "0002_school_years_and_holidays"),
    ]

    operations = [
        migrations.AlterField(
            model_name="activityclass",
            name="age_max",
            field=models.PositiveSmallIntegerField(
                help_text="Oldest age this class is recommended for. Parents can still register a child outside the range after confirming a warning."
            ),
        ),
        migrations.AlterField(
            model_name="activityclass",
            name="age_min",
            field=models.PositiveSmallIntegerField(
                help_text="Youngest age this class is recommended for."
            ),
        ),
    ]
