"""British spelling in the event labels the admin shows.

State only: the stored values (ENROLLMENT_REQUESTED and friends) are rows in
the notification log and are deliberately untouched.
"""
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0008_cancellation_events"),
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
    ]
