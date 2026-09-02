"""Warmer default emails, signed by the PTA rather than "the school".

The originals (0002) read like system notices. These are written the way a
parent volunteer would write to another parent: a greeting, the facts, what
happens next, a sign-off from `{{ sender_name }}` (Site configuration → sender
name, "European School PTA" by default).

Only rows still carrying the 0002 wording are rewritten, so anything an admin
has already customised is left exactly as it is. Missing rows are created.
"""
import importlib

from django.db import migrations

SIGN_OFF = "\n\nWarm regards,\n{{ sender_name }}"
FOOTER = (
    "\n\n—\nYou're receiving this because you have an account on the "
    "{{ sender_name }} activities site: {{ site_url }}"
    "{% if contact_email %}\nQuestions? Just reply, or write to {{ contact_email }}.{% endif %}"
)

TEMPLATES = {
    "ENROLLMENT_REQUESTED": (
        "We've got {{ child_name }}'s registration for {{ class_title }} 🎉",
        "Hi {{ parent_first_name }},\n\n"
        "Thanks — we've received your registration for {{ child_name }} in "
        "{{ class_title }} with {{ provider_name }} ({{ schedule }}).\n\n"
        "Here's what happens next: one of our volunteers will look it over and confirm "
        "the place. You'll hear from us shortly — usually within a few days. If the "
        "class turns out to be full, we'll add {{ child_first_name }} to the waiting list and "
        "let you know the moment a place opens up.\n\n"
        "You can see all your registrations in one place here: {{ action_url }}"
        + SIGN_OFF
        + FOOTER,
    ),
    "REGISTRATION_CONFIRMED": (
        "Confirmed: {{ child_name }} has a place in {{ class_title }} 🎉",
        "Hi {{ parent_first_name }},\n\n"
        "Great news — we're pleased to confirm {{ child_name }}'s place in "
        "{{ class_title }}!\n\n"
        "The details:\n"
        "  • When: {{ schedule }}\n"
        "  • Who: {{ provider_name }}\n"
        "{% if location %}  • Where: {{ location }}\n{% endif %}"
        "  • Term: {{ term_name }}\n\n"
        "A couple of practical things. Fees are paid directly to {{ provider_name }} "
        "on the terms they've agreed with us — they'll be in touch about that. And if "
        "{{ child_first_name }} is in primary, please make sure they're collected promptly "
        "at the end of each session, unless you've told us on the registration form "
        "that they may go home on their own.\n\n"
        "Need to change or cancel anything? Do it from your family page rather than "
        "with the provider, so the class lists stay accurate: {{ action_url }}\n\n"
        "We hope {{ child_first_name }} has a brilliant time."
        + SIGN_OFF
        + FOOTER,
    ),
    "REQUEST_REJECTED": (
        "About {{ child_name }}'s registration for {{ class_title }}",
        "Hi {{ parent_first_name }},\n\n"
        "We're sorry — we weren't able to accept the registration for {{ child_name }} "
        "in {{ class_title }} ({{ term_name }}) this time.\n\n"
        "This is usually down to age group or class size rather than anything else, "
        "and it's not the end of the story: there are plenty of other activities in the "
        "catalogue, and we'd love to see {{ child_first_name }} in one of them.\n\n"
        "Browse what's on: {{ site_url }}\n\n"
        "If you'd like to know more about this decision, just reply to this email and "
        "we'll get back to you."
        + SIGN_OFF
        + FOOTER,
    ),
    "WAITLISTED": (
        "{{ child_name }} is on the waiting list for {{ class_title }}",
        "Hi {{ parent_first_name }},\n\n"
        "{{ class_title }} ({{ schedule }}) is full at the moment, so we've added "
        "{{ child_name }} to the waiting list. Places do come free — families move, "
        "timetables change — and when one does, we'll email you an offer.\n\n"
        "You'll then have a short window to confirm, so keep an eye on your inbox. "
        "Nothing else to do in the meantime.\n\n"
        "Your family page shows where things stand: {{ action_url }}"
        + SIGN_OFF
        + FOOTER,
    ),
    "WAITLIST_OFFER": (
        "A place has opened up for {{ child_name }} in {{ class_title }}!",
        "Hi {{ parent_first_name }},\n\n"
        "Good news — a place has just come free in {{ class_title }} ({{ schedule }}, "
        "{{ term_name }}) and we're holding it for {{ child_name }}.\n\n"
        "Please confirm or decline by {{ offer_expires_at }}. After that we'll need to "
        "offer the place to the next family on the list, so don't sit on it!\n\n"
        "Confirm or decline here: {{ action_url }}"
        + SIGN_OFF
        + FOOTER,
    ),
    "OFFER_EXPIRED": (
        "The place we held for {{ child_name }} in {{ class_title }} has gone",
        "Hi {{ parent_first_name }},\n\n"
        "We held a place for {{ child_name }} in {{ class_title }}, but didn't hear back "
        "in time, so it has now gone to the next family on the waiting list.\n\n"
        "No hard feelings — life is busy. If {{ child_first_name }} would still like to join, "
        "just register again from the catalogue and we'll put them back on the list: "
        "{{ site_url }}"
        + SIGN_OFF
        + FOOTER,
    ),
    "SUBSCRIPTION_CANCELLED": (
        "Cancelled: {{ child_name }}'s place in {{ class_title }}",
        "Hi {{ parent_first_name }},\n\n"
        "This is to confirm that {{ child_name }}'s registration for {{ class_title }} "
        "({{ term_name }}) has been cancelled.\n\n"
        "If that was you, all done — there's nothing else to do, and the provider "
        "has been told through us. If it wasn't, or you didn't expect this, reply to "
        "this email and we'll sort it out.\n\n"
        "Looking for something else? The catalogue is here: {{ site_url }}"
        + SIGN_OFF
        + FOOTER,
    ),
    "CLASS_CANCELLED": (
        "{{ class_title }} won't be running this term",
        "Hi {{ parent_first_name }},\n\n"
        "We're sorry to let you know that {{ class_title }} ({{ schedule }}, "
        "{{ term_name }}) has been cancelled, so {{ child_name }}'s registration has "
        "been removed.\n\n"
        "This is usually because a class didn't reach the minimum number of children "
        "it needs to run. We know it's disappointing — please do have a look at the "
        "other activities on offer: {{ site_url }}\n\n"
        "If you have any questions, just reply to this email."
        + SIGN_OFF
        + FOOTER,
    ),
    "GUARDIAN_INVITE": (
        "{{ parent_name }} has invited you to manage {{ child_name }}'s activities",
        "Hello,\n\n"
        "{{ parent_name }} has invited you to help manage {{ child_name }}'s "
        "extra-curricular activities at {{ school_name }}. Once you accept you'll be "
        "able to register {{ child_first_name }} for classes, respond to waiting-list offers "
        "and see their schedule — everything {{ parent_name }} can do.\n\n"
        "Accept the invitation here: {{ action_url }}\n\n"
        "If you weren't expecting this, you can safely ignore this email."
        + SIGN_OFF
        + FOOTER,
    ),
    "BROADCAST": (
        "{{ subject }}",
        "Hi {{ parent_name }},\n\n{{ body }}" + SIGN_OFF + FOOTER,
    ),
    # Admin alerts stay short: they're a to-do, not a letter.
    "ADMIN_NEW_REQUEST": (
        "New registration: {{ child_name }} → {{ class_title }}",
        "{{ parent_name }} has registered {{ child_name }} for {{ class_title }} "
        "({{ term_name }}).\n\nReview pending registrations: {{ action_url }}",
    ),
    "ADMIN_SEAT_FREED": (
        "Place freed in {{ class_title }}",
        "A place has come free in {{ class_title }} ({{ term_name }}).\n"
        "Waiting list: {{ waitlist_count }} famil{{ waitlist_count|pluralize:'y,ies' }}.\n\n"
        "Offer it from the waiting-list page: {{ action_url }}",
    ),
    "ADMIN_OFFER_LAPSED": (
        "Offer lapsed for {{ class_title }}",
        "The place offered to {{ child_name }} in {{ class_title }} was "
        "{{ lapse_reason }}. It is free again.\n\n"
        "Offer it to another family: {{ action_url }}",
    ),
}


def _previous():
    return importlib.import_module("apps.notifications.migrations.0002_default_templates")


# The WhatsApp parameter suggestions are unchanged from 0002; re-exported so
# callers (tests) can read every default from this one module.
WA_PARAMS = _previous().WA_PARAMS


def _swap(apps, source, target):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    for event, (subject, body) in target.items():
        row, created = NotificationTemplate.objects.get_or_create(
            event=event,
            defaults={
                "email_subject": subject,
                "email_body": body,
                "wa_param_order": WA_PARAMS.get(event, []),
            },
        )
        if created:
            continue
        old_subject, old_body = source.get(event, (None, None))
        if row.email_subject == old_subject and row.email_body == old_body:
            row.email_subject = subject
            row.email_body = body
            row.save(update_fields=["email_subject", "email_body"])


def forwards(apps, schema_editor):
    _swap(apps, _previous().TEMPLATES, TEMPLATES)


def backwards(apps, schema_editor):
    _swap(apps, TEMPLATES, _previous().TEMPLATES)


class Migration(migrations.Migration):
    dependencies = [("notifications", "0004_broadcast_wa_params")]
    operations = [migrations.RunPython(forwards, backwards)]
