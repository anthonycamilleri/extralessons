"""Classes assigned to administrators: super admins see everything, other
admins only their classes; alerts follow the same rule; and the
registration-based counts."""
import pytest
from django.contrib.auth.models import Permission
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import ActivityClass, generate_sessions
from apps.enrollments import services
from apps.enrollments.models import Enrollment
from apps.notifications.models import Broadcast, Event, Notification

from .factories import ActivityClassFactory, AdminFactory, ChildFactory, UserFactory

pytestmark = pytest.mark.django_db


def _assign(cls, *admins):
    cls.administrators.add(*admins)
    return cls


def _super():
    return AdminFactory(is_superuser=True)


class TestResponsibleAdmins:
    def test_super_admin_sees_everything_and_admins_only_their_classes(self):
        boss, admin, idle = _super(), AdminFactory(), AdminFactory()
        mine, theirs = ActivityClassFactory(), ActivityClassFactory()
        _assign(mine, admin)

        assert set(ActivityClass.objects.managed_by(boss)) == {mine, theirs}
        assert set(ActivityClass.objects.managed_by(admin)) == {mine}
        assert not ActivityClass.objects.managed_by(idle).exists()
        assert boss.is_super_admin and not admin.is_super_admin

    def test_assigned_class_alerts_its_admins_only(self):
        boss, admin, other = _super(), AdminFactory(), AdminFactory()
        mine = _assign(ActivityClassFactory(), admin)
        _assign(ActivityClassFactory(), other)

        assert set(User.objects.responsible_admins(mine)) == {admin}
        assert boss not in User.objects.responsible_admins(mine)

    def test_unassigned_class_alerts_the_super_admins(self):
        boss, admin = _super(), AdminFactory()
        _assign(ActivityClassFactory(), admin)
        unassigned = ActivityClassFactory()

        assert set(User.objects.responsible_admins(unassigned)) == {boss}

    def test_falls_back_to_every_admin_when_there_is_no_super_admin(self):
        a, b = AdminFactory(), AdminFactory()
        _assign(ActivityClassFactory(), a)
        orphan = ActivityClassFactory()

        assert set(User.objects.responsible_admins(orphan)) == {a, b}

    def test_inactive_and_non_admin_accounts_are_never_alerted(self):
        boss = _super()
        AdminFactory(is_active=False, is_superuser=True)
        UserFactory(is_superuser=True)  # a superuser parent is still a parent
        cls = ActivityClassFactory()

        assert list(User.objects.responsible_admins(cls)) == [boss]


class TestScopedAlerts:
    def test_new_request_emails_only_the_responsible_admins(self):
        boss, admin, other = _super(), AdminFactory(), AdminFactory()
        mine = _assign(ActivityClassFactory(), admin)
        _assign(ActivityClassFactory(), other)

        services.register(ChildFactory(), mine)

        recipients = set(
            Notification.objects.filter(event=Event.ADMIN_NEW_REQUEST).values_list(
                "recipient", flat=True
            )
        )
        assert recipients == {admin.pk}

    def test_seat_freed_alert_follows_the_same_rule(self):
        admin, other = AdminFactory(), AdminFactory()
        mine = _assign(ActivityClassFactory(capacity=1), admin)
        _assign(ActivityClassFactory(), other)
        enrolled = services.approve_request(services.register(ChildFactory(), mine), admin)
        services.approve_request(services.register(ChildFactory(), mine), admin)

        services.cancel(enrolled, Enrollment.CancelReason.PARENT)

        recipients = set(
            Notification.objects.filter(event=Event.ADMIN_SEAT_FREED).values_list(
                "recipient", flat=True
            )
        )
        assert recipients == {admin.pk}


class TestAdminDashboard:
    def setup_method(self):
        self.boss, self.admin = _super(), AdminFactory()
        self.mine = _assign(ActivityClassFactory(title="Chess"), self.admin)
        self.theirs = ActivityClassFactory(title="Drama")
        self.my_request = services.register(ChildFactory(), self.mine)
        self.their_request = services.register(ChildFactory(), self.theirs)

    def test_admin_sees_only_their_requests_and_classes(self, client):
        client.force_login(self.admin)
        content = client.get(reverse("admintools_requests")).content.decode()

        assert self.my_request.child.full_name in content
        assert self.their_request.child.full_name not in content
        assert "Chess" in content and "Drama" not in content
        assert "You look after" in content
        assert "scope=mine" not in content  # no switch: there is nothing to switch to

    def test_admin_with_no_classes_is_told_so(self, client):
        client.force_login(AdminFactory())
        content = client.get(reverse("admintools_requests")).content.decode()

        assert "No classes are assigned to you yet" in content
        assert "Chess" not in content and "Drama" not in content

    def test_super_admin_sees_everything_and_who_looks_after_what(self, client):
        client.force_login(self.boss)
        content = client.get(reverse("admintools_requests")).content.decode()

        assert self.my_request.child.full_name in content
        assert self.their_request.child.full_name in content
        assert "Looked after by" in content
        assert "scope=mine" not in content  # no classes of their own: no switch

    def test_admin_cannot_act_on_another_class(self, client):
        client.force_login(self.admin)

        approve = client.post(
            reverse("admintools_request_approve", args=[self.their_request.pk])
        )
        reject = client.post(reverse("admintools_request_reject", args=[self.their_request.pk]))
        waitlist = client.get(reverse("admintools_waitlist", args=[self.theirs.pk]))

        assert (approve.status_code, reject.status_code, waitlist.status_code) == (404, 404, 404)
        self.their_request.refresh_from_db()
        assert self.their_request.status == Enrollment.Status.REQUESTED

    def test_admin_can_act_on_their_own_class(self, client):
        client.force_login(self.admin)

        response = client.post(
            reverse("admintools_request_approve", args=[self.my_request.pk])
        )

        assert response.status_code == 302
        self.my_request.refresh_from_db()
        assert self.my_request.status == Enrollment.Status.ENROLLED
        assert client.get(reverse("admintools_waitlist", args=[self.mine.pk])).status_code == 200

    def test_super_admin_can_act_on_any_class(self, client):
        client.force_login(self.boss)

        response = client.post(
            reverse("admintools_request_approve", args=[self.my_request.pk])
        )

        assert response.status_code == 302
        self.my_request.refresh_from_db()
        assert self.my_request.status == Enrollment.Status.ENROLLED

    def test_admin_cannot_offer_a_seat_in_another_class(self, client):
        full = ActivityClassFactory(capacity=0)
        waiting = services.approve_request(services.register(ChildFactory(), full), self.boss)
        full.capacity = 1
        full.save()
        client.force_login(self.admin)

        response = client.post(reverse("admintools_waitlist_offer", args=[waiting.pk]))

        assert response.status_code == 404
        waiting.refresh_from_db()
        assert waiting.status == Enrollment.Status.WAITLISTED


class TestOnlyMineSwitch:
    """A super admin who also looks after classes can narrow the dashboard."""

    def setup_method(self):
        self.boss = _super()
        self.mine = _assign(ActivityClassFactory(title="Chess"), self.boss)
        self.theirs = _assign(ActivityClassFactory(title="Drama"), AdminFactory())
        self.my_request = services.register(ChildFactory(), self.mine)
        self.their_request = services.register(ChildFactory(), self.theirs)

    def test_defaults_to_all_with_the_switch_shown(self, client):
        client.force_login(self.boss)
        content = client.get(reverse("admintools_requests")).content.decode()

        assert 'href="?scope=mine"' in content and 'href="?scope=all"' in content
        assert "Chess" in content and "Drama" in content
        assert self.their_request.child.full_name in content

    def test_only_mine_narrows_and_is_remembered(self, client):
        client.force_login(self.boss)

        narrowed = client.get(reverse("admintools_requests") + "?scope=mine")
        content = narrowed.content.decode()
        assert "Chess" in content and "Drama" not in content
        assert self.their_request.child.full_name not in content
        assert narrowed.cookies["admin_scope"].value == "mine"

        remembered = client.get(reverse("admintools_requests")).content.decode()
        assert "Drama" not in remembered
        assert "Showing only the classes you look after yourself" in remembered

        widened = client.get(reverse("admintools_requests") + "?scope=all").content.decode()
        assert "Drama" in widened

    def test_switch_is_not_offered_to_plain_admins(self, client):
        plain = AdminFactory()
        _assign(self.theirs, plain)
        client.force_login(plain)

        response = client.get(reverse("admintools_requests") + "?scope=all")

        content = response.content.decode()
        assert "Chess" not in content and "Drama" in content
        assert "admin_scope" not in response.cookies

    def test_only_mine_still_allows_acting_on_any_class(self, client):
        client.force_login(self.boss)
        client.get(reverse("admintools_requests") + "?scope=mine")

        response = client.post(
            reverse("admintools_request_approve", args=[self.their_request.pk])
        )

        assert response.status_code == 302
        self.their_request.refresh_from_db()
        assert self.their_request.status == Enrollment.Status.ENROLLED

    def test_broadcast_all_classes_follows_the_switch(self, client):
        my_parent = self.my_request.child.guardians.first()
        their_parent = self.their_request.child.guardians.first()
        client.force_login(self.boss)

        content = client.get(reverse("admintools_broadcast") + "?scope=mine").content.decode()
        assert "All my classes" in content
        assert "Chess" in content and "Drama" not in content

        response = client.post(
            reverse("admintools_broadcast") + "?scope=mine",
            {"scope": "ALL_CLASSES", "subject": "Hello", "body": "Chess news."},
        )

        assert response.status_code == 302
        sent_to = set(
            Notification.objects.filter(event=Event.BROADCAST).values_list("recipient", flat=True)
        )
        assert my_parent.pk in sent_to and their_parent.pk not in sent_to
        broadcast = Broadcast.objects.get()
        assert broadcast.scope == Broadcast.Scope.SELECTED_CLASSES
        assert list(broadcast.classes.all()) == [self.mine]

    def test_broadcast_to_all_when_viewing_all(self, client):
        client.force_login(self.boss)
        content = client.get(reverse("admintools_broadcast") + "?scope=all").content.decode()
        assert "All published classes" in content

        client.post(
            reverse("admintools_broadcast") + "?scope=all",
            {"scope": "ALL_CLASSES", "subject": "Hello", "body": "Everyone."},
        )

        assert Broadcast.objects.get().scope == Broadcast.Scope.ALL_CLASSES
        assert Notification.objects.filter(event=Event.BROADCAST).count() >= 2


class TestAdminBroadcastScope:
    def test_admin_broadcast_to_all_reaches_only_their_families(self, client):
        admin = AdminFactory()
        mine = _assign(ActivityClassFactory(title="Chess"), admin)
        theirs = ActivityClassFactory(title="Drama")
        my_parent = services.register(ChildFactory(), mine).child.guardians.first()
        their_parent = services.register(ChildFactory(), theirs).child.guardians.first()
        client.force_login(admin)

        content = client.get(reverse("admintools_broadcast")).content.decode()
        assert "Chess" in content and "Drama" not in content
        assert "All my classes" in content

        response = client.post(
            reverse("admintools_broadcast"),
            {"scope": "ALL_CLASSES", "subject": "Hello", "body": "Chess news."},
        )

        assert response.status_code == 302
        sent_to = set(
            Notification.objects.filter(event=Event.BROADCAST).values_list("recipient", flat=True)
        )
        assert my_parent.pk in sent_to and their_parent.pk not in sent_to
        assert list(Broadcast.objects.get().classes.all()) == [mine]

    def test_admin_cannot_pick_another_class(self, client):
        admin = AdminFactory()
        _assign(ActivityClassFactory(), admin)
        theirs = ActivityClassFactory()
        client.force_login(admin)

        response = client.post(
            reverse("admintools_broadcast"),
            {"scope": "SELECTED_CLASSES", "classes": [theirs.pk], "subject": "x", "body": "y"},
        )

        assert response.status_code == 200  # rejected by the form, not sent
        assert not Notification.objects.filter(event=Event.BROADCAST).exists()


class TestDjangoAdminScoping:
    def test_staff_admin_sees_only_their_classes_in_the_admin(self, client):
        staff = AdminFactory(is_staff=True)
        staff.user_permissions.set(
            Permission.objects.filter(codename__in=["view_activityclass", "view_enrollment"])
        )
        mine = _assign(ActivityClassFactory(title="Chess"), staff)
        ActivityClassFactory(title="Drama")
        services.register(ChildFactory(first_name="Chessa"), mine)
        client.force_login(staff)

        classes = client.get(reverse("admin:catalog_activityclass_changelist")).content.decode()
        enrollments = client.get(reverse("admin:enrollments_enrollment_changelist")).content.decode()

        assert "Chess" in classes and "Drama" not in classes
        assert "Chessa" in enrollments

    def test_super_admin_always_sees_everything(self, admin_client, admin_user):
        _assign(ActivityClassFactory(title="Chess"), admin_user)
        ActivityClassFactory(title="Drama")

        content = admin_client.get(
            reverse("admin:catalog_activityclass_changelist")
        ).content.decode()

        assert "Chess" in content and "Drama" in content

    def test_assign_administrators_action(self, admin_client):
        a, b = AdminFactory(), AdminFactory()
        one, two = ActivityClassFactory(), ActivityClassFactory()
        one.administrators.add(a)

        response = admin_client.post(
            reverse("admin:catalog_activityclass_changelist"),
            {
                "action": "assign_administrators",
                "_selected_action": [one.pk, two.pk],
                "apply": "1",
                "administrators": [b.pk],
            },
        )
        assert response.status_code == 302
        assert set(one.administrators.all()) == {a, b}
        assert set(two.administrators.all()) == {b}

        admin_client.post(
            reverse("admin:catalog_activityclass_changelist"),
            {
                "action": "assign_administrators",
                "_selected_action": [one.pk],
                "apply": "1",
                "administrators": [b.pk],
                "replace": "on",
            },
        )
        assert set(one.administrators.all()) == {b}

    def test_assign_action_shows_confirmation_page_first(self, admin_client):
        cls = ActivityClassFactory()
        response = admin_client.post(
            reverse("admin:catalog_activityclass_changelist"),
            {"action": "assign_administrators", "_selected_action": [cls.pk]},
        )
        assert response.status_code == 200
        assert b"Assign administrators" in response.content


class TestRegistrationCounts:
    def test_registrations_count_every_live_request(self):
        admin = _super()
        cls = ActivityClassFactory(capacity=3)
        services.register(ChildFactory(), cls)  # pending
        services.approve_request(services.register(ChildFactory(), cls), admin)  # enrolled
        rejected = services.register(ChildFactory(), cls)
        services.reject_request(rejected, admin)  # cancelled: not a registration

        cls = ActivityClass.objects.with_counts().get(pk=cls.pk)

        assert (cls.registrations_count, cls.confirmed_count, cls.requested_count) == (2, 1, 1)
        assert cls.places_free == 2  # the office can still approve two
        assert cls.places_available == 1  # but parents only see one left
        assert cls.places_available_now() == 1

    def test_waiting_list_and_offers_count_as_registrations(self):
        admin = _super()
        cls = ActivityClassFactory(capacity=1)
        enrolled = services.approve_request(services.register(ChildFactory(), cls), admin)
        waiting = services.approve_request(services.register(ChildFactory(), cls), admin)
        services.cancel(enrolled, Enrollment.CancelReason.PARENT)
        services.offer_seat(waiting, admin)

        cls = ActivityClass.objects.with_counts().get(pk=cls.pk)

        assert cls.registrations_count == 1
        assert (cls.confirmed_count, cls.offered_count, cls.enrolled_count) == (0, 1, 1)
        assert cls.places_available == 0

    def test_counts_survive_the_sessions_join(self):
        """Regression: the catalogue chains with_next_session() onto
        with_counts(); the sessions join used to multiply every count."""
        admin = _super()
        cls = ActivityClassFactory(capacity=5)
        generate_sessions(cls)
        assert cls.sessions.count() > 1
        services.approve_request(services.register(ChildFactory(), cls), admin)
        services.register(ChildFactory(), cls)

        annotated = ActivityClass.objects.with_counts().with_next_session().get(pk=cls.pk)

        assert (annotated.enrolled_count, annotated.registrations_count) == (1, 2)
        assert annotated.places_available == 3

    def test_catalogue_shows_places_left_after_pending_requests(self, client):
        cls = ActivityClassFactory(capacity=2)
        services.register(ChildFactory(), cls)

        catalogue = client.get(reverse("catalogue")).content.decode()
        detail = client.get(cls.get_absolute_url()).content.decode()

        assert "1 of 2 places available" in catalogue
        assert "1 of 2 places available" in detail

    def test_catalogue_shows_full_once_requests_reach_capacity(self, client):
        cls = ActivityClassFactory(capacity=1)
        services.register(ChildFactory(), cls)  # pending, not yet confirmed

        catalogue = client.get(reverse("catalogue")).content.decode()
        detail = client.get(cls.get_absolute_url()).content.decode()

        assert "Full — waiting list" in catalogue
        assert "Join waiting list" in catalogue
        assert "Full — join the waiting list" in detail

    def test_dashboard_shows_registrations_confirmed_and_available(self, client):
        admin = _super()
        cls = ActivityClassFactory(title="Chess", capacity=3)
        services.register(ChildFactory(), cls)
        services.approve_request(services.register(ChildFactory(), cls), admin)
        client.force_login(admin)

        content = client.get(reverse("admintools_requests")).content.decode()

        assert '<td data-label="Registrations"><b>2</b></td>' in content
        assert '<td data-label="Confirmed">1 / 3' in content
        assert '<span class="badge badge-ok">1</span>' in content
        # The per-request availability badge is the approval number, not the
        # parent-facing one: the office can still enrol both children.
        assert "2 free" in content

    def test_age_confirm_step_uses_the_parent_facing_number(self, client):
        parent = UserFactory()
        child = ChildFactory(parent=parent)
        child.date_of_birth = child.date_of_birth.replace(year=child.date_of_birth.year - 10)
        child.save()
        cls = ActivityClassFactory(capacity=1, age_min=5, age_max=8)
        services.register(ChildFactory(), cls)  # pending: the one place is spoken for
        client.force_login(parent)

        response = client.post(
            reverse("enroll", args=[cls.pk]), {"child": child.pk, "terms_accepted": "1"}
        )

        assert response.status_code == 200
        assert "Yes, join the waiting list" in response.content.decode()
