"""One lesson off: the note against the cancelled date, emailed to the families.

Marking a date cancelled used to tell nobody — the reason an office typed into
the note went nowhere. LESSON_CANCELLED carries it, in the voice of 0005, with
one message covering every date cancelled in the same breath.
"""
from django.db import migrations, models

SIGN_OFF = "\n\nWarm regards,\n{{ sender_name }}"
FOOTER = (
    "\n\n—\nYou're receiving this because you have an account on the "
    "{{ sender_name }} activities site: {{ site_url }}"
    "{% if contact_email %}\nQuestions? Just reply, or write to {{ contact_email }}.{% endif %}"
)

EVENT = "LESSON_CANCELLED"
SUBJECT = "No {{ class_title }} on {{ dates_summary }}"
BODY = (
    "Hi {{ parent_first_name }},\n\n"
    "{{ class_title }} ({{ schedule }}) is off for {{ child_first_name }} on:\n\n"
    "{{ dates }}\n\n"
    "{% if reason %}The reason we've been given: {{ reason }}\n\n{% endif %}"
    "Every other date in {{ term_name }} runs as usual, so there's nothing you need "
    "to do other than not coming along "
    "{% if date_count == 1 %}that day{% else %}on those days{% endif %}.\n\n"
    "If that's difficult, or you have any questions, just reply to this email."
    + SIGN_OFF
    + FOOTER
)
WA_PARAMS = ["child_name", "class_title", "dates_summary"]


def create_template(apps, schema_editor):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    NotificationTemplate.objects.get_or_create(
        event=EVENT,
        defaults={
            "email_subject": SUBJECT,
            "email_body": BODY,
            "wa_param_order": WA_PARAMS,
        },
    )


def delete_template(apps, schema_editor):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    NotificationTemplate.objects.filter(event=EVENT).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0010_rich_text_announcements"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="event",
            field=models.CharField(
                choices=[
                    ("ENROLLMENT_REQUESTED", "Enrolment requested (receipt)"),
                    ("REGISTRATION_CONFIRMED", "Registration confirmed"),
                    ("REQUEST_REJECTED", "Request not approved"),
                    ("WAITLISTED", "Added to waiting list"),
                    ("WAITLIST_OFFER", "Seat offered from waiting list"),
                    ("OFFER_EXPIRED", "Waiting-list offer expired"),
                    ("SUBSCRIPTION_CANCELLED", "Enrolment cancelled by the school"),
                    ("WITHDRAWN", "Withdrawn by the family (receipt)"),
                    ("CANCELLATION_REQUESTED", "Cancellation requested (receipt)"),
                    ("CANCELLATION_CONFIRMED", "Cancellation confirmed"),
                    ("CANCELLATION_DECLINED", "Cancellation not accepted, place kept"),
                    ("CLASS_CANCELLED", "Class cancelled"),
                    ("LESSON_CANCELLED", "Lesson cancelled (one or more dates off)"),
                    ("GUARDIAN_INVITE", "Co-parent invitation"),
                    ("BROADCAST", "Announcement"),
                    ("ADMIN_NEW_REQUEST", "Admin: new enrolment request"),
                    ("ADMIN_SEAT_FREED", "Admin: seat freed"),
                    ("ADMIN_OFFER_LAPSED", "Admin: offer declined/expired"),
                    (
                        "ADMIN_CANCELLATION_REQUESTED",
                        "Admin: family asks to cancel a place",
                    ),
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
                    ("ENROLLMENT_REQUESTED", "Enrolment requested (receipt)"),
                    ("REGISTRATION_CONFIRMED", "Registration confirmed"),
                    ("REQUEST_REJECTED", "Request not approved"),
                    ("WAITLISTED", "Added to waiting list"),
                    ("WAITLIST_OFFER", "Seat offered from waiting list"),
                    ("OFFER_EXPIRED", "Waiting-list offer expired"),
                    ("SUBSCRIPTION_CANCELLED", "Enrolment cancelled by the school"),
                    ("WITHDRAWN", "Withdrawn by the family (receipt)"),
                    ("CANCELLATION_REQUESTED", "Cancellation requested (receipt)"),
                    ("CANCELLATION_CONFIRMED", "Cancellation confirmed"),
                    ("CANCELLATION_DECLINED", "Cancellation not accepted, place kept"),
                    ("CLASS_CANCELLED", "Class cancelled"),
                    ("LESSON_CANCELLED", "Lesson cancelled (one or more dates off)"),
                    ("GUARDIAN_INVITE", "Co-parent invitation"),
                    ("BROADCAST", "Announcement"),
                    ("ADMIN_NEW_REQUEST", "Admin: new enrolment request"),
                    ("ADMIN_SEAT_FREED", "Admin: seat freed"),
                    ("ADMIN_OFFER_LAPSED", "Admin: offer declined/expired"),
                    (
                        "ADMIN_CANCELLATION_REQUESTED",
                        "Admin: family asks to cancel a place",
                    ),
                    ("CONTACT_MESSAGE", "Admin: message from the contact form"),
                ],
                max_length=30,
                unique=True,
            ),
        ),
        migrations.RunPython(create_template, delete_template),
    ]
