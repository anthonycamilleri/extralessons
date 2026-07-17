"""Seed editable default content for every notification event.

Admins can change all of these later under Admin → Notification templates.
"""
from django.db import migrations

SIGNATURE = "\n\n— {{ school_name }}"

TEMPLATES = {
    "ENROLLMENT_REQUESTED": (
        "Request received: {{ class_title }}",
        "Dear {{ parent_name }},\n\n"
        "We received your request to enroll {{ child_name }} in {{ class_title }} "
        "({{ schedule }}, {{ term_name }}).\n\n"
        "The school will review it shortly and you will hear from us as soon as it "
        "is confirmed." + SIGNATURE,
    ),
    "REGISTRATION_CONFIRMED": (
        "Enrolled: {{ child_name }} in {{ class_title }}",
        "Dear {{ parent_name }},\n\n"
        "Good news! {{ child_name }} is now enrolled in {{ class_title }} with "
        "{{ provider_name }} ({{ schedule }}, {{ term_name }}).\n"
        "{% if location %}Location: {{ location }}\n{% endif %}"
        "\nYou can manage this enrollment from your dashboard: {{ action_url }}" + SIGNATURE,
    ),
    "REQUEST_REJECTED": (
        "Update on your request for {{ class_title }}",
        "Dear {{ parent_name }},\n\n"
        "Unfortunately we could not accept the enrollment request for "
        "{{ child_name }} in {{ class_title }} ({{ term_name }}).\n\n"
        "Please contact the school if you have any questions." + SIGNATURE,
    ),
    "WAITLISTED": (
        "Waiting list: {{ child_name }} for {{ class_title }}",
        "Dear {{ parent_name }},\n\n"
        "{{ class_title }} ({{ schedule }}) is currently full, so {{ child_name }} "
        "has been placed on the waiting list.\n\n"
        "If a seat becomes available we will contact you with an offer. You can "
        "check the waiting list from your dashboard: {{ action_url }}" + SIGNATURE,
    ),
    "WAITLIST_OFFER": (
        "A seat is available for {{ child_name }} in {{ class_title }}!",
        "Dear {{ parent_name }},\n\n"
        "A seat has become available in {{ class_title }} ({{ schedule }}, "
        "{{ term_name }}) and it is being held for {{ child_name }}.\n\n"
        "Please confirm or decline before {{ offer_expires_at }} — after that the "
        "seat will be offered to another family.\n\n"
        "Respond here: {{ action_url }}" + SIGNATURE,
    ),
    "OFFER_EXPIRED": (
        "The seat offer for {{ class_title }} has expired",
        "Dear {{ parent_name }},\n\n"
        "The offer of a seat for {{ child_name }} in {{ class_title }} was not "
        "confirmed in time and has now expired. The seat has been released.\n\n"
        "If you are still interested, please contact the school." + SIGNATURE,
    ),
    "SUBSCRIPTION_CANCELLED": (
        "Cancelled: {{ child_name }} — {{ class_title }}",
        "Dear {{ parent_name }},\n\n"
        "The enrollment of {{ child_name }} in {{ class_title }} ({{ term_name }}) "
        "has been cancelled.\n\n"
        "If this was unexpected, please contact the school." + SIGNATURE,
    ),
    "CLASS_CANCELLED": (
        "Class cancelled: {{ class_title }}",
        "Dear {{ parent_name }},\n\n"
        "We are sorry to let you know that {{ class_title }} ({{ schedule }}, "
        "{{ term_name }}) has been cancelled. {{ child_name }}'s enrollment has "
        "been removed.\n\n"
        "Please contact the school for more information." + SIGNATURE,
    ),
    "GUARDIAN_INVITE": (
        "You have been invited to manage {{ child_name }}'s activities",
        "Hello,\n\n"
        "{{ parent_name }} invited you to co-manage {{ child_name }}'s "
        "extra-curricular activities at {{ school_name }}.\n\n"
        "Accept the invitation here: {{ action_url }}" + SIGNATURE,
    ),
    "BROADCAST": (
        "{{ subject }}",
        "Dear {{ parent_name }},\n\n{{ body }}" + SIGNATURE,
    ),
    "ADMIN_NEW_REQUEST": (
        "New enrollment request: {{ child_name }} → {{ class_title }}",
        "{{ parent_name }} requested to enroll {{ child_name }} in {{ class_title }} "
        "({{ term_name }}).\n\nReview pending requests: {{ action_url }}",
    ),
    "ADMIN_SEAT_FREED": (
        "Seat freed in {{ class_title }}",
        "A seat has been freed in {{ class_title }} ({{ term_name }}).\n"
        "Waiting list: {{ waitlist_count }} famil{{ waitlist_count|pluralize:'y,ies' }}.\n\n"
        "Offer the seat from the waiting-list page: {{ action_url }}",
    ),
    "ADMIN_OFFER_LAPSED": (
        "Offer lapsed for {{ class_title }}",
        "The seat offer for {{ child_name }} in {{ class_title }} was "
        "{{ lapse_reason }}. The seat is free again.\n\n"
        "Offer it to another family: {{ action_url }}",
    ),
}

# Suggested WhatsApp params per event; the wa_template_name stays empty until the
# school has approved templates in Meta Business Manager and fills the names in
# via the admin.
WA_PARAMS = {
    "REGISTRATION_CONFIRMED": ["child_name", "class_title", "schedule"],
    "WAITLISTED": ["child_name", "class_title"],
    "WAITLIST_OFFER": ["child_name", "class_title", "offer_expires_at"],
    "OFFER_EXPIRED": ["child_name", "class_title"],
    "SUBSCRIPTION_CANCELLED": ["child_name", "class_title"],
    "CLASS_CANCELLED": ["class_title"],
    "BROADCAST": ["class_title", "body"],
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


def remove_templates(apps, schema_editor):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    NotificationTemplate.objects.filter(event__in=TEMPLATES).delete()


class Migration(migrations.Migration):
    dependencies = [("notifications", "0001_initial")]
    operations = [migrations.RunPython(create_templates, remove_templates)]
