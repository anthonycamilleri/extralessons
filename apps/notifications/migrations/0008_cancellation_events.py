"""Leaving a class: withdrawal receipts, cancellation requests and their outcome.

Five new events get a default template each, in the voice of 0005. The
existing SUBSCRIPTION_CANCELLED wording is narrowed to what it now means — a
cancellation the school made — but only where it still reads exactly as 0005
seeded it, so an office's own wording survives.
"""
import importlib

from django.db import migrations, models

SIGN_OFF = "\n\nWarm regards,\n{{ sender_name }}"
FOOTER = (
    "\n\n—\nYou're receiving this because you have an account on the "
    "{{ sender_name }} activities site: {{ site_url }}"
    "{% if contact_email %}\nQuestions? Just reply, or write to {{ contact_email }}.{% endif %}"
)

TEMPLATES = {
    "WITHDRAWN": (
        "Withdrawn: {{ child_name }} from {{ class_title }}",
        "Hi {{ parent_first_name }},\n\n"
        "Done — {{ child_name }} has been withdrawn from {{ class_title }} "
        "({{ schedule }}, {{ term_name }}), with immediate effect. The place is "
        "free again for another family.\n\n"
        "There's nothing else to do: we've let the provider know through the class "
        "lists, so there's no need to write to them. If this wasn't you, or you've "
        "changed your mind, you can register again from the catalogue — a new "
        "request goes to the back of the queue, though, so don't leave it too long: "
        "{{ site_url }}"
        + SIGN_OFF
        + FOOTER,
    ),
    "CANCELLATION_REQUESTED": (
        "We've received your request to cancel {{ child_name }}'s place in {{ class_title }}",
        "Hi {{ parent_first_name }},\n\n"
        "Thanks — we've received your request to cancel {{ child_name }}'s place in "
        "{{ class_title }} ({{ schedule }}, {{ term_name }}).\n\n"
        "Because the two-week withdrawal period has passed, one of our volunteers "
        "needs to confirm the cancellation with the provider first. We'll email you "
        "as soon as that's done — usually within a few days.\n\n"
        "Until you hear from us, the place is still {{ child_first_name }}'s: please "
        "keep attending, and bear in mind that the provider's fees may still be due "
        "for the current period, as set out in the terms and conditions.\n\n"
        "Changed your mind? Just reply to this email and we'll leave everything as "
        "it is. Your family page shows where things stand: {{ action_url }}"
        + SIGN_OFF
        + FOOTER,
    ),
    "CANCELLATION_CONFIRMED": (
        "Confirmed: {{ child_name }}'s place in {{ class_title }} has been cancelled",
        "Hi {{ parent_first_name }},\n\n"
        "As you asked, {{ child_name }}'s place in {{ class_title }} ({{ term_name }}) "
        "has now been cancelled. {{ child_first_name }} no longer needs to attend, and "
        "the provider has been told through us.\n\n"
        "Anything still owed to the provider for the current period is settled "
        "directly with them, on the terms you agreed. If you have any questions "
        "about that, they're the ones to ask — and if something isn't resolved, "
        "tell us.\n\n"
        "Thanks for letting us know rather than just stopping: it means another "
        "child gets the place. The catalogue is here whenever you want it: "
        "{{ site_url }}"
        + SIGN_OFF
        + FOOTER,
    ),
    "CANCELLATION_DECLINED": (
        "About cancelling {{ child_name }}'s place in {{ class_title }}",
        "Hi {{ parent_first_name }},\n\n"
        "We've looked at your request to cancel {{ child_name }}'s place in "
        "{{ class_title }} ({{ term_name }}) and, for now, we've kept the place as "
        "it is.\n\n"
        "This is usually because the provider's terms for the current period don't "
        "allow it, or because we need to talk it through with you first — not "
        "because we want to hold anyone to a class they've outgrown. One of our "
        "volunteers will be in touch; if you'd rather get ahead of that, just reply "
        "to this email.\n\n"
        "In the meantime {{ child_first_name }}'s place is unchanged: "
        "{{ action_url }}"
        + SIGN_OFF
        + FOOTER,
    ),
    # Admin alerts stay short: they're a to-do, not a letter.
    "ADMIN_CANCELLATION_REQUESTED": (
        "Cancellation requested: {{ child_name }} in {{ class_title }}",
        "{{ parent_name }} has asked to cancel {{ child_name }}'s place in "
        "{{ class_title }} ({{ term_name }}). The two-week withdrawal period ended on "
        "{{ withdrawal_deadline }}, so the place stays theirs until you confirm.\n\n"
        "Confirm the cancellation, or keep the place, from the requests page: "
        "{{ action_url }}",
    ),
}

WA_PARAMS = {
    "WITHDRAWN": ["child_name", "class_title"],
    "CANCELLATION_REQUESTED": ["child_name", "class_title"],
    "CANCELLATION_CONFIRMED": ["child_name", "class_title"],
    "CANCELLATION_DECLINED": ["child_name", "class_title"],
}

# SUBSCRIPTION_CANCELLED now only announces a cancellation the school made:
# parents who leave get WITHDRAWN or CANCELLATION_CONFIRMED instead.
SCHOOL_CANCELLED = (
    "Cancelled: {{ child_name }}'s place in {{ class_title }}",
    "Hi {{ parent_first_name }},\n\n"
    "This is to let you know that {{ child_name }}'s registration for "
    "{{ class_title }} ({{ term_name }}) has been cancelled by the PTA.\n\n"
    "We only do this when we have to — a class being reorganised, a place that "
    "couldn't be taken up, or something we've already discussed with you. If this "
    "comes as a surprise, reply to this email and we'll explain and sort it out.\n\n"
    "The provider has been told through us, so there's nothing you need to do. "
    "Looking for something else? The catalogue is here: {{ site_url }}"
    + SIGN_OFF
    + FOOTER,
)


def _previous():
    return importlib.import_module("apps.notifications.migrations.0005_friendly_templates")


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
    _reword(NotificationTemplate, _previous().TEMPLATES["SUBSCRIPTION_CANCELLED"], SCHOOL_CANCELLED)


def _reword(NotificationTemplate, source, target):
    row = NotificationTemplate.objects.filter(event="SUBSCRIPTION_CANCELLED").first()
    if row is not None and (row.email_subject, row.email_body) == tuple(source):
        row.email_subject, row.email_body = target
        row.save(update_fields=["email_subject", "email_body"])


def delete_templates(apps, schema_editor):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    NotificationTemplate.objects.filter(event__in=TEMPLATES).delete()
    _reword(NotificationTemplate, SCHOOL_CANCELLED, _previous().TEMPLATES["SUBSCRIPTION_CANCELLED"])


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0007_announcements_wording"),
    ]

    operations = [
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
                    ("SUBSCRIPTION_CANCELLED", "Enrollment cancelled by the school"),
                    ("WITHDRAWN", "Withdrawn by the family (receipt)"),
                    ("CANCELLATION_REQUESTED", "Cancellation requested (receipt)"),
                    ("CANCELLATION_CONFIRMED", "Cancellation confirmed"),
                    ("CANCELLATION_DECLINED", "Cancellation not accepted, place kept"),
                    ("CLASS_CANCELLED", "Class cancelled"),
                    ("GUARDIAN_INVITE", "Co-parent invitation"),
                    ("BROADCAST", "Announcement"),
                    ("ADMIN_NEW_REQUEST", "Admin: new enrollment request"),
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
                    ("ENROLLMENT_REQUESTED", "Enrollment requested (receipt)"),
                    ("REGISTRATION_CONFIRMED", "Registration confirmed"),
                    ("REQUEST_REJECTED", "Request not approved"),
                    ("WAITLISTED", "Added to waiting list"),
                    ("WAITLIST_OFFER", "Seat offered from waiting list"),
                    ("OFFER_EXPIRED", "Waiting-list offer expired"),
                    ("SUBSCRIPTION_CANCELLED", "Enrollment cancelled by the school"),
                    ("WITHDRAWN", "Withdrawn by the family (receipt)"),
                    ("CANCELLATION_REQUESTED", "Cancellation requested (receipt)"),
                    ("CANCELLATION_CONFIRMED", "Cancellation confirmed"),
                    ("CANCELLATION_DECLINED", "Cancellation not accepted, place kept"),
                    ("CLASS_CANCELLED", "Class cancelled"),
                    ("GUARDIAN_INVITE", "Co-parent invitation"),
                    ("BROADCAST", "Announcement"),
                    ("ADMIN_NEW_REQUEST", "Admin: new enrollment request"),
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
