"""Broadcast WhatsApp params: a guardian can be in several targeted classes,
so the broadcast context is class-agnostic — switch the default placeholder
mapping from class_title to subject. Only rows still on the old default are
touched; admin-customized mappings are left alone."""
from django.db import migrations

OLD = ["class_title", "body"]
NEW = ["subject", "body"]


def forwards(apps, schema_editor):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    NotificationTemplate.objects.filter(event="BROADCAST", wa_param_order=OLD).update(
        wa_param_order=NEW
    )


def backwards(apps, schema_editor):
    NotificationTemplate = apps.get_model("notifications", "NotificationTemplate")
    NotificationTemplate.objects.filter(event="BROADCAST", wa_param_order=NEW).update(
        wa_param_order=OLD
    )


class Migration(migrations.Migration):
    dependencies = [("notifications", "0003_notification_recipient_email_and_more")]
    operations = [migrations.RunPython(forwards, backwards)]
