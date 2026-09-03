"""Contact-form messages: a new event, its template, and a Reply-To column.

The template is created here like every other default (0002, 0005) so the
office can reword it in the admin; the Reply-To column is what lets the office
answer the parent who wrote instead of the site's sending address.
"""
from django.db import migrations, models

EVENT = "CONTACT_MESSAGE"
SUBJECT = "Website enquiry: {{ subject }}"
BODY = (
    "{{ from_name }} sent this through the contact form on {{ site_url }}.\n\n"
    "From:    {{ from_name }} <{{ from_email }}>\n"
    "{% if submitted_by %}Account: signed in as {{ submitted_by }}\n{% endif %}"
    "About:   {{ subject }}\n\n"
    "{{ body }}\n\n"
    "—\nHit reply to answer {{ from_name }} directly."
)


def create_template(apps, schema_editor):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    NotificationTemplate.objects.get_or_create(
        event=EVENT,
        defaults={
            # Admin alerts are email-only: WhatsApp templates target parents.
            "email_subject": SUBJECT,
            "email_body": BODY,
            "wa_param_order": [],
        },
    )


def delete_template(apps, schema_editor):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    NotificationTemplate.objects.filter(event=EVENT).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0005_friendly_templates"),
    ]

    operations = [
        migrations.AddField(
            model_name="notification",
            name="reply_to",
            field=models.EmailField(
                blank=True,
                help_text="Reply-To for this email, when replies should not come back to the sending address (contact-form messages reply to the writer).",
                max_length=254,
            ),
        ),
        migrations.AlterField(
            model_name="notification",
            name="event",
            field=models.CharField(
                choices=[
                    ("ENROLLMENT_REQUESTED", "Enrollment requested (receipt)"),
                    ("REGISTRATION_CONFIRMED", "Registration confirmed"),
                    ("REQUEST_REJECTED", "Request not approved"),
                    ("WAITLISTED", "Added to waiting list"),
                    ("WAITLIST_OFFER", "Seat offered from waiting list"),
                    ("OFFER_EXPIRED", "Waiting-list offer expired"),
                    ("SUBSCRIPTION_CANCELLED", "Enrollment cancelled"),
                    ("CLASS_CANCELLED", "Class cancelled"),
                    ("GUARDIAN_INVITE", "Co-parent invitation"),
                    ("BROADCAST", "Announcement"),
                    ("ADMIN_NEW_REQUEST", "Admin: new enrollment request"),
                    ("ADMIN_SEAT_FREED", "Admin: seat freed"),
                    ("ADMIN_OFFER_LAPSED", "Admin: offer declined/expired"),
                    ("CONTACT_MESSAGE", "Admin: message from the contact form"),
                ],
                max_length=30,
            ),
        ),
        migrations.AlterField(
            model_name="notificationtemplate",
            name="event",
            field=models.CharField(
                choices=[
                    ("ENROLLMENT_REQUESTED", "Enrollment requested (receipt)"),
                    ("REGISTRATION_CONFIRMED", "Registration confirmed"),
                    ("REQUEST_REJECTED", "Request not approved"),
                    ("WAITLISTED", "Added to waiting list"),
                    ("WAITLIST_OFFER", "Seat offered from waiting list"),
                    ("OFFER_EXPIRED", "Waiting-list offer expired"),
                    ("SUBSCRIPTION_CANCELLED", "Enrollment cancelled"),
                    ("CLASS_CANCELLED", "Class cancelled"),
                    ("GUARDIAN_INVITE", "Co-parent invitation"),
                    ("BROADCAST", "Announcement"),
                    ("ADMIN_NEW_REQUEST", "Admin: new enrollment request"),
                    ("ADMIN_SEAT_FREED", "Admin: seat freed"),
                    ("ADMIN_OFFER_LAPSED", "Admin: offer declined/expired"),
                    ("CONTACT_MESSAGE", "Admin: message from the contact form"),
                ],
                max_length=30,
                unique=True,
            ),
        ),
        migrations.RunPython(create_template, delete_template),
    ]
