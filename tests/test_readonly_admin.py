"""The read-only admin: sees everything a super admin sees, changes nothing.

Its own role (User.Role.READONLY_ADMIN) rather than a flag on the admin one, so
the acting-admin machinery (class assignment, alerts, the verbs the permission
mixin hands out) never reaches it. What it may do comes from User.has_perm:
every model's view permission, nothing else.
"""
import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse

from apps.accounts.models import User
from apps.catalog.models import ActivityClass
from apps.enrollments import services
from apps.enrollments.models import Enrollment
from apps.notifications.models import Broadcast

from .factories import (
    ActivityClassFactory,
    AdminFactory,
    ChildFactory,
    ReadOnlyAdminFactory,
    SuperAdminFactory,
    UserFactory,
)
from .test_school_admin import TestWhoMayDoWhat

pytestmark = pytest.mark.django_db


class TestTheRole:
    def test_saving_sets_the_staff_flag(self):
        user = User.objects.create_user("look@school.test", "pw", role=User.Role.READONLY_ADMIN)
        assert user.is_staff

    def test_is_an_admin_and_a_parent_but_not_an_acting_admin(self):
        viewer = ReadOnlyAdminFactory()
        assert viewer.uses_admin
        assert viewer.is_read_only_admin
        assert viewer.is_parent
        assert viewer.sees_everything
        assert not viewer.is_super_admin
        assert viewer not in User.objects.active_admins()

    def test_holds_every_view_permission_and_nothing_else(self):
        viewer = ReadOnlyAdminFactory()
        assert viewer.has_perm("catalog.view_activityclass")
        assert viewer.has_perm("accounts.view_user")
        assert viewer.has_perm("accounts.view_siteconfig")
        assert viewer.has_module_perms("notifications")
        for verb in ("add", "change", "delete"):
            assert not viewer.has_perm(f"catalog.{verb}_activityclass"), verb
        assert not viewer.has_perm("notifications.add_broadcast")

    def test_an_inactive_account_holds_nothing(self):
        viewer = ReadOnlyAdminFactory(is_active=False)
        assert not viewer.has_perm("catalog.view_activityclass")
        assert not viewer.has_module_perms("catalog")

    def test_superuser_status_is_refused_and_ignored(self):
        viewer = ReadOnlyAdminFactory.build(is_superuser=True)
        with pytest.raises(ValidationError) as excinfo:
            viewer.full_clean()
        assert "is_superuser" in excinfo.value.message_dict
        # Even if it slipped into the database, read-only stays read-only.
        assert not viewer.has_perm("catalog.change_activityclass")
        assert viewer.has_perm("catalog.view_activityclass")

    def test_acting_admins_may_be_superusers(self):
        SuperAdminFactory.build().full_clean(exclude=["password"])


class TestSeesEverything:
    def test_every_admin_page_opens_as_a_viewer(self, client):
        client.force_login(ReadOnlyAdminFactory())
        for name in TestWhoMayDoWhat.ALLOWED + TestWhoMayDoWhat.STRUCTURAL:
            if name.endswith("_add"):
                continue
            assert client.get(reverse(name)).status_code == 200, name

    def test_the_index_lists_every_model_without_add_links(self, client):
        client.force_login(ReadOnlyAdminFactory())
        content = client.get(reverse("admin:index")).content.decode()
        assert reverse("admin:catalog_term_changelist") in content
        assert reverse("admin:accounts_user_changelist") in content
        assert reverse("admin:accounts_siteconfig_changelist") in content
        assert reverse("admin:catalog_activityclass_add") not in content
        assert "Read-only" in content
        assert reverse("admin:notifications_broadcast_add") not in content

    def test_sees_every_class_child_and_enrolment_unassigned(self, client):
        viewer = ReadOnlyAdminFactory()
        admin = AdminFactory()
        theirs = ActivityClassFactory(title="Chess Club")
        theirs.administrators.add(admin)
        other = ActivityClassFactory(title="Robotics")
        services.register(ChildFactory(first_name="Ada"), theirs)
        services.register(ChildFactory(first_name="Bea"), other)
        ChildFactory(first_name="Cleo")  # registered for nothing at all

        assert set(ActivityClass.objects.managed_by(viewer)) == {theirs, other}
        assert Enrollment.objects.desk_count(viewer) == 2

        client.force_login(viewer)
        classes = client.get(reverse("admin:catalog_activityclass_changelist")).content.decode()
        assert "Chess Club" in classes and "Robotics" in classes
        children = client.get(reverse("admin:accounts_child_changelist")).content.decode()
        assert "Ada" in children and "Bea" in children and "Cleo" in children
        requests_page = client.get(reverse("admin:enrollments_enrollment_requests")).content.decode()
        assert "Ada" in requests_page and "Bea" in requests_page
        assert "No classes are assigned to you" not in requests_page
        assert "read-only access" in requests_page

    def test_change_pages_open_read_only(self, client):
        viewer = ReadOnlyAdminFactory()
        cls = ActivityClassFactory(title="Chess Club")
        parent = UserFactory()
        client.force_login(viewer)

        page = client.get(reverse("admin:catalog_activityclass_change", args=[cls.pk]))
        assert page.status_code == 200
        content = page.content.decode()
        assert "Chess Club" in content
        assert 'name="title"' not in content  # rendered as text, not a field
        assert 'name="_save"' not in content
        assert reverse("admin:catalog_activityclass_announce", args=[cls.pk]) not in content

        account = client.get(reverse("admin:accounts_user_change", args=[parent.pk]))
        assert account.status_code == 200
        assert 'name="_save"' not in account.content.decode()

        child = ChildFactory(first_name="Ada", parent=parent)
        services.register(child, cls)
        page = client.get(reverse("admin:accounts_child_change", args=[child.pk]))
        assert page.status_code == 200
        content = page.content.decode()
        assert "Ada" in content and parent.email in content and "Chess Club" in content
        assert 'name="_save"' not in content

    def test_sent_announcements_are_all_visible(self, client):
        admin = AdminFactory()
        cls = ActivityClassFactory()
        cls.administrators.add(admin)
        Broadcast.objects.create(
            sender=admin,
            scope=Broadcast.Scope.SELECTED_CLASSES,
            subject="Kit reminder",
            body="Bring boots",
        )
        client.force_login(ReadOnlyAdminFactory())
        content = client.get(reverse("admin:notifications_broadcast_changelist")).content.decode()
        assert "Kit reminder" in content

    def test_certificate_opens_for_the_read_only_office(self):
        from apps.catalog.models import Instructor, Provider

        provider = Provider.objects.create(name="AllStars")
        instructor = Instructor.objects.create(provider=provider, user=UserFactory())
        assert instructor.may_be_opened_by(ReadOnlyAdminFactory())
        assert not instructor.may_be_opened_by(UserFactory())


class TestChangesNothing:
    def setup_method(self):
        self.viewer = ReadOnlyAdminFactory()
        self.boss = SuperAdminFactory()
        self.cls = ActivityClassFactory(title="Chess", capacity=1)
        # Enzo takes the one seat, Wanda lands on the waiting list, Pat is
        # still waiting for a decision.
        services.approve_request(
            services.register(ChildFactory(first_name="Enzo"), self.cls), self.boss
        )
        self.waiting = services.approve_request(
            services.register(ChildFactory(first_name="Wanda"), self.cls), self.boss
        )
        self.pending = services.register(ChildFactory(first_name="Pat"), self.cls)

    def test_add_pages_are_forbidden(self, client):
        client.force_login(self.viewer)
        for name in TestWhoMayDoWhat.ALLOWED + TestWhoMayDoWhat.STRUCTURAL:
            if name.endswith("_add"):
                assert client.get(reverse(name)).status_code == 403, name

    def test_saving_a_change_form_is_refused(self, client):
        client.force_login(self.viewer)
        response = client.post(
            reverse("admin:catalog_activityclass_change", args=[self.cls.pk]),
            {"title": "Renamed"},
        )
        assert response.status_code == 403
        self.cls.refresh_from_db()
        assert self.cls.title == "Chess"

    def test_desk_pages_show_no_buttons(self, client):
        client.force_login(self.viewer)
        requests_page = client.get(reverse("admin:enrollments_enrollment_requests")).content.decode()
        assert "Pat" in requests_page
        assert ">Approve<" not in requests_page and ">Reject<" not in requests_page
        assert reverse("admin:notifications_broadcast_add") not in requests_page

        roster = client.get(
            reverse("admin:catalog_activityclass_roster", args=[self.cls.pk])
        ).content.decode()
        assert "Enzo" in roster and "Wanda" in roster and "Pat" in roster
        assert "Offer seat" not in roster
        assert ">Approve<" not in roster
        assert "View class" in roster
        assert reverse("admin:catalog_activityclass_announce", args=[self.cls.pk]) not in roster
        # The office's own placement controls: add, move, remove.
        assert "Add a child" not in roster and "not open for registration" not in roster
        assert reverse("admin:catalog_activityclass_register", args=[self.cls.pk]) not in roster
        assert "Move…" not in roster
        assert "Cancel place" not in roster and "Remove" not in roster

    def test_csv_download_still_works(self, client):
        client.force_login(self.viewer)
        response = client.get(
            reverse("admin:catalog_activityclass_roster", args=[self.cls.pk]) + "?format=csv"
        )
        assert response.status_code == 200
        assert "Wanda" in response.content.decode()

    def test_transitions_are_forbidden(self, client):
        client.force_login(self.viewer)
        for name, enrollment in (
            ("admin:enrollments_enrollment_approve", self.pending),
            ("admin:enrollments_enrollment_reject", self.pending),
            ("admin:enrollments_enrollment_offer", self.waiting),
            ("admin:enrollments_enrollment_cancel", self.waiting),
            ("admin:enrollments_enrollment_move", self.waiting),
        ):
            assert client.post(reverse(name, args=[enrollment.pk])).status_code == 403, name
        assert (
            client.get(reverse("admin:enrollments_enrollment_move", args=[self.waiting.pk])).status_code
            == 403
        )
        self.pending.refresh_from_db()
        assert self.pending.status == Enrollment.Status.REQUESTED
        self.waiting.refresh_from_db()
        assert self.waiting.status == Enrollment.Status.WAITLISTED

    def test_adding_a_child_is_forbidden(self, client):
        client.force_login(self.viewer)
        newcomer = ChildFactory(first_name="Nia")
        response = client.post(
            reverse("admin:catalog_activityclass_register", args=[self.cls.pk]),
            {"child": newcomer.pk},
        )
        assert response.status_code == 403
        assert not Enrollment.objects.filter(child=newcomer).exists()

    def test_announcing_is_forbidden(self, client):
        client.force_login(self.viewer)
        assert (
            client.get(reverse("admin:catalog_activityclass_announce", args=[self.cls.pk])).status_code
            == 403
        )
        assert client.get(reverse("admin:notifications_broadcast_add")).status_code == 403
        assert Broadcast.objects.count() == 0

    def test_bulk_actions_are_neither_offered_nor_run(self, client):
        client.force_login(self.viewer)
        changelist = reverse("admin:enrollments_enrollment_changelist")
        content = client.get(changelist).content.decode()
        assert 'value="approve_requests"' not in content
        assert 'name="action"' not in content

        response = client.post(
            changelist,
            {"action": "approve_requests", "_selected_action": [self.pending.pk], "index": 0},
        )
        assert response.status_code in (200, 302)
        self.pending.refresh_from_db()
        assert self.pending.status == Enrollment.Status.REQUESTED

        classes = client.get(reverse("admin:catalog_activityclass_changelist")).content.decode()
        for action in ("publish_classes", "cancel_classes", "assign_administrators"):
            assert f'value="{action}"' not in classes
        assert reverse("admin:catalog_activityclass_announce", args=[self.cls.pk]) not in classes
        assert reverse("admin:catalog_activityclass_roster", args=[self.cls.pk]) in classes

    def test_certificate_action_stays_with_acting_admins(self, client):
        from apps.catalog.models import Instructor

        instructor = Instructor.objects.create(provider=self.cls.provider, user=UserFactory())
        self.cls.instructors.add(instructor)
        client.force_login(self.viewer)
        content = client.get(reverse("admin:catalog_instructor_changelist")).content.decode()
        assert 'value="mark_certificate_checked"' not in content

        admin = AdminFactory()
        self.cls.administrators.add(admin)
        client.force_login(admin)
        content = client.get(reverse("admin:catalog_instructor_changelist")).content.decode()
        assert 'value="mark_certificate_checked"' in content

    def test_structural_actions_need_the_change_verb(self, client):
        from tests.factories import SchoolYearFactory

        SchoolYearFactory()
        client.force_login(self.viewer)
        content = client.get(reverse("admin:catalog_schoolyear_changelist")).content.decode()
        assert 'value="copy_holidays"' not in content
        content = client.get(reverse("admin:notifications_notification_changelist")).content.decode()
        assert 'value="retry_failed"' not in content


class TestOutsideTheAdmin:
    def test_is_never_assigned_a_class_or_alerted(self):
        viewer = ReadOnlyAdminFactory()
        cls = ActivityClassFactory()
        assert viewer not in User.objects.responsible_admins(cls)
        from apps.catalog.admin import AssignAdministratorsForm

        assert viewer not in AssignAdministratorsForm().fields["administrators"].queryset

    def test_lands_on_the_desk_after_login_with_the_admin_links(self, client):
        viewer = ReadOnlyAdminFactory()
        services.register(ChildFactory(), ActivityClassFactory())
        client.force_login(viewer)

        assert client.get(reverse("post_login")).url == reverse(
            "admin:enrollments_enrollment_requests"
        )
        nav = client.get(reverse("catalogue")).content.decode()
        assert reverse("admin:index") in nav
        assert reverse("parent_home") in nav
        assert 'class="badge badge-warn nav-badge">1</span>' in nav

    def test_has_a_family_page_like_any_admin(self, client):
        client.force_login(ReadOnlyAdminFactory())
        response = client.get(reverse("parent_home"))
        assert response.status_code == 200
        assert reverse("child_add") in response.content.decode()

    def test_may_not_compose_announcement_pictures(self, client):
        client.force_login(ReadOnlyAdminFactory())
        response = client.post(reverse("announcement_image_upload"))
        assert response.status_code == 403
