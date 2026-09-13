"""An instructor's account invitation: the one email a brand-new account gets.

A provider creates the account from its dashboard; this tells the instructor
it exists and hands them Django's own set-a-password link. Created here like
every other default (0002, 0005) so the office can reword it in the admin.
"""
from django.db import migrations, models

SIGN_OFF = "\n\nWarm regards,\n{{ sender_name }}"

EVENT = "INSTRUCTOR_INVITE"
SUBJECT = "Your instructor account for {{ provider_name }} at {{ school_name }}"
BODY = (
    "Hi {{ instructor_first_name }},\n\n"
    "{{ inviter_name }} has set you up as an instructor with {{ provider_name }} on the "
    "{{ school_name }} activities site. Once you are in, you will see the classes assigned "
    "to you, the children in them, and you can take the register and write to the families.\n\n"
    "Choose a password to get started:\n\n{{ action_url }}\n\n"
    "The link works for three days. If it has expired, ask {{ inviter_name }} to send "
    "it again, or use \"Forgotten your password?\" on the login page: {{ login_url }}\n\n"
    "Your login is your email address, {{ login_email }}.\n\n"
    "Once you are in, please fill in your short profile and upload your certificate of "
    "police conduct; the school office checks it before your profile is shown to parents."
    + SIGN_OFF
)


def create_template(apps, schema_editor):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    NotificationTemplate.objects.get_or_create(
        event=EVENT,
        defaults={
            # Provider-facing, email only: WhatsApp templates target parents.
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
        ("notifications", "0011_lesson_cancelled_event"),
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

