"""The Django admin as the one admin surface: who may do what, the requests
badge, the roster and its CSV, bulk actions, and the old dashboard's redirects."""
import pytest
from django.urls import reverse

from apps.accounts.models import User
from apps.enrollments import services
from apps.enrollments.models import Enrollment
from apps.notifications.models import Event, Notification

from .factories import (
    ActivityClassFactory,
    AdminFactory,
    ChildFactory,
    SuperAdminFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


def _assign(cls, *admins):
    cls.administrators.add(*admins)
    return cls


class TestRoleImpliesStaff:
    def test_saving_an_admin_sets_the_staff_flag(self):
        user = User.objects.create_user("new@school.test", "pw", role=User.Role.ADMIN)
        assert user.is_staff

    def test_promoting_a_parent_sets_it_too(self):
        parent = UserFactory()
        assert not parent.is_staff
        parent.role = User.Role.ADMIN
        parent.save(update_fields=["role"])
        parent.refresh_from_db()
        assert parent.is_staff

    def test_other_roles_are_left_alone(self):
        assert not UserFactory().is_staff


class TestWhoMayDoWhat:
    ALLOWED = [
        "admin:catalog_activityclass_changelist",
        "admin:catalog_classsession_changelist",
        "admin:enrollments_enrollment_changelist",
        "admin:enrollments_attendance_changelist",
        "admin:accounts_child_changelist",
        "admin:notifications_broadcast_changelist",
        "admin:notifications_broadcast_add",
        "admin:enrollments_enrollment_requests",
    ]
    STRUCTURAL = [
        "admin:catalog_term_changelist",
        "admin:catalog_schoolyear_changelist",
        "admin:catalog_holiday_changelist",
        "admin:catalog_provider_changelist",
        "admin:accounts_user_changelist",
        "admin:accounts_siteconfig_changelist",
        "admin:accounts_guardianinvite_changelist",
        "admin:notifications_notificationtemplate_changelist",
        "admin:notifications_notification_changelist",
        "admin:media_storedfile_changelist",
        "admin:catalog_activityclass_add",
    ]

    def test_admin_gets_the_class_bound_pages_only(self, client):
        client.force_login(AdminFactory())
        for name in self.ALLOWED:
            assert client.get(reverse(name)).status_code == 200, name
        for name in self.STRUCTURAL:
            assert client.get(reverse(name)).status_code == 403, name

    def test_super_admin_gets_everything(self, client):
        client.force_login(SuperAdminFactory())
        for name in self.ALLOWED + self.STRUCTURAL:
            assert client.get(reverse(name)).status_code == 200, name

    def test_desk_links_resolve(self, client):
        """Every link in the desk nav and index panel opens, filters included."""
        client.force_login(SuperAdminFactory())
        index = client.get(reverse("admin:index")).content.decode()
        classes_url = reverse("admin:catalog_activityclass_changelist") + "?term__is_active__exact=1"
        assert classes_url in index
        assert client.get(classes_url).status_code == 200

    def test_index_lists_only_what_the_admin_may_open(self, client):
        client.force_login(AdminFactory())
        content = client.get(reverse("admin:index")).content.decode()
        assert reverse("admin:catalog_activityclass_changelist") in content
        assert reverse("admin:catalog_term_changelist") not in content
        assert reverse("admin:accounts_user_changelist") not in content

    def test_admin_class_actions_exclude_programme_setup(self, client):
        admin = AdminFactory()
        _assign(ActivityClassFactory(), admin)
        client.force_login(admin)

        content = client.get(reverse("admin:catalog_activityclass_changelist")).content.decode()

        assert 'value="publish_classes"' in content
        assert 'value="cancel_classes"' in content
        for hidden in ("assign_administrators", "clone_into_term", "archive_classes"):
            assert f'value="{hidden}"' not in content

    def test_admin_edits_their_class_but_not_who_looks_after_it(self, client):
        admin = AdminFactory()
        mine = _assign(ActivityClassFactory(), admin)
        client.force_login(admin)

        page = client.get(reverse("admin:catalog_activityclass_change", args=[mine.pk]))

        assert page.status_code == 200
        content = page.content.decode()
        assert 'name="title"' in content  # editable
        assert 'name="administrators"' not in content  # read-only for them

    def test_staff_provider_is_kept_out(self, client):
        provider = UserFactory(role=User.Role.PROVIDER, is_staff=True)
        client.force_login(provider)
        response = client.get(reverse("admin:index"))
        assert response.status_code == 302  # to the admin login


class TestBadge:
    def test_admin_header_counts_their_pending_requests(self, client):
        admin = AdminFactory()
        mine = _assign(ActivityClassFactory(), admin)
        services.register(ChildFactory(), mine)
        services.register(ChildFactory(), mine)
        services.register(ChildFactory(), ActivityClassFactory())  # someone else's
        client.force_login(admin)

        content = client.get(reverse("admin:index")).content.decode()

        assert '<span class="desk-badge desk-badge-live">2</span>' in content
        assert "requests awaiting review" in content

    def test_super_admin_counts_everything(self, client):
        services.register(ChildFactory(), ActivityClassFactory())
        services.register(ChildFactory(), ActivityClassFactory())
        client.force_login(SuperAdminFactory())

        content = client.get(reverse("admin:catalog_activityclass_changelist")).content.decode()

        assert '<span class="desk-badge desk-badge-live">2</span>' in content

    def test_site_nav_shows_the_count_to_admins_only(self, client):
        admin = AdminFactory()
        services.register(ChildFactory(), _assign(ActivityClassFactory(), admin))
        client.force_login(admin)
        assert 'class="badge badge-warn nav-badge">1</span>' in client.get(
            reverse("catalogue")
        ).content.decode()

        client.force_login(UserFactory())
        assert "nav-badge" not in client.get(reverse("catalogue")).content.decode()

    def test_login_page_has_no_badge(self, client):
        content = client.get(reverse("admin:login")).content.decode()
        assert "desk-badge" not in content


class TestRoster:
    def setup_method(self):
        self.boss = SuperAdminFactory()
        self.parent = UserFactory(first_name="Pat", last_name="Parent", phone_e164="+35699000000")
        self.cls = ActivityClassFactory(title="Chess", capacity=1)
        self.enrolled = services.approve_request(
            services.register(
                ChildFactory(first_name="Enzo", parent=self.parent, notes="Nut allergy"), self.cls
            ),
            self.boss,
        )
        self.waiting = services.approve_request(
            services.register(ChildFactory(first_name="Wanda"), self.cls), self.boss
        )
        self.pending = services.register(ChildFactory(first_name="Pending"), self.cls)

    def test_roster_shows_every_state_with_contacts(self, client):
        client.force_login(self.boss)
        content = client.get(
            reverse("admin:catalog_activityclass_roster", args=[self.cls.pk])
        ).content.decode()

        assert "Enzo" in content and "Nut allergy" in content and "+35699000000" in content
        assert "Wanda" in content and "Offer seat" in content
        assert "Pending" in content and "Approve" in content
        assert "Must be collected" in content

    def test_csv_download(self, client):
        client.force_login(self.boss)
        response = client.get(
            reverse("admin:catalog_activityclass_roster", args=[self.cls.pk]) + "?format=csv"
        )

        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/csv")
        assert 'filename="roster-' in response["Content-Disposition"]
        body = response.content.decode("utf-8-sig")
        assert body.startswith("Status,Position,Child")
        assert "Enrolled,,Enzo Tester" in body
        assert "Waiting,1,Wanda Tester" in body
        assert "Requested,,Pending Tester" in body
        assert "Pat Parent <" in body and "+35699000000" in body

    def test_offer_from_roster_returns_to_roster(self, client):
        services.cancel(self.enrolled, Enrollment.CancelReason.PARENT)
        roster = reverse("admin:catalog_activityclass_roster", args=[self.cls.pk])
        client.force_login(self.boss)

        response = client.post(
            reverse("admin:enrollments_enrollment_offer", args=[self.waiting.pk]),
            {"next": roster},
        )

        assert response.status_code == 302 and response.url == roster
        self.waiting.refresh_from_db()
        assert self.waiting.status == Enrollment.Status.OFFERED

    def test_roster_is_scoped(self, client):
        other = AdminFactory()
        _assign(ActivityClassFactory(), other)
        client.force_login(other)
        assert (
            client.get(reverse("admin:catalog_activityclass_roster", args=[self.cls.pk])).status_code
            == 404
        )


class TestBulkActions:
    def test_approve_selected_requests(self, client):
        boss = SuperAdminFactory()
        cls = ActivityClassFactory(capacity=1)
        first = services.register(ChildFactory(), cls)
        second = services.register(ChildFactory(), cls)
        client.force_login(boss)

        response = client.post(
            reverse("admin:enrollments_enrollment_changelist"),
            {"action": "approve_requests", "_selected_action": [first.pk, second.pk]},
        )

        assert response.status_code == 302
        first.refresh_from_db()
        second.refresh_from_db()
        assert first.status == Enrollment.Status.ENROLLED
        assert second.status == Enrollment.Status.WAITLISTED

    def test_bulk_actions_skip_other_admins_rows(self, client):
        admin = AdminFactory()
        mine = services.register(ChildFactory(), _assign(ActivityClassFactory(), admin))
        theirs = services.register(ChildFactory(), ActivityClassFactory())
        client.force_login(admin)

        client.post(
            reverse("admin:enrollments_enrollment_changelist"),
            {"action": "reject_requests", "_selected_action": [mine.pk, theirs.pk]},
        )

        mine.refresh_from_db()
        theirs.refresh_from_db()
        assert mine.status == Enrollment.Status.CANCELLED
        assert theirs.status == Enrollment.Status.REQUESTED


class TestChildren:
    def test_admin_sees_the_children_in_their_classes_only(self, client):
        admin = AdminFactory()
        mine = _assign(ActivityClassFactory(title="Chess"), admin)
        in_mine = ChildFactory(first_name="Chessa")
        services.register(in_mine, mine)
        ChildFactory(first_name="Stranger")
        client.force_login(admin)

        content = client.get(reverse("admin:accounts_child_changelist")).content.decode()

        assert "Chessa" in content and "Stranger" not in content
        assert "Chess (Requested" in content  # the "registered for" column

    def test_child_page_shows_registrations_and_guardian_contacts(self, client):
        admin = AdminFactory()
        parent = UserFactory(phone_e164="+35699111111")
        child = ChildFactory(parent=parent)
        mine = _assign(ActivityClassFactory(title="Chess"), admin)
        services.register(child, mine)
        client.force_login(admin)

        page = client.get(reverse("admin:accounts_child_change", args=[child.pk]))

        assert page.status_code == 200
        content = page.content.decode()
        assert "Chess" in content
        assert parent.email in content and "+35699111111" in content
        assert 'name="_save"' not in content  # read-only for a regular admin
        assert "autocomplete" not in content  # no widget that would 403 on lookup

    def test_super_admin_may_edit_children(self, client):
        child = ChildFactory()
        client.force_login(SuperAdminFactory())
        content = client.get(reverse("admin:accounts_child_change", args=[child.pk])).content.decode()
        assert 'name="_save"' in content


class TestOldDashboardRedirects:
    def test_old_urls_forward_into_the_admin(self, client):
        cls = ActivityClassFactory()
        client.force_login(SuperAdminFactory())

        assert client.get("/admin-tools/requests/").url == reverse(
            "admin:enrollments_enrollment_requests"
        )
        assert client.get(f"/admin-tools/classes/{cls.pk}/waitlist/").url == reverse(
            "admin:catalog_activityclass_roster", args=[cls.pk]
        )
        assert client.get("/admin-tools/broadcast/").url == reverse(
            "admin:notifications_broadcast_add"
        )

    def test_login_lands_admins_on_the_requests_page(self, client):
        admin = AdminFactory()
        client.force_login(admin)
        assert client.get(reverse("post_login")).url == reverse(
            "admin:enrollments_enrollment_requests"
        )

    def test_alert_emails_link_into_the_admin(self):
        admin = AdminFactory()
        cls = _assign(ActivityClassFactory(capacity=1), admin)
        services.register(ChildFactory(), cls)

        body = Notification.objects.get(event=Event.ADMIN_NEW_REQUEST).rendered_body
        assert reverse("admin:enrollments_enrollment_requests") in body
