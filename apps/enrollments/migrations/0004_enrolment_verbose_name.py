"""What the admin calls an enrolment, in English.

State only. The app label, the tables and the URL names stay as they were
created; only the words on screen change.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("enrollments", "0003_cancellation_requests"),
    ]

    operations = [
        migrations.AlterModelOptions(
            name="enrollment",
            options={
                "ordering": ["created_at"],
                "verbose_name": "enrolment",
                "verbose_name_plural": "enrolments",
            },
        ),
    ]
