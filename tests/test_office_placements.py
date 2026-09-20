"""The office places children itself, from the roster: adds a child to a
class, moves one to another class, ends a registration.

Covers the two new services (admin_register, transfer), the roster's controls,
the move page with its "class is full" step, who may reach what, and the one
email a move sends.
"""
import json

import pytest
from django.urls import reverse

from apps.catalog.models import ActivityClass
from apps.enrollments import services
from apps.enrollments.models import Enrollment
from apps.enrollments.services import EnrollmentError
from apps.notifications.models import Event, Notification

from .factories import (
    ActivityClassFactory,
    AdminFactory,
    ChildFactory,
    SuperAdminFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db

S = Enrollment.Status


def emails(event=None):
    rows = Notification.objects.filter(channel=Notification.Channel.EMAIL)
    return rows.filter(event=event) if event else rows


def family_emails():
    """Everything sent to parents, by event, in order."""
    return list(
        emails()
        .filter(recipient__role="PARENT")
        .order_by("id")
        .values_list("event", flat=True)
    )


def roster(cls):
    return reverse("admin:catalog_activityclass_roster", args=[cls.pk])


def move_url(enrollment):
    return reverse("admin:enrollments_enrollment_move", args=[enrollment.pk])


def cancel_url(enrollment):
    return reverse("admin:enrollments_enrollment_cancel", args=[enrollment.pk])


def register_url(cls):
    return reverse("admin:catalog_activityclass_register", args=[cls.pk])


def enrolled(cls, child=None, admin=None):
    admin = admin or SuperAdminFactory()
    return services.approve_request(services.register(child or ChildFactory(), cls), admin)


# ---------------------------------------------------------------------------
# Services
# ---------------------------------------------------------------------------


class TestAdminRegister:
    def test_enrols_straight_away_and_confirms_to_the_family(self):
        admin, cls, child = SuperAdminFactory(), ActivityClassFactory(), ChildFactory()

        enrollment = services.admin_register(child, cls, admin)

        assert enrollment.status == S.ENROLLED
        assert enrollment.decided_by == admin
        assert enrollment.approved_at and enrollment.enrolled_at
        assert enrollment.terms_accepted_at is None  # nobody ticked the box
        assert family_emails() == [Event.REGISTRATION_CONFIRMED]
        assert not emails(Event.ADMIN_NEW_REQUEST).exists()

    def test_a_full_class_is_not_a_barrier(self):
        cls = ActivityClassFactory(capacity=1)
        enrolled(cls)

        enrollment = services.admin_register(ChildFactory(), cls, SuperAdminFactory())

        assert enrollment.status == S.ENROLLED
        assert services.seats_over_capacity(cls) == 1

    def test_refuses_a_duplicate(self):
        cls, child = ActivityClassFactory(), ChildFactory()
        services.register(child, cls)

        with pytest.raises(EnrollmentError, match="already has an active registration"):
            services.admin_register(child, cls, SuperAdminFactory())

    def test_refuses_a_class_that_is_not_open(self):
        cls = ActivityClassFactory(status=ActivityClass.Status.DRAFT)

        with pytest.raises(EnrollmentError, match="not open"):
            services.admin_register(ChildFactory(), cls, SuperAdminFactory())


class TestTransfer:
    def setup_method(self):
        self.admin = SuperAdminFactory()
        self.source = ActivityClassFactory(title="Chess", capacity=3)
        self.target = ActivityClassFactory(title="Drama", capacity=3)
        self.child = ChildFactory()
        self.enrollment = services.approve_request(
            services.register(self.child, self.source, terms_accepted=True), self.admin
        )
        Notification.objects.all().delete()

    def test_ends_the_old_place_and_confirms_the_new_one(self):
        moved = services.transfer(self.enrollment, self.target, self.admin)

        self.enrollment.refresh_from_db()
        assert self.enrollment.status == S.CANCELLED
        assert self.enrollment.cancel_reason == Enrollment.CancelReason.TRANSFERRED
        assert self.enrollment.decided_by == self.admin
        assert moved.activity_class == self.target
        assert moved.status == S.ENROLLED
        assert moved.decided_by == self.admin
        assert moved.enrolled_at is not None
        # The parent's acceptance of the terms travels with the child.
        assert moved.terms_accepted_at == self.enrollment.terms_accepted_at

    def test_the_family_gets_one_email_about_the_move(self):
        services.transfer(self.enrollment, self.target, self.admin)

        assert family_emails() == [Event.ENROLLMENT_TRANSFERRED]
        email = emails(Event.ENROLLMENT_TRANSFERRED).get()
        assert "Drama" in email.rendered_subject
        assert "Chess" in email.rendered_body and "Drama" in email.rendered_body
        assert self.target.schedule_display in email.rendered_body

    def test_a_waitlisted_child_moves_into_a_confirmed_place(self):
        full = ActivityClassFactory(capacity=0)
        waiting = services.approve_request(services.register(ChildFactory(), full), self.admin)
        assert waiting.status == S.WAITLISTED

        moved = services.transfer(waiting, self.target, self.admin)

        assert moved.status == S.ENROLLED
        waiting.refresh_from_db()
        assert waiting.cancel_reason == Enrollment.CancelReason.TRANSFERRED

    def test_a_freed_seat_alerts_the_admins_when_someone_waits(self):
        self.source.capacity = 1
        self.source.save()
        services.approve_request(services.register(ChildFactory(), self.source), self.admin)
        Notification.objects.all().delete()

        services.transfer(self.enrollment, self.target, self.admin)

        assert emails(Event.ADMIN_SEAT_FREED).exists()

    def test_moving_over_capacity_is_allowed(self):
        self.target.capacity = 0
        self.target.save()

        moved = services.transfer(self.enrollment, self.target, self.admin)

        assert moved.status == S.ENROLLED
        assert services.seats_over_capacity(self.target) == 1

    def test_answers_a_pending_cancellation_request(self):
        Enrollment.objects.filter(pk=self.enrollment.pk).update(
            cancel_requested_at=self.enrollment.enrolled_at, cancel_requested_by=UserFactory()
        )
        self.enrollment.refresh_from_db()

        services.transfer(self.enrollment, self.target, self.admin)

        self.enrollment.refresh_from_db()
        assert self.enrollment.cancel_requested_at is None
        assert not Enrollment.objects.cancellation_requests_for(self.admin).exists()

    def test_refuses_the_same_class(self):
        with pytest.raises(EnrollmentError, match="already registered"):
            services.transfer(self.enrollment, self.source, self.admin)

    def test_refuses_a_closed_target(self):
        self.target.status = ActivityClass.Status.CANCELLED
        self.target.save()

        with pytest.raises(EnrollmentError, match="no longer open"):
            services.transfer(self.enrollment, self.target, self.admin)
        self.enrollment.refresh_from_db()
        assert self.enrollment.status == S.ENROLLED

    def test_refuses_when_the_child_is_already_in_the_target(self):
        services.register(self.child, self.target)

        with pytest.raises(EnrollmentError, match="already has an active registration"):
            services.transfer(self.enrollment, self.target, self.admin)

    def test_refuses_a_registration_that_is_over(self):
        services.cancel(self.enrollment, Enrollment.CancelReason.ADMIN, actor=self.admin)

        with pytest.raises(EnrollmentError, match="no longer active"):
            services.transfer(self.enrollment, self.target, self.admin)
        assert not self.target.enrollments.exists()


# ---------------------------------------------------------------------------
# The roster and its controls
# ---------------------------------------------------------------------------


class TestRosterControls:
    def setup_method(self):
        self.boss = SuperAdminFactory()
        self.cls = ActivityClassFactory(title="Chess", capacity=2)
        self.other = ActivityClassFactory(title="Drama")
        self.place = enrolled(self.cls, admin=self.boss)

    def test_the_roster_offers_add_move_and_cancel(self, client):
        client.force_login(self.boss)
        page = client.get(roster(self.cls)).content.decode()

        assert "Add a child" in page
        assert 'class="admin-autocomplete"' in page  # the picker
        assert "admin/js/autocomplete.js" in page  # ...and its script
        assert register_url(self.cls) in page
        assert move_url(self.place) in page
        assert cancel_url(self.place) in page and "Cancel place" in page
        assert reverse("admin:help_topic", args=["adding-and-moving-children"]) in page

    def test_a_class_that_is_not_open_has_no_add_box(self, client):
        self.cls.status = ActivityClass.Status.CANCELLED
        self.cls.save()
        client.force_login(self.boss)
        page = client.get(roster(self.cls)).content.decode()

        assert register_url(self.cls) not in page
        assert "not open for registration" in page

    def test_a_family_asking_to_cancel_keeps_the_dedicated_buttons(self, client):
        Enrollment.objects.filter(pk=self.place.pk).update(
            cancel_requested_at=self.place.enrolled_at
        )
        client.force_login(self.boss)
        page = client.get(roster(self.cls)).content.decode()

        assert "Confirm cancellation" in page
        assert cancel_url(self.place) not in page  # no second, blunter, cancel

    def test_waiting_list_and_offers_carry_their_own_buttons(self, client):
        full = ActivityClassFactory(capacity=0)
        waiting = services.approve_request(services.register(ChildFactory(), full), self.boss)
        offered = services.approve_request(services.register(ChildFactory(), full), self.boss)
        services.offer_seat(offered, self.boss)
        client.force_login(self.boss)
        page = client.get(roster(full)).content.decode()

        assert move_url(waiting) in page and "Remove" in page
        assert cancel_url(offered) in page and "Withdraw offer" in page

    def test_register_enrols_and_reports(self, client):
        client.force_login(self.boss)
        child = ChildFactory(first_name="Nadia")

        response = client.post(register_url(self.cls), {"child": child.pk}, follow=True)

        assert response.redirect_chain[-1][0] == roster(self.cls)
        page = response.content.decode()
        assert "Nadia Tester enrolled in Chess; the family has been told." in page
        assert Enrollment.objects.get(child=child, activity_class=self.cls).status == S.ENROLLED

    def test_register_over_capacity_says_so(self, client):
        enrolled(self.cls, admin=self.boss)  # now 2 of 2
        client.force_login(self.boss)

        page = client.post(
            register_url(self.cls), {"child": ChildFactory().pk}, follow=True
        ).content.decode()

        assert "1 seat over its capacity of 2" in page
        assert "over capacity" in page  # the roster's red pill

    def test_register_notes_an_age_outside_the_range(self, client):
        client.force_login(self.boss)
        toddler = ChildFactory(first_name="Tiny", date_of_birth=self.cls.term.start_date)

        page = client.post(
            register_url(self.cls), {"child": toddler.pk}, follow=True
        ).content.decode()

        assert "Tiny is 0 at the start of term, outside the recommended ages 5–12" in page

    def test_register_refuses_a_duplicate_with_a_message(self, client):
        client.force_login(self.boss)

        page = client.post(
            register_url(self.cls), {"child": self.place.child.pk}, follow=True
        ).content.decode()

        assert "already has an active registration" in page
        assert self.cls.enrollments.filter(child=self.place.child).count() == 1

    def test_register_without_a_child_is_a_nudge_not_a_crash(self, client):
        client.force_login(self.boss)

        page = client.post(register_url(self.cls), {}, follow=True).content.decode()

        assert "Pick a child from the list first." in page

    def test_register_is_post_only(self, client):
        client.force_login(self.boss)
        assert client.get(register_url(self.cls)).status_code == 405

    def test_cancel_from_the_roster(self, client):
        client.force_login(self.boss)
        Notification.objects.all().delete()

        assert client.get(cancel_url(self.place)).status_code == 405
        response = client.post(cancel_url(self.place), {"next": roster(self.cls)})

        assert response.status_code == 302 and response.url == roster(self.cls)
        self.place.refresh_from_db()
        assert self.place.status == S.CANCELLED
        assert self.place.cancel_reason == Enrollment.CancelReason.ADMIN
        assert self.place.decided_by == self.boss
        assert family_emails() == [Event.SUBSCRIPTION_CANCELLED]


class TestMovePage:
    def setup_method(self):
        self.boss = SuperAdminFactory()
        self.source = ActivityClassFactory(title="Chess", capacity=2)
        self.target = ActivityClassFactory(title="Drama", capacity=2)
        self.full = ActivityClassFactory(title="Judo", capacity=0)
        ActivityClassFactory(title="Old Pottery", status=ActivityClass.Status.ARCHIVED)
        self.place = enrolled(self.source, admin=self.boss)

    def test_lists_the_other_open_classes_with_their_seats(self, client):
        client.force_login(self.boss)
        page = client.get(move_url(self.place)).content.decode()

        assert "Drama" in page and "2 seats free" in page
        assert "Judo" in page and "full (0 of 0 seats taken)" in page
        assert "Old Pottery" not in page  # archived
        assert page.count('name="target"') == 2  # Chess itself is not offered

    def test_moving_to_a_class_with_room_is_one_click(self, client):
        client.force_login(self.boss)

        response = client.post(move_url(self.place), {"target": self.target.pk}, follow=True)

        assert response.redirect_chain[-1][0] == roster(self.target)
        page = response.content.decode()
        assert "Kid" in page and "moved from Chess to Drama; the family has been told." in page
        assert self.target.enrollments.filter(child=self.place.child, status=S.ENROLLED).exists()

    def test_a_full_class_asks_first(self, client):
        client.force_login(self.boss)

        response = client.post(move_url(self.place), {"target": self.full.pk})

        assert response.status_code == 200
        page = response.content.decode()
        assert "Judo is full" in page and "Move anyway (over capacity)" in page
        assert 'name="confirm_full"' in page
        self.place.refresh_from_db()
        assert self.place.status == S.ENROLLED  # nothing happened yet

        response = client.post(
            move_url(self.place), {"target": self.full.pk, "confirm_full": "1"}, follow=True
        )

        assert response.redirect_chain[-1][0] == roster(self.full)
        page = response.content.decode()
        assert "moved from Chess to Judo" in page
        assert "Judo is now 1 seat over its capacity of 0." in page
        self.place.refresh_from_db()
        assert self.place.cancel_reason == Enrollment.CancelReason.TRANSFERRED

    def test_a_child_already_in_the_target_is_refused_on_the_page(self, client):
        services.register(self.place.child, self.target)
        client.force_login(self.boss)

        response = client.post(move_url(self.place), {"target": self.target.pk})

        assert response.status_code == 200
        assert "already has an active registration for Drama" in response.content.decode()
        self.place.refresh_from_db()
        assert self.place.status == S.ENROLLED

    def test_nothing_chosen_is_an_error_not_a_move(self, client):
        client.force_login(self.boss)

        response = client.post(move_url(self.place), {})

        assert response.status_code == 200
        assert "Choose the class to move the child to." in response.content.decode()

    def test_a_registration_that_is_over_cannot_be_moved(self, client):
        services.cancel(self.place, Enrollment.CancelReason.ADMIN, actor=self.boss)
        client.force_login(self.boss)

        response = client.get(move_url(self.place), follow=True)

        assert response.redirect_chain[-1][0] == roster(self.source)
        assert "nothing to move" in response.content.decode()

    def test_no_destination_is_said_plainly(self, client):
        admin = AdminFactory()
        self.source.administrators.add(admin)
        client.force_login(admin)

        page = client.get(move_url(self.place)).content.decode()

        assert "no other open class among the ones you look after" in page


# ---------------------------------------------------------------------------
# Who may do what
# ---------------------------------------------------------------------------


class TestScope:
    def setup_method(self):
        self.boss, self.admin = SuperAdminFactory(), AdminFactory()
        self.mine = ActivityClassFactory(title="Chess", capacity=5)
        self.mine_too = ActivityClassFactory(title="Go", capacity=5)
        self.theirs = ActivityClassFactory(title="Drama", capacity=5)
        self.mine.administrators.add(self.admin)
        self.mine_too.administrators.add(self.admin)
        self.my_place = enrolled(self.mine, admin=self.boss)
        self.their_place = enrolled(self.theirs, admin=self.boss)

    def test_an_admin_works_only_their_own_roster(self, client):
        client.force_login(self.admin)

        assert client.get(move_url(self.their_place)).status_code == 404
        assert client.post(cancel_url(self.their_place)).status_code == 404
        response = client.post(register_url(self.theirs), {"child": ChildFactory().pk})
        assert response.status_code == 404
        self.their_place.refresh_from_db()
        assert self.their_place.status == S.ENROLLED

    def test_an_admin_can_only_move_into_classes_they_look_after(self, client):
        client.force_login(self.admin)
        page = client.get(move_url(self.my_place)).content.decode()
        assert "Go" in page and "Drama" not in page

        response = client.post(move_url(self.my_place), {"target": self.theirs.pk})

        assert response.status_code == 200  # the form comes back with an error
        self.my_place.refresh_from_db()
        assert self.my_place.status == S.ENROLLED
        assert not self.theirs.enrollments.filter(child=self.my_place.child).exists()

        response = client.post(move_url(self.my_place), {"target": self.mine_too.pk})

        assert response.status_code == 302 and response.url == roster(self.mine_too)

    def test_an_admin_registers_children_of_families_already_in_their_classes(self, client):
        client.force_login(self.admin)
        known = self.my_place.child
        stranger = self.their_place.child

        assert client.post(register_url(self.mine_too), {"child": known.pk}).status_code == 302
        assert self.mine_too.enrollments.filter(child=known, status=S.ENROLLED).exists()

        page = client.post(
            register_url(self.mine_too), {"child": stranger.pk}, follow=True
        ).content.decode()
        assert "Pick a child from the list first." in page
        assert not self.mine_too.enrollments.filter(child=stranger).exists()

    def test_the_picker_searches_the_same_scope(self, client):
        """The autocomplete behind "Add a child" is the child list's own
        search: a super admin finds anyone, an admin the children of families
        in their classes."""
        url = (
            reverse("admin:autocomplete")
            + "?app_label=enrollments&model_name=enrollment&field_name=child&term=Kid"
        )

        client.force_login(self.admin)
        names = {r["text"] for r in json.loads(client.get(url).content)["results"]}
        assert str(self.my_place.child) in names
        assert str(self.their_place.child) not in names

        client.force_login(self.boss)
        names = {r["text"] for r in json.loads(client.get(url).content)["results"]}
        assert {str(self.my_place.child), str(self.their_place.child)} <= names

    def test_a_parent_is_kept_out(self, client):
        client.force_login(UserFactory())

        for response in (
            client.get(move_url(self.my_place)),
            client.post(cancel_url(self.my_place)),
            client.post(register_url(self.mine), {"child": ChildFactory().pk}),
        ):
            assert response.status_code == 302 and "/admin/login/" in response["Location"]
