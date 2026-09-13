"""Instructors: who a provider's people are, what each of them sees, and the
profile and police-conduct certificate behind every name on a class page."""
import io

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from PIL import Image

from apps.accounts.models import User
from apps.catalog.models import ActivityClass, Instructor, generate_sessions
from apps.enrollments import services
from apps.enrollments.models import Attendance
from apps.notifications.models import Event, Notification

from .factories import (
    ActivityClassFactory,
    AdminFactory,
    ChildFactory,
    ProviderFactory,
    ProviderUserFactory,
    SuperAdminFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


def provider_setup():
    """A provider with a manager account and two published classes."""
    provider = ProviderFactory()
    manager = ProviderUserFactory()
    provider.members.add(manager)
    taught = ActivityClassFactory(provider=provider, title="Taught Class", capacity=5)
    other = ActivityClassFactory(provider=provider, title="Other Class", capacity=5)
    return provider, manager, taught, other


def instructor_for(provider, *classes, user=None):
    user = user or ProviderUserFactory()
    instructor = Instructor.objects.create(provider=provider, user=user)
    instructor.classes.set(classes)
    return instructor


def pdf_upload(name="cert.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 fake certificate", content_type="application/pdf")


def png_upload(name="photo.png"):
    buffer = io.BytesIO()
    Image.new("RGB", (40, 40), (10, 20, 30)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class TestScoping:
    def test_run_by_is_members_plus_assigned_instructors(self):
        provider, manager, taught, other = provider_setup()
        instructor = instructor_for(provider, taught)
        both = ProviderUserFactory()
        provider.members.add(both)
        instructor_for(provider, taught, user=both)
        stranger = ActivityClassFactory()

        assert set(ActivityClass.objects.run_by(manager)) == {taught, other}
        assert set(ActivityClass.objects.run_by(instructor.user)) == {taught}
        assert list(ActivityClass.objects.run_by(both)) == sorted(
            [taught, other], key=lambda c: (c.weekday, c.start_time, c.title)
        )
        assert stranger not in ActivityClass.objects.run_by(manager)

    def test_instructor_sees_only_assigned_classes(self, client):
        provider, manager, taught, other = provider_setup()
        instructor = instructor_for(provider, taught)
        client.force_login(instructor.user)

        page = client.get(reverse("provider_home")).content.decode()
        assert "Taught Class" in page
        assert "Other Class" not in page
        assert client.get(reverse("provider_class", args=[taught.pk])).status_code == 200
        assert client.get(reverse("provider_class", args=[other.pk])).status_code == 404

    def test_instructor_sees_children_and_takes_attendance(self, client):
        provider, manager, taught, other = provider_setup()
        instructor = instructor_for(provider, taught)
        admin = AdminFactory()
        child = ChildFactory(first_name="Nutty", notes="Peanut allergy")
        services.approve_request(services.register(child, taught), admin)
        generate_sessions(taught)
        session = taught.sessions.first()
        client.force_login(instructor.user)

        roster = client.get(reverse("provider_class", args=[taught.pk])).content.decode()
        assert "Nutty" in roster and "Peanut allergy" in roster

        url = reverse("provider_attendance", args=[taught.pk, session.pk])
        assert client.post(url, {"present": [child.pk]}).status_code == 302
        mark = Attendance.objects.get(session=session, child=child)
        assert mark.present and mark.marked_by == instructor.user

        generate_sessions(other)
        forbidden = reverse("provider_attendance", args=[other.pk, other.sessions.first().pk])
        assert client.get(forbidden).status_code == 404

    def test_instructor_messages_only_assigned_classes(self, client):
        provider, manager, taught, other = provider_setup()
        instructor = instructor_for(provider, taught)
        admin = AdminFactory()
        parent = UserFactory()
        services.approve_request(services.register(ChildFactory(parent=parent), taught), admin)
        client.force_login(instructor.user)

        refused = client.post(
            reverse("provider_broadcast"),
            {"classes": [other.pk], "subject": "Hijack", "body_html": "<p>x</p>"},
        )
        assert refused.status_code == 200
        assert not Notification.objects.filter(event=Event.BROADCAST).exists()

        sent = client.post(
            reverse("provider_broadcast"),
            {"classes": [taught.pk], "subject": "Kit", "body_html": "<p>Boots.</p>"},
        )
        assert sent.status_code == 302
        assert Notification.objects.filter(event=Event.BROADCAST, recipient=parent).exists()

    def test_plain_instructor_cannot_manage_instructors(self, client):
        provider, manager, taught, other = provider_setup()
        instructor = instructor_for(provider, taught)
        client.force_login(instructor.user)

        page = client.get(reverse("provider_instructors"))
        assert page.status_code == 200
        assert b"Only a provider's own account manages its instructors" in page.content
        assert client.get(reverse("provider_instructor_add", args=[provider.pk])).status_code == 404
        assert client.get(reverse("provider_instructor", args=[instructor.pk])).status_code == 404
        # ...but their own profile is theirs to edit.
        assert (
            client.get(reverse("provider_instructor_profile", args=[instructor.pk])).status_code
            == 200
        )

    def test_navigation_shows_the_right_links(self, client):
        provider, manager, taught, other = provider_setup()
        instructor = instructor_for(provider, taught)

        instructors_link = f'href="{reverse("provider_instructors")}">Instructors</a>'
        profile_link = f'href="{reverse("provider_my_profile")}">My profile</a>'

        client.force_login(manager)
        nav = client.get(reverse("provider_home")).content.decode()
        assert instructors_link in nav
        assert profile_link not in nav

        client.force_login(instructor.user)
        nav = client.get(reverse("provider_home")).content.decode()
        assert profile_link in nav
        assert instructors_link not in nav


class TestManagingInstructors:
    def test_manager_creates_an_account_and_the_invite_link_works(self, client):
        provider, manager, taught, other = provider_setup()
        client.force_login(manager)

        response = client.post(
            reverse("provider_instructor_add", args=[provider.pk]),
            {
                "email": "New.Coach@Example.com",
                "first_name": "Nadia",
                "last_name": "Coach",
                "classes": [taught.pk],
            },
        )
        assert response.status_code == 302
        user = User.objects.get(email="new.coach@example.com")
        assert user.role == User.Role.PROVIDER
        assert not user.has_usable_password()
        instructor = Instructor.objects.get(user=user, provider=provider)
        assert list(instructor.classes.all()) == [taught]
        assert set(ActivityClass.objects.run_by(user)) == {taught}

        invite = Notification.objects.get(event=Event.INSTRUCTOR_INVITE, recipient=user)
        assert invite.status == Notification.Status.PENDING
        assert provider.name in invite.rendered_subject
        link = next(
            line for line in invite.rendered_body.splitlines() if "/accounts/password-reset/" in line
        ).strip()
        path = link.split("http://localhost:8000", 1)[1]
        client.logout()
        landing = client.get(path, follow=True)
        assert landing.status_code == 200
        assert b"Choose a new password" in landing.content
        assert b"invalid or has expired" not in landing.content

    def test_existing_provider_account_is_linked_not_recreated(self, client):
        provider, manager, taught, other = provider_setup()
        elsewhere = ProviderUserFactory(email="shared@example.com", first_name="Sam")
        client.force_login(manager)

        response = client.post(
            reverse("provider_instructor_add", args=[provider.pk]),
            {"email": "shared@example.com", "first_name": "Sam", "last_name": "X", "classes": []},
        )

        assert response.status_code == 302
        assert User.objects.filter(email="shared@example.com").count() == 1
        assert Instructor.objects.filter(provider=provider, user=elsewhere).exists()
        assert not Notification.objects.filter(event=Event.INSTRUCTOR_INVITE).exists()

    def test_parent_or_admin_address_is_refused(self, client):
        provider, manager, taught, other = provider_setup()
        parent = UserFactory(email="mum@example.com")
        client.force_login(manager)

        response = client.post(
            reverse("provider_instructor_add", args=[provider.pk]),
            {"email": parent.email, "first_name": "M", "last_name": "P", "classes": []},
        )

        assert response.status_code == 200
        assert b"belongs to a parent or school account" in response.content
        assert not Instructor.objects.filter(user=parent).exists()

    def test_manager_cannot_add_to_a_provider_they_do_not_run(self, client):
        provider, manager, taught, other = provider_setup()
        someone_elses = ProviderFactory()
        client.force_login(manager)
        assert (
            client.get(reverse("provider_instructor_add", args=[someone_elses.pk])).status_code
            == 404
        )

    def test_manager_adds_themself_as_an_instructor(self, client):
        provider, manager, taught, other = provider_setup()
        client.force_login(manager)

        response = client.post(reverse("provider_instructor_add_self", args=[provider.pk]))

        assert response.status_code == 302
        instructor = Instructor.objects.get(provider=provider, user=manager)
        # Both hats: still sees every class, now with a profile of their own.
        assert set(ActivityClass.objects.run_by(manager)) == {taught, other}
        page = client.get(reverse("provider_instructors")).content.decode()
        assert ">You<" in page and "I teach too" not in page
        assert client.get(reverse("provider_my_profile")).status_code == 302
        assert (
            client.get(reverse("provider_instructor_profile", args=[instructor.pk])).status_code
            == 200
        )

    def test_manager_reassigns_classes(self, client):
        provider, manager, taught, other = provider_setup()
        instructor = instructor_for(provider, taught)
        client.force_login(manager)

        response = client.post(
            reverse("provider_instructor", args=[instructor.pk]), {"classes": [other.pk]}
        )

        assert response.status_code == 302
        assert list(instructor.classes.all()) == [other]
        assert set(ActivityClass.objects.run_by(instructor.user)) == {other}

    def test_class_assignment_form_refuses_another_providers_class(self, client):
        provider, manager, taught, other = provider_setup()
        instructor = instructor_for(provider, taught)
        stranger = ActivityClassFactory()
        client.force_login(manager)

        response = client.post(
            reverse("provider_instructor", args=[instructor.pk]), {"classes": [stranger.pk]}
        )

        assert response.status_code == 200
        assert list(instructor.classes.all()) == [taught]

    def test_resend_invite_only_before_first_login(self, client):
        provider, manager, taught, other = provider_setup()
        instructor = instructor_for(provider, taught)
        client.force_login(manager)

        client.post(reverse("provider_instructor_resend", args=[instructor.pk]))
        assert Notification.objects.filter(event=Event.INSTRUCTOR_INVITE).count() == 1

        from django.utils import timezone

        instructor.user.last_login = timezone.now()
        instructor.user.save(update_fields=["last_login"])
        client.post(reverse("provider_instructor_resend", args=[instructor.pk]))
        assert Notification.objects.filter(event=Event.INSTRUCTOR_INVITE).count() == 1

    def test_remove_deletes_profile_and_disables_an_orphaned_account(self, client):
        provider, manager, taught, other = provider_setup()
        instructor = instructor_for(provider, taught)
        instructor.replace_certificate(pdf_upload())
        storage, name = instructor.conduct_certificate.storage, instructor.conduct_certificate.name
        user = instructor.user
        client.force_login(manager)

        response = client.post(reverse("provider_instructor_remove", args=[instructor.pk]))

        assert response.status_code == 302
        assert not Instructor.objects.filter(pk=instructor.pk).exists()
        assert not storage.exists(name)
        user.refresh_from_db()
        assert not user.is_active
        assert not ActivityClass.objects.run_by(user).exists()

    def test_remove_keeps_an_account_still_in_use(self, client):
        provider, manager, taught, other = provider_setup()
        second = ProviderFactory()
        shared = ProviderUserFactory()
        mine = instructor_for(provider, taught, user=shared)
        instructor_for(second, user=shared)
        client.force_login(manager)

        client.post(reverse("provider_instructor_remove", args=[mine.pk]))

        shared.refresh_from_db()
        assert shared.is_active
        assert Instructor.objects.filter(user=shared).count() == 1


class TestProfileAndCertificate:
    def test_instructor_edits_profile_and_uploads_certificate(self, client):
        provider, manager, taught, other = provider_setup()
        instructor = instructor_for(provider, taught)
        client.force_login(instructor.user)

        response = client.post(
            reverse("provider_instructor_profile", args=[instructor.pk]),
            {
                "first_name": "Nadia",
                "last_name": "Coach",
                "bio": "Ten years of junior football.",
                "photo": png_upload(),
                "conduct_certificate": pdf_upload(),
                "conduct_certificate_issued_on": "2026-01-15",
            },
        )

        assert response.status_code == 302
        instructor.refresh_from_db()
        assert instructor.user.get_full_name() == "Nadia Coach"
        assert instructor.bio == "Ten years of junior football."
        assert instructor.photo.name.startswith("instructors/") and instructor.photo.name.endswith(".jpg")
        assert instructor.conduct_certificate.name.startswith("private/conduct-certificates/")
        assert str(instructor.conduct_certificate_issued_on) == "2026-01-15"
        assert instructor.conduct_certificate_uploaded_at is not None
        assert instructor.certificate_status == "uploaded"
        assert not instructor.certificate_checked

    def test_certificate_must_be_a_real_pdf_or_picture(self, client):
        provider, manager, taught, other = provider_setup()
        instructor = instructor_for(provider, taught)
        client.force_login(instructor.user)
        url = reverse("provider_instructor_profile", args=[instructor.pk])
        base = {"first_name": "A", "last_name": "B", "bio": ""}

        fake_pdf = SimpleUploadedFile("cert.pdf", b"<html>not a pdf</html>", content_type="application/pdf")
        response = client.post(url, {**base, "conduct_certificate": fake_pdf})
        assert response.status_code == 200 and b"not a PDF we can read" in response.content

        exe = SimpleUploadedFile("cert.exe", b"MZ", content_type="application/octet-stream")
        response = client.post(url, {**base, "conduct_certificate": exe})
        assert response.status_code == 200 and b"PDF, JPG or PNG" in response.content
        instructor.refresh_from_db()
        assert not instructor.has_certificate

    def test_new_upload_clears_the_schools_check(self):
        provider, manager, taught, other = provider_setup()
        instructor = instructor_for(provider, taught)
        office = AdminFactory()
        instructor.replace_certificate(pdf_upload("first.pdf"))
        first_name = instructor.conduct_certificate.name
        assert instructor.mark_certificate_checked(office)
        assert instructor.certificate_checked

        instructor.replace_certificate(pdf_upload("second.pdf"))

        assert not instructor.certificate_checked
        assert instructor.conduct_certificate_checked_by is None
        assert not instructor.conduct_certificate.storage.exists(first_name)
        assert instructor.conduct_certificate.storage.exists(instructor.conduct_certificate.name)

    def test_certificate_download_is_for_the_few(self, client):
        provider, manager, taught, other = provider_setup()
        instructor = instructor_for(provider, taught)
        instructor.replace_certificate(pdf_upload())
        url = reverse("provider_instructor_certificate", args=[instructor.pk])

        def status_for(user):
            client.force_login(user)
            return client.get(url).status_code

        assert status_for(instructor.user) == 200
        assert status_for(manager) == 200
        assert status_for(AdminFactory()) == 200
        assert status_for(SuperAdminFactory()) == 200
        assert status_for(UserFactory()) == 404  # a parent
        assert status_for(ProviderUserFactory()) == 404  # another provider's account
        other_instructor = instructor_for(provider, other)
        assert status_for(other_instructor.user) == 404  # a colleague
        client.logout()
        assert client.get(url).status_code == 302  # login first

        client.force_login(manager)
        response = client.get(url)
        assert response["Content-Type"] == "application/pdf"
        assert response["Content-Disposition"].startswith("attachment;")
        assert "no-store" in response["Cache-Control"]
        assert b"".join(response.streaming_content).startswith(b"%PDF")

    def test_private_names_are_never_served_at_media_url(self, client):
        from apps.media.models import StoredFile

        StoredFile.objects.create(
            name="private/conduct-certificates/x.pdf",
            content=b"%PDF",
            content_type="application/pdf",
            size=4,
        )
        StoredFile.objects.create(
            name="classes/pic.jpg", content=b"\xff\xd8", content_type="image/jpeg", size=2
        )
        assert client.get("/media/private/conduct-certificates/x.pdf").status_code == 404
        assert client.get("/media/classes/pic.jpg").status_code == 200

    def test_public_class_page_shows_profiles_and_only_a_checked_certificate(self, client):
        provider, manager, taught, other = provider_setup()
        instructor = instructor_for(provider, taught)
        instructor.user.first_name, instructor.user.last_name = "Nadia", "Coach"
        instructor.user.save()
        instructor.bio = "Ten years of junior football."
        instructor.save()
        instructor.replace_certificate(pdf_upload())
        certificate_url = reverse("provider_instructor_certificate", args=[instructor.pk])

        page = client.get(taught.get_absolute_url()).content.decode()
        assert "Who runs it" in page
        assert "Nadia Coach" in page and "Ten years of junior football." in page
        assert "certificate checked by the school" not in page
        assert certificate_url not in page

        instructor.mark_certificate_checked(AdminFactory())
        page = client.get(taught.get_absolute_url()).content.decode()
        assert "Police conduct certificate checked by the school" in page
        assert certificate_url not in page

        # The other class has no instructor: the section is not shown at all.
        assert "Who runs it" not in client.get(other.get_absolute_url()).content.decode()

    def test_provider_class_page_lists_instructors(self, client):
        provider, manager, taught, other = provider_setup()
        instructor = instructor_for(provider, taught)
        instructor.user.first_name = "Nadia"
        instructor.user.save()
        client.force_login(manager)

        page = client.get(reverse("provider_class", args=[taught.pk])).content.decode()
        assert "Nadia" in page
        assert reverse("provider_instructors") in page


class TestBackend:
    def test_super_admin_sees_instructors_and_marks_certificates_checked(self, client):
        provider, manager, taught, other = provider_setup()
        instructor = instructor_for(provider, taught)
        instructor.replace_certificate(pdf_upload())
        boss = SuperAdminFactory()
        client.force_login(boss)

        listing = client.get(reverse("admin:catalog_instructor_changelist"))
        assert listing.status_code == 200
        assert b"uploaded, to check" in listing.content
        change = client.get(reverse("admin:catalog_instructor_change", args=[instructor.pk]))
        assert change.status_code == 200
        assert reverse("provider_instructor_certificate", args=[instructor.pk]).encode() in change.content

        response = client.post(
            reverse("admin:catalog_instructor_changelist"),
            {"action": "mark_certificate_checked", "_selected_action": [instructor.pk]},
        )
        assert response.status_code == 302
        instructor.refresh_from_db()
        assert instructor.certificate_checked
        assert instructor.conduct_certificate_checked_by == boss

    def test_regular_admin_sees_only_instructors_of_their_classes(self, client):
        provider, manager, taught, other = provider_setup()
        mine = instructor_for(provider, taught)
        theirs = instructor_for(provider, other)
        mine.user.first_name, theirs.user.first_name = "Visible", "Hidden"
        mine.user.save()
        theirs.user.save()
        admin = AdminFactory()
        taught.administrators.add(admin)
        client.force_login(admin)

        listing = client.get(reverse("admin:catalog_instructor_changelist")).content.decode()
        assert "Visible" in listing and "Hidden" not in listing
        assert client.get(reverse("admin:catalog_instructor_change", args=[mine.pk])).status_code == 200
        assert client.get(reverse("admin:catalog_instructor_change", args=[theirs.pk])).status_code == 302

    def test_roster_and_class_form_show_instructors(self, client):
        provider, manager, taught, other = provider_setup()
        instructor = instructor_for(provider, taught)
        instructor.user.first_name = "Nadia"
        instructor.user.save()
        client.force_login(SuperAdminFactory())

        roster = client.get(
            reverse("admin:catalog_activityclass_roster", kwargs={"object_id": taught.pk})
        ).content.decode()
        assert "Nadia" in roster and "no certificate" in roster

        change = client.get(reverse("admin:catalog_activityclass_change", args=[taught.pk]))
        assert change.status_code == 200
        assert b'name="instructors"' in change.content

    def test_class_form_refuses_another_providers_instructor(self):
        from apps.catalog.admin import ActivityClassForm

        provider, manager, taught, other = provider_setup()
        stranger = instructor_for(ProviderFactory())
        data = {
            field: getattr(taught, field)
            for field in ["provider", "term", "title", "slug", "description", "age_min", "age_max", "capacity", "weekday", "location", "status"]
        }
        data.update(
            {
                "provider": taught.provider_id,
                "term": taught.term_id,
                "start_time": "15:00",
                "end_time": "16:00",
                "instructors": [stranger.pk],
            }
        )
        form = ActivityClassForm(data, instance=taught)
        assert not form.is_valid()
        assert "instructors" in form.errors

    def test_provider_admin_lists_instructors_inline(self, client):
        provider, manager, taught, other = provider_setup()
        instructor = instructor_for(provider, taught)
        instructor.user.first_name = "Nadia"
        instructor.user.save()
        client.force_login(SuperAdminFactory())

        page = client.get(reverse("admin:catalog_provider_change", args=[provider.pk]))
        assert page.status_code == 200
        assert b"Taught Class" in page.content
        assert b"not uploaded" in page.content


class TestSeveralProviders:
    def test_my_profile_offers_a_choice_when_teaching_for_two_providers(self, client):
        provider, manager, taught, other = provider_setup()
        second = ProviderFactory(name="Second Academy")
        shared = ProviderUserFactory()
        first_profile = instructor_for(provider, taught, user=shared)
        second_profile = instructor_for(second, user=shared)
        client.force_login(shared)

        page = client.get(reverse("provider_my_profile"))

        assert page.status_code == 200
        content = page.content.decode()
        assert "Second Academy" in content
        assert reverse("provider_instructor_profile", args=[first_profile.pk]) in content
        assert reverse("provider_instructor_profile", args=[second_profile.pk]) in content

    def test_my_profile_without_a_profile_explains_and_goes_home(self, client):
        provider, manager, taught, other = provider_setup()
        client.force_login(manager)

        response = client.get(reverse("provider_my_profile"), follow=True)

        assert response.redirect_chain[-1][0] == reverse("provider_home")
        assert b"do not have an instructor profile yet" in response.content
