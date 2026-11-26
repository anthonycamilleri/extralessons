"""Extra lessons: a date added on top of the weekly schedule is announced.

Two new events, in the voice of 0011 and 0016. LESSON_ADDED tells every
family with a confirmed place, INSTRUCTOR_LESSON_ADDED tells the teachers
assigned to the class (or the provider's contact address when nobody is),
email-only as every provider-facing message is. Each date is written with
its own time and place, since an extra lesson often meets at neither the
usual time nor the usual place.
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
    "{% else %}No children are enrolled yet, so no families needed telling.{% endif %}"
)

TEMPLATES = {
    "LESSON_ADDED": (
        "Extra {{ class_title }} lesson on {{ dates_summary }}",
        "Hi {{ parent_first_name }},\n\n"
        "We've added {% if date_count == 1 %}an extra lesson{% else %}extra lessons{% endif %} "
        "of {{ class_title }} for {{ child_first_name }}:\n\n"
        "{{ dates }}\n\n"
        "{% if reason %}{{ reason }}\n\n{% endif %}"
        "{% if date_count == 1 %}This is{% else %}These are{% endif %} on top of the usual "
        "{{ schedule }}, which carry on as normal.\n\n"
        "If {{ child_first_name }} can't make it, or you have any questions, just reply "
        "to this email."
        + SIGN_OFF
        + FAMILY_FOOTER,
    ),
    "INSTRUCTOR_LESSON_ADDED": (
        "{{ class_title }}: extra lesson on {{ dates_summary }}",
        "Hi {{ instructor_first_name }},\n\n"
        "{% if date_count == 1 %}An extra lesson has{% else %}Extra lessons have{% endif %} "
        "been added to {{ class_title }}, on top of the usual {{ schedule }}:\n\n"
        "{{ dates }}\n\n"
        "{% if reason %}Note: {{ reason }}\n\n{% endif %}"
        + FAMILIES_TOLD
        + "\n\n"
        "The class and its dates: {{ action_url }}\n\n"
        "If you can't teach it, or this looks wrong, just reply to this email."
        + SIGN_OFF
        + TEACHER_FOOTER,
    ),
}
WA_PARAMS = {
    # Parent-facing, so it can go by WhatsApp once a template is approved in
    # Meta; the instructor email is email-only.
    "LESSON_ADDED": ["child_name", "class_title", "dates_summary"],
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
        ("notifications", "0016_lesson_reminders_and_teachers"),
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
                    ("LESSON_ADDED", "Extra lesson added (one or more dates)"),
                    ("GUARDIAN_INVITE", "Co-parent invitation"),
                    ("BROADCAST", "Announcement"),
                    ("INSTRUCTOR_INVITE", "Instructor: account invitation"),
                    ("INSTRUCTOR_LESSON_CANCELLED", "Instructor: lesson cancelled"),
                    (
                        "INSTRUCTOR_LESSON_REMINDER",
                        "Instructor: lesson cancelled, reminder the day before",
                    ),
                    ("INSTRUCTOR_LESSON_ADDED", "Instructor: extra lesson added"),
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
                    ("LESSON_ADDED", "Extra lesson added (one or more dates)"),
                    ("GUARDIAN_INVITE", "Co-parent invitation"),
                    ("BROADCAST", "Announcement"),
                    ("INSTRUCTOR_INVITE", "Instructor: account invitation"),
                    ("INSTRUCTOR_LESSON_CANCELLED", "Instructor: lesson cancelled"),
                    (
                        "INSTRUCTOR_LESSON_REMINDER",
                        "Instructor: lesson cancelled, reminder the day before",
                    ),
                    ("INSTRUCTOR_LESSON_ADDED", "Instructor: extra lesson added"),
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
