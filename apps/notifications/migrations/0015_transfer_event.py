"""Moved to another class: the one email a family gets when the office moves
their child from one class to another.

A move used to be impossible in one step; done as a cancellation plus a new
registration it would have sent two emails, the first of them alarming.
ENROLLMENT_TRANSFERRED says what happened in one message, in the voice of
0005: the new details, and that the old class is simply over for the child.
"""
from django.db import migrations, models

SIGN_OFF = "\n\nWarm regards,\n{{ sender_name }}"
FOOTER = (
    "\n\n—\nYou're receiving this because you have an account on the "
    "{{ sender_name }} activities site: {{ site_url }}"
    "{% if contact_email %}\nQuestions? Just reply, or write to {{ contact_email }}.{% endif %}"
)

EVENT = "ENROLLMENT_TRANSFERRED"
SUBJECT = "{{ child_name }} has moved to {{ class_title }}"
BODY = (
    "Hi {{ parent_first_name }},\n\n"
    "As arranged, {{ child_name }}'s place has moved from {{ from_class_title }} "
    "({{ from_schedule }}) to {{ class_title }}.\n\n"
    "The new details:\n"
    "  • When: {{ schedule }}\n"
    "  • Who: {{ provider_name }}\n"
    "{% if location %}  • Where: {{ location }}\n{% endif %}"
    "  • Term: {{ term_name }}\n\n"
    "{{ child_first_name }} no longer has a place in {{ from_class_title }}, so there's "
    "no need to go along to that one any more. The class lists have been updated, "
    "so there's nothing you need to tell the providers.\n\n"
    "Fees are paid directly to {{ provider_name }} on the terms they've agreed with "
    "us — they'll be in touch about that. If anything was already paid for "
    "{{ from_class_title }}, {{ from_provider_name }} is the one to sort it out with, "
    "and if something isn't resolved, tell us.\n\n"
    "If this isn't what you expected, just reply to this email. Your family page "
    "shows where things stand: {{ action_url }}"
    + SIGN_OFF
    + FOOTER
)
WA_PARAMS = ["child_name", "from_class_title", "class_title", "schedule"]


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
        ("notifications", "0014_broadcast_ever_registered"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="event",
            field=models.CharField(
                choices=[
                    ("ENROLLMENT_REQUESTED", "Enrolment requested (receipt)"),
                    ("REGISTRATION_CONFIRMED", "Registration confirmed"),
                    ("ENROLLMENT_TRANSFERRED", "Moved to another class"),
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
                    ("INSTRUCTOR_INVITE", "Instructor: account invitation"),
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
                    ("ENROLLMENT_TRANSFERRED", "Moved to another class"),
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
                    ("INSTRUCTOR_INVITE", "Instructor: account invitation"),
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
