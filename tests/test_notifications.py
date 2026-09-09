import datetime

import pytest
from django.core import mail
from django.utils import timezone

from apps.enrollments import services as enrollment_services
from apps.enrollments.models import Enrollment
from apps.notifications import worker
from apps.notifications.channels.base import ChannelError
from apps.notifications.models import Broadcast, Event, Notification, NotificationTemplate
from apps.notifications.services import queue_broadcast, queue_event

from .factories import ActivityClassFactory, AdminFactory, ChildFactory, UserFactory

pytestmark = pytest.mark.django_db


def make_enrollment(parent=None, **user_kwargs):
    parent = parent or UserFactory(**user_kwargs)
    child = ChildFactory(parent=parent)
    return enrollment_services.register(child, ActivityClassFactory()), parent


class TestQueueing:
    def test_optout_rows_are_skipped_with_reason(self):
        enrollment, parent = make_enrollment(notify_email=False)
        row = Notification.objects.get(
            recipient=parent,
            channel=Notification.Channel.EMAIL,
            event=Event.ENROLLMENT_REQUESTED,
        )
        assert row.status == Notification.Status.SKIPPED
        assert "disabled" in row.skip_reason

    def test_whatsapp_skipped_without_phone(self):
        enrollment, parent = make_enrollment(notify_whatsapp=True, phone_e164="")
        row = Notification.objects.get(
            recipient=parent, channel=Notification.Channel.WHATSAPP
        )
        assert row.status == Notification.Status.SKIPPED
        assert "phone" in row.skip_reason.lower()

    def test_whatsapp_skipped_without_approved_template(self):
        enrollment, parent = make_enrollment(
            notify_whatsapp=True, phone_e164="+35699000001"
        )
        row = Notification.objects.get(
            recipient=parent, channel=Notification.Channel.WHATSAPP
        )
        # default templates ship without a Meta-approved template name
        assert row.status == Notification.Status.SKIPPED
        assert "template" in row.skip_reason.lower()

    def test_whatsapp_pending_with_template_and_params_rendered(self):
        NotificationTemplate.objects.filter(event=Event.ENROLLMENT_REQUESTED).update(
            wa_template_name="school_request_receipt",
            wa_param_order=["child_name", "class_title"],
        )
        parent = UserFactory(notify_whatsapp=True, phone_e164="+35699000001")
        child = ChildFactory(parent=parent)
        cls = ActivityClassFactory(title="Chess Club")
        enrollment_services.register(child, cls)

        row = Notification.objects.get(
            recipient=parent, channel=Notification.Channel.WHATSAPP
        )
        assert row.status == Notification.Status.PENDING
        assert row.wa_params == [child.full_name, "Chess Club"]

    def test_disabled_template_queues_nothing(self):
        NotificationTemplate.objects.filter(event=Event.ENROLLMENT_REQUESTED).update(
            enabled=False
        )
        enrollment, parent = make_enrollment()
        assert not Notification.objects.filter(
            event=Event.ENROLLMENT_REQUESTED
        ).exists()

    def test_rendered_snapshot_uses_template_content(self):
        NotificationTemplate.objects.filter(event=Event.ENROLLMENT_REQUESTED).update(
            email_subject="Custom: {{ child_name }}",
        )
        enrollment, parent = make_enrollment()
        row = Notification.objects.get(
            recipient=parent, channel=Notification.Channel.EMAIL
        )
        assert row.rendered_subject == f"Custom: {enrollment.child.full_name}"


class TestBroadcast:
    def test_broadcast_reaches_guardians_of_selected_classes_once(self):
        parent1, parent2 = UserFactory(), UserFactory()
        cls = ActivityClassFactory()
        other_cls = ActivityClassFactory()
        child = ChildFactory(parent=parent1)
        child.guardian_links.create(user=parent2)  # two guardians, same child
        enrollment_services.register(child, cls)
        enrollment_services.register(ChildFactory(), other_cls)  # not targeted

        broadcast = Broadcast.objects.create(
            sender=AdminFactory(),
            scope=Broadcast.Scope.SELECTED_CLASSES,
            subject="Trip reminder",
            body="Bring a packed lunch.",
        )
        broadcast.classes.add(cls)
        count = queue_broadcast(broadcast)

        assert count == 2  # both guardians, nobody else
        recipients = set(
            Notification.objects.filter(
                event=Event.BROADCAST, channel=Notification.Channel.EMAIL
            ).values_list("recipient", flat=True)
        )
        assert recipients == {parent1.pk, parent2.pk}
        broadcast.refresh_from_db()
        assert broadcast.sent_at is not None

    def test_rich_text_broadcast_sends_html_and_plain_text(self):
        from django.core import mail

        from apps.notifications.services import create_broadcast

        parent = UserFactory(first_name="Paula", phone_e164="+35699000000", notify_whatsapp=True)
        cls = ActivityClassFactory()
        enrollment_services.register(ChildFactory(parent=parent), cls)

        broadcast, count = create_broadcast(
            sender=AdminFactory(),
            scope=Broadcast.Scope.SELECTED_CLASSES,
            subject="Trip",
            body_html='<h1>Kit</h1><p class="ql-align-center">Bring <b>boots</b>.</p>'
            '<p><img src="https://x.org/a.jpg" alt="Boots"></p><script>x()</script>',
            classes=[cls],
        )

        assert count == 1
        assert broadcast.body_html == (
            '<h2>Kit</h2><p>Bring <b>boots</b>.</p><p><img src="https://x.org/a.jpg" alt="Boots"></p>'
        )
        assert broadcast.body == "Kit\n\nBring boots.\n\n[Boots]"

        email = Notification.objects.get(
            event=Event.BROADCAST, channel=Notification.Channel.EMAIL
        )
        assert "Hi Paula" in email.rendered_body
        assert "Bring boots." in email.rendered_body
        assert "<b>" not in email.rendered_body
        assert email.rendered_html.lstrip().startswith("<!DOCTYPE html>")
        assert "Hi Paula" in email.rendered_html
        assert "Bring <b>boots</b>." in email.rendered_html
        assert 'src="https://x.org/a.jpg"' in email.rendered_html
        assert "max-width:100%" in email.rendered_html

        whatsapp = Notification.objects.get(
            event=Event.BROADCAST, channel=Notification.Channel.WHATSAPP
        )
        assert "<" not in "".join(whatsapp.wa_params)
        assert any("Bring boots." in param for param in whatsapp.wa_params)

        # Delivery: one multipart email, text and HTML.
        worker.deliver(email)
        assert len(mail.outbox) == 1
        sent = mail.outbox[0]
        assert "Bring boots." in sent.body
        assert sent.alternatives[0][1] == "text/html"
        assert "Bring <b>boots</b>." in sent.alternatives[0][0]

    def test_plain_text_broadcast_stays_text_only(self):
        from django.core import mail

        from apps.notifications.services import create_broadcast

        parent = UserFactory()
        cls = ActivityClassFactory()
        enrollment_services.register(ChildFactory(parent=parent), cls)

        create_broadcast(
            sender=AdminFactory(),
            scope=Broadcast.Scope.SELECTED_CLASSES,
            subject="Old style",
            body="Just words.",
            classes=[cls],
        )
        email = Notification.objects.get(
            event=Event.BROADCAST, channel=Notification.Channel.EMAIL
        )
        assert email.rendered_html == ""
        worker.deliver(email)
        assert mail.outbox[0].alternatives == []

    def test_empty_rich_text_is_refused(self):
        from apps.notifications.services import create_broadcast

        with pytest.raises(ValueError):
            create_broadcast(
                sender=AdminFactory(),
                scope=Broadcast.Scope.ALL_CLASSES,
                subject="Nothing",
                body_html="<p><br></p>",
            )
        assert not Broadcast.objects.exists()


class TestWorker:
    def test_deliver_email_sends_and_marks_sent(self):
        enrollment, parent = make_enrollment()
        processed = worker.run_once()
        assert processed >= 1
        row = Notification.objects.get(
            recipient=parent, channel=Notification.Channel.EMAIL
        )
        assert row.status == Notification.Status.SENT
        assert row.sent_at is not None
        assert len(mail.outbox) >= 1
        assert enrollment.child.full_name in mail.outbox[0].body

    def test_retry_backoff_then_failed(self, monkeypatch, settings):
        settings.NOTIFIER_MAX_ATTEMPTS = 2
        enrollment, parent = make_enrollment()
        row = Notification.objects.get(
            recipient=parent, channel=Notification.Channel.EMAIL
        )

        class BrokenAdapter:
            def send(self, notification):
                raise ChannelError("smtp down")

        monkeypatch.setattr(worker, "get_adapter", lambda channel: BrokenAdapter())

        assert worker.deliver(row) == Notification.Status.PENDING
        row.refresh_from_db()
        assert row.attempts == 1
        assert row.next_attempt_at > timezone.now()

        assert worker.deliver(row) == Notification.Status.FAILED
        row.refresh_from_db()
        assert "smtp down" in row.last_error

    def test_permanent_failure_fails_immediately(self, monkeypatch):
        enrollment, parent = make_enrollment()
        row = Notification.objects.get(
            recipient=parent, channel=Notification.Channel.EMAIL
        )

        class RejectingAdapter:
            def send(self, notification):
                raise ChannelError("bad address", permanent=True)

        monkeypatch.setattr(worker, "get_adapter", lambda channel: RejectingAdapter())
        assert worker.deliver(row) == Notification.Status.FAILED
        assert row.attempts == 1

    def test_claim_skips_future_retries(self):
        enrollment, parent = make_enrollment()
        Notification.objects.filter(status=Notification.Status.PENDING).update(
            next_attempt_at=timezone.now() + datetime.timedelta(minutes=5)
        )
        assert worker.claim_batch() == []

    def test_recover_stuck_requeues(self):
        enrollment, parent = make_enrollment()
        stuck_at = timezone.now() - datetime.timedelta(minutes=30)
        Notification.objects.filter(status=Notification.Status.PENDING).update(
            status=Notification.Status.SENDING, next_attempt_at=stuck_at
        )
        assert worker.recover_stuck() >= 1
        assert Notification.objects.filter(
            status=Notification.Status.SENDING
        ).count() == 0

    def test_worker_cycle_expires_offers(self):
        admin = AdminFactory()
        cls = ActivityClassFactory(capacity=1)
        enrolled = enrollment_services.approve_request(
            enrollment_services.register(ChildFactory(), cls), admin
        )
        waitlisted = enrollment_services.approve_request(
            enrollment_services.register(ChildFactory(), cls), admin
        )
        enrollment_services.cancel(enrolled, Enrollment.CancelReason.PARENT)
        offered = enrollment_services.offer_seat(waitlisted, admin)
        Enrollment.objects.filter(pk=offered.pk).update(
            offer_expires_at=timezone.now() - datetime.timedelta(minutes=1)
        )

        worker.run_once()

        offered.refresh_from_db()
        assert offered.status == Enrollment.Status.CANCELLED
        assert offered.cancel_reason == Enrollment.CancelReason.OFFER_EXPIRED


class TestGuardianInviteDelivery:
    def test_invite_email_goes_to_address_without_account(self, client):
        parent = UserFactory()
        child = ChildFactory(parent=parent)
        client.force_login(parent)
        from django.urls import reverse

        client.post(
            reverse("child_invite_guardian", args=[child.pk]),
            {"email": "coparent@family.test"},
        )
        row = Notification.objects.get(event=Event.GUARDIAN_INVITE)
        assert row.recipient is None
        assert row.recipient_email == "coparent@family.test"

        worker.run_once()
        row.refresh_from_db()
        assert row.status == Notification.Status.SENT
        assert any("coparent@family.test" in m.to for m in mail.outbox)
