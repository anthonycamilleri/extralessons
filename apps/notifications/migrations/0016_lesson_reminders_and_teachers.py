"""Cancelled lessons: a reminder the day before, and the teacher is told too.

Three new events, in the voice of 0005 and 0011. The family reminder is
queued by the nightly notifier (apps.enrollments.services.
send_lesson_reminders), so nothing has to run between the cancellation and
the day before. The instructor emails go to the teachers assigned to the
class, or the provider's contact address when nobody is assigned; they are
email-only, as every provider-facing message is.
"""
from django.db import migrations, models

SIGN_OFF = "\n\nWarm regards,\n{{ sender_name }}"
FAMILY_FOOTER = (
    "\n\n—\nYou're receiving this because you have an account on the "
    "{{ sender_name }} activities site: {{ site_url }}"
    "{% if contact_email %}\nQuestions? Just reply, or write to {{ contact_email }}.{% endif %}"
)
TEACHER_FOOTER = (
    "\n\n—\nYou're receiving this because you teach {{ class_title }} for "
    "{{ provider_name }} on the {{ sender_name }} activities site: {{ site_url }}"
)
FAMILIES_TOLD = (
    "{% if children_count == 1 %}We've let the family of the 1 enrolled child know."
    "{% elif children_count %}We've let the families of the {{ children_count }} "
    "enrolled children know."
    "{% else %}No children are enrolled, so no families needed telling.{% endif %}"
)

TEMPLATES = {
    "LESSON_CANCELLED_REMINDER": (
        "Reminder: no {{ class_title }} {{ when }}, {{ dates_summary }}",
        "Hi {{ parent_first_name }},\n\n"
        "Just a reminder that there's no {{ class_title }} ({{ schedule }}) for "
        "{{ child_first_name }} {{ when }}, {{ dates_summary }}.\n\n"
        "{% if reason %}The reason we've been given: {{ reason }}\n\n{% endif %}"
        "Every other date in {{ term_name }} runs as usual.\n\n"
        "If you have any questions, just reply to this email."
        + SIGN_OFF
        + FAMILY_FOOTER,
    ),
    "INSTRUCTOR_LESSON_CANCELLED": (
        "{{ class_title }}: no lesson on {{ dates_summary }}",
        "Hi {{ instructor_first_name }},\n\n"
        "{{ class_title }} ({{ schedule }}{% if location %}, {{ location }}{% endif %}) "
        "is cancelled on:\n\n"
        "{{ dates }}\n\n"
        "{% if reason %}Reason: {{ reason }}\n\n{% endif %}"
        "There's no lesson to teach "
        "{% if date_count == 1 %}that day{% else %}on those days{% endif %}. "
        + FAMILIES_TOLD
        + "\n\n"
        "The class and its dates: {{ action_url }}\n\n"
        "If this looks wrong, just reply to this email."
        + SIGN_OFF
        + TEACHER_FOOTER,
    ),
    "INSTRUCTOR_LESSON_REMINDER": (
        "Reminder: no {{ class_title }} {{ when }}, {{ dates_summary }}",
        "Hi {{ instructor_first_name }},\n\n"
        "Just a reminder that {{ class_title }} ({{ schedule }}"
        "{% if location %}, {{ location }}{% endif %}) is cancelled {{ when }}, "
        "{{ dates_summary }}, so there's no lesson to teach.\n\n"
        "{% if reason %}Reason: {{ reason }}\n\n{% endif %}"
        + FAMILIES_TOLD
        + "\n\n"
        "The class and its dates: {{ action_url }}"
        + SIGN_OFF
        + TEACHER_FOOTER,
    ),
}
WA_PARAMS = {
    # Parent-facing, so it can go by WhatsApp once a template is approved in
    # Meta; the instructor emails are email-only.
    "LESSON_CANCELLED_REMINDER": ["child_name", "class_title", "dates_summary"],
}


def create_templates(apps, schema_editor):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    for event, (subject, body) in TEMPLATES.items():
        NotificationTemplate.objects.get_or_create(
            event=event,
            defaults={
                "email_subject": subject,
                "email_body": body,
                "wa_param_order": WA_PARAMS.get(event, []),
            },
        )


def delete_templates(apps, schema_editor):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    NotificationTemplate.objects.filter(event__in=TEMPLATES).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0015_transfer_event"),
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
                    (
                        "LESSON_CANCELLED_REMINDER",
                        "Lesson cancelled: reminder the day before",
                    ),
                    ("GUARDIAN_INVITE", "Co-parent invitation"),
                    ("BROADCAST", "Announcement"),
                    ("INSTRUCTOR_INVITE", "Instructor: account invitation"),
                    ("INSTRUCTOR_LESSON_CANCELLED", "Instructor: lesson cancelled"),
                    (
                        "INSTRUCTOR_LESSON_REMINDER",
                        "Instructor: lesson cancelled, reminder the day before",
                    ),
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
                    (
                        "LESSON_CANCELLED_REMINDER",
                        "Lesson cancelled: reminder the day before",
                    ),
                    ("GUARDIAN_INVITE", "Co-parent invitation"),
                    ("BROADCAST", "Announcement"),
                    ("INSTRUCTOR_INVITE", "Instructor: account invitation"),
                    ("INSTRUCTOR_LESSON_CANCELLED", "Instructor: lesson cancelled"),
                    (
                        "INSTRUCTOR_LESSON_REMINDER",
                        "Instructor: lesson cancelled, reminder the day before",
                    ),
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
        migrations.RunPython(create_templates, delete_templates),
    ]
