"""Two help texts: the withdrawal window is reachable now, and "enrolment".

State only — Django emits no SQL for help_text.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0005_withdrawal_window"),
    ]

    operations = [
        migrations.AlterField(
            model_name="siteconfig",
            name="notify_admins_new_request",
            field=models.BooleanField(
                default=True,
                help_text="Email school admins when a new enrolment request arrives.",
            ),
        ),
        migrations.AlterField(
            model_name="siteconfig",
            name="withdrawal_window_days",
            field=models.PositiveSmallIntegerField(
                default=14,
                help_text="Days after registering during which a family can withdraw a confirmed place themselves, with immediate effect. After that they can only ask to cancel, and an admin confirms (or keeps the place). Requests, waiting-list entries and offers can always be withdrawn. The terms and conditions below spell this period out in words — change them to match if you change this.",
            ),
        ),
    ]
