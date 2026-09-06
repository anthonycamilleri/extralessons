"""School admins are parents too: one account, both sides of the site.

The admin role includes the parent one (User.FAMILY_ROLES / User.is_parent):
the family pages, the register form and co-parent invitations accept both, an
admin's own children are marked on the desk, and providers stay outside.
"""
import pytest
from django.urls import reverse

from apps.accounts.models import Child, GuardianInvite, User
from apps.enrollments import services
from apps.enrollments.models import Enrollment

from .factories import (
    ActivityClassFactory,
    AdminFactory,
    ChildFactory,
    ProviderUserFactory,
    SuperAdminFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


class TestWhoIsAParent:
    def test_parents_and_admins_are_providers_are_not(self):
        assert UserFactory().is_parent
        assert AdminFactory().is_parent
        assert SuperAdminFactory().is_parent
        assert not ProviderUserFactory().is_parent

    def test_promoting_a_parent_keeps_their_family(self, client):
        parent = UserFactory()
        child = ChildFactory(parent=parent)
        parent.role = User.Role.ADMIN
        parent.save()

        client.force_login(parent)
        response = client.get(reverse("parent_home"))

        assert response.status_code == 200
        assert child.full_name in response.content.decode()


class TestAdminOnTheFamilySide:
    def test_family_page_opens_with_the_add_child_offer(self, client):
        client.force_login(AdminFactory())
        response = client.get(reverse("parent_home"))
        assert response.status_code == 200
        assert reverse("child_add") in response.content.decode()

    def test_admin_can_add_a_child(self, client):
        admin = AdminFactory()
        client.force_login(admin)

        response = client.post(
            reverse("child_add"),
            {
                "first_name": "Ada",
                "last_name": "Admin",
                "school_class": "P3E",
                "date_of_birth": "2018-05-04",
            },
        )

        assert response.status_code == 302
        child = Child.objects.get(first_name="Ada")
        assert child.guardian_links.get().user == admin

    def test_class_page_offers_the_register_form_with_their_children(self, client):
        admin = AdminFactory()
        child = ChildFactory(parent=admin)
        cls = ActivityClassFactory()
        client.force_login(admin)

        content = client.get(cls.get_absolute_url()).content.decode()

        assert "Only parent accounts can register children." not in content
        assert child.display_name in content
        assert reverse("enroll", args=[cls.pk]) in content

    def test_admin_can_register_their_own_child(self, client):
        admin = AdminFactory()
        child = ChildFactory(parent=admin)
        cls = ActivityClassFactory()
        client.force_login(admin)

        response = client.post(
            reverse("enroll", args=[cls.pk]), {"child": child.pk, "terms_accepted": "1"}
        )

        assert response.status_code == 302
        assert Enrollment.objects.filter(
            child=child, activity_class=cls, status=Enrollment.Status.REQUESTED
        ).exists()

    def test_admin_can_accept_a_co_parent_invitation(self, client):
        parent = UserFactory()
        child = ChildFactory(parent=parent)
        admin = AdminFactory(email="admin-parent@school.test")
        invite = GuardianInvite.objects.create(
            child=child, email=admin.email, invited_by=parent
        )
        client.force_login(admin)

        client.post(reverse("accept_guardian_invite", args=[invite.token]))

        invite.refresh_from_db()
        assert invite.accepted_at is not None
        assert child.guardians.filter(pk=admin.pk).exists()

    def test_admin_still_lands_on_the_desk_after_login(self, client):
        client.force_login(AdminFactory())
        response = client.get(reverse("post_login"))
        assert response.status_code == 302
        assert response.url == reverse("admin:enrollments_enrollment_requests")

    def test_nav_shows_my_family_beside_the_desk(self, client):
        client.force_login(AdminFactory())
        content = client.get(reverse("catalogue")).content.decode()
        assert reverse("parent_home") in content
        assert reverse("admin:enrollments_enrollment_requests") in content

    def test_admin_cannot_touch_another_familys_child(self, client):
        other = ChildFactory()
        client.force_login(AdminFactory())
        assert client.get(reverse("child_edit", args=[other.pk])).status_code == 404


class TestProvidersStayOutside:
    def test_provider_gets_403_on_the_family_pages(self, client):
        client.force_login(ProviderUserFactory())
        assert client.get(reverse("parent_home")).status_code == 403
        assert client.get(reverse("child_add")).status_code == 403

    def test_provider_cannot_accept_a_co_parent_invitation(self, client):
        provider = ProviderUserFactory(email="coach@school.test")
        child = ChildFactory()
        invite = GuardianInvite.objects.create(
            child=child, email=provider.email, invited_by=child.guardians.get()
        )
        client.force_login(provider)

        client.post(reverse("accept_guardian_invite", args=[invite.token]))

        invite.refresh_from_db()
        assert invite.accepted_at is None
        assert not child.guardians.filter(pk=provider.pk).exists()

    def test_class_page_still_turns_a_provider_away(self, client):
        client.force_login(ProviderUserFactory())
        content = client.get(ActivityClassFactory().get_absolute_url()).content.decode()
        assert "Only parent accounts can register children." in content


class TestOwnChildOnTheDesk:
    def test_requests_page_marks_the_admins_own_child(self, client):
        admin = SuperAdminFactory()
        cls = ActivityClassFactory()
        mine = services.register(ChildFactory(parent=admin, first_name="Mina"), cls)
        theirs = services.register(ChildFactory(first_name="Theo"), cls)
        client.force_login(admin)

        content = client.get(reverse("admin:enrollments_enrollment_requests")).content.decode()

        assert content.count("Your child") == 1
        assert content.index("Mina") < content.index("Your child") < content.index("Theo")
        assert mine.pk != theirs.pk

    def test_cancellation_request_is_marked_too(self, client):
        admin = SuperAdminFactory()
        cls = ActivityClassFactory()
        enrollment = services.approve_request(
            services.register(ChildFactory(parent=admin), cls), admin
        )
        Enrollment.objects.filter(pk=enrollment.pk).update(
            cancel_requested_at=enrollment.enrolled_at, cancel_requested_by=admin
        )
        client.force_login(admin)

        content = client.get(reverse("admin:enrollments_enrollment_requests")).content.decode()

        assert "Your child" in content

    def test_roster_marks_the_admins_own_child_in_every_section(self, client):
        admin = SuperAdminFactory()
        cls = ActivityClassFactory(capacity=1)
        services.approve_request(services.register(ChildFactory(parent=admin), cls), admin)
        services.approve_request(services.register(ChildFactory(parent=admin), cls), admin)
        services.register(ChildFactory(parent=admin), cls)
        services.register(ChildFactory(), cls)
        client.force_login(admin)

        content = client.get(
            reverse("admin:catalog_activityclass_roster", args=[cls.pk])
        ).content.decode()

        # enrolled, waitlisted and pending: three of the four rows are theirs
        assert content.count("Your child") == 3

    def test_no_marks_for_an_admin_without_children(self, client):
        cls = ActivityClassFactory()
        services.register(ChildFactory(), cls)
        client.force_login(SuperAdminFactory())

        content = client.get(reverse("admin:enrollments_enrollment_requests")).content.decode()

        assert "Your child" not in content

    def test_admin_may_still_decide_on_their_own_child(self, client):
        admin = SuperAdminFactory()
        enrollment = services.register(ChildFactory(parent=admin), ActivityClassFactory())
        client.force_login(admin)

        response = client.post(
            reverse("admin:enrollments_enrollment_approve", args=[enrollment.pk])
        )

        assert response.status_code == 302
        enrollment.refresh_from_db()
        assert enrollment.status == Enrollment.Status.ENROLLED
