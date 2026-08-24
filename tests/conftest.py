"""Shared fixtures."""
import importlib

import pytest


@pytest.fixture
def default_notification_templates(db):
    """Restore the templates the 0002 data migration seeds.

    Tests marked `django_db(transaction=True)` truncate every table between
    tests, which takes the seeded NotificationTemplate rows with it — and
    without a template, queueing silently produces no rows at all. Non-
    transactional tests roll back instead and never lose them, so only the
    transactional suites need this.
    """
    from apps.notifications.models import NotificationTemplate

    migration = importlib.import_module(
        "apps.notifications.migrations.0002_default_templates"
    )
    for event, (subject, body) in migration.TEMPLATES.items():
        NotificationTemplate.objects.get_or_create(
            event=event,
            defaults={
                "email_subject": subject,
                "email_body": body,
                "wa_param_order": migration.WA_PARAMS.get(event, []),
            },
        )
    return NotificationTemplate.objects.all()
