# Rich-text announcements: the formatted body on the Broadcast, and the HTML
# half of the email snapshotted on each Notification row.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0009_enrolment_event_labels"),
    ]

    operations = [
        migrations.AddField(
            model_name="broadcast",
            name="body_html",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="notification",
            name="rendered_html",
            field=models.TextField(blank=True, default=""),
        ),
    ]
