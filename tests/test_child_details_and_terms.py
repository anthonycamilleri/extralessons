"""Child class + going-home flag, the Terms & Conditions page, and the
catalogue's "first/next class" date."""
import datetime

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.accounts.models import SCHOOL_CLASS_CODES, Child, SiteConfig
from apps.catalog.models import ClassSession, generate_sessions
from apps.enrollments.models import Enrollment

from .factories import ActivityClassFactory, ChildFactory, ProviderUserFactory, UserFactory

pytestmark = pytest.mark.django_db


def _child_payload(**overrides):
    payload = {
        "first_name": "Ada",
        "last_name": "Test",
        "school_class": "P2E",
        "date_of_birth": "2018-04-01",
        "notes": "",
    }
    payload.update(overrides)
    return payload


class TestChildDetails:
    def test_class_codes_cover_both_cycles_and_sections(self):
        assert len(SCHOOL_CLASS_CODES) == (5 + 7) * 2
        assert {"P1E", "P1S", "P5S", "S1E", "S7E", "S7S"} <= set(SCHOOL_CLASS_CODES)
        assert "P6E" not in SCHOOL_CLASS_CODES
        assert "S8S" not in SCHOOL_CLASS_CODES

    def test_class_is_required_on_the_form(self, client):
        client.force_login(UserFactory())
        response = client.post(reverse("child_add"), _child_payload(school_class=""))
        assert response.status_code == 200
        assert "This field is required" in response.content.decode()
        assert not Child.objects.exists()

    def test_class_must_be_a_known_code(self, client):
        client.force_login(UserFactory())
        response = client.post(reverse("child_add"), _child_payload(school_class="P9E"))
        assert response.status_code == 200
        assert not Child.objects.exists()

    def test_leave_alone_flag_is_saved_and_shown_to_the_family(self, client):
        parent = UserFactory()
        client.force_login(parent)
        client.post(reverse("child_add"), _child_payload(may_leave_alone="on"))
        child = Child.objects.get()
        assert child.school_class == "P2E"
        assert child.may_leave_alone is True
        assert child.display_name == "Ada Test (P2E)"

        home = client.get(reverse("parent_home")).content.decode()
        assert "P2E" in home
        assert "may go home alone" in home

    def test_provider_roster_shows_class_and_going_home_arrangement(self, client):
        provider_user = ProviderUserFactory()
        cls = ActivityClassFactory()
        cls.provider.members.add(provider_user)
        collected = ChildFactory(first_name="Collect", school_class="P1S")
        alone = ChildFactory(first_name="Solo", school_class="S3E", may_leave_alone=True)
        for child in (collected, alone):
            Enrollment.objects.create(
                child=child, activity_class=cls, status=Enrollment.Status.ENROLLED
            )

        client.force_login(provider_user)
        page = client.get(reverse("provider_class", args=[cls.pk])).content.decode()
        assert "P1S" in page and "S3E" in page
        assert page.count("May leave alone") == 1
        assert page.count("Must be collected") == 1

    def test_register_picker_labels_children_with_their_class(self, client):
        parent = UserFactory()
        ChildFactory(parent=parent, first_name="Ada", last_name="Test", school_class="P4S")
        client.force_login(parent)
        page = client.get(ActivityClassFactory().get_absolute_url()).content.decode()
        assert "Ada Test (P4S)" in page


class TestTerms:
    def test_default_terms_are_seeded_and_rendered_from_markdown(self, client):
        response = client.get(reverse("terms"))
        content = response.content.decode()
        assert response.status_code == 200
        assert "<h1>Extra-Curricular Activities" in content
        assert "<h2>Payments</h2>" in content
        assert "The PTA does not collect or handle any money." in content

    def test_navigation_links_to_terms(self, client):
        content = client.get(reverse("catalogue")).content.decode()
        assert content.count(reverse("terms")) >= 2  # nav and footer

    def test_admin_can_switch_terms_off(self, client):
        config = SiteConfig.get()
        config.terms_markdown = ""
        config.save()
        assert client.get(reverse("terms")).status_code == 404
        assert reverse("terms") not in client.get(reverse("catalogue")).content.decode()

    def test_registration_requires_the_terms_tick(self, client):
        parent = UserFactory()
        child = ChildFactory(parent=parent)
        cls = ActivityClassFactory()
        client.force_login(parent)

        page = client.get(cls.get_absolute_url()).content.decode()
        assert 'name="terms_accepted"' in page

        response = client.post(reverse("enroll", args=[cls.pk]), {"child": child.pk})
        assert response.status_code == 302
        assert response.url.endswith("#register")
        assert not Enrollment.objects.exists()

        client.post(
            reverse("enroll", args=[cls.pk]), {"child": child.pk, "terms_accepted": "1"}
        )
        enrollment = Enrollment.objects.get()
        assert enrollment.terms_accepted_at is not None

    def test_no_tick_needed_when_terms_are_switched_off(self, client):
        config = SiteConfig.get()
        config.terms_markdown = ""
        config.save()
        parent = UserFactory()
        child = ChildFactory(parent=parent)
        cls = ActivityClassFactory()
        client.force_login(parent)

        assert 'name="terms_accepted"' not in client.get(cls.get_absolute_url()).content.decode()
        client.post(reverse("enroll", args=[cls.pk]), {"child": child.pk})
        assert Enrollment.objects.get().terms_accepted_at is None


class TestNextClassDate:
    def test_catalogue_shows_first_class_before_the_class_starts(self, client):
        today = timezone.localdate()
        cls = ActivityClassFactory(term__start_date=today + datetime.timedelta(days=3))
        generate_sessions(cls)
        first = cls.sessions.order_by("date").first().date

        for url in (reverse("catalogue"), cls.get_absolute_url()):
            content = client.get(url).content.decode()
            assert "First class" in content
            assert first.strftime("%-d") in content
            # The term's paperwork dates are no longer parent-facing.
            assert cls.term.end_date.strftime("%-d %b %Y") not in content

    def test_catalogue_shows_next_class_once_running_and_skips_cancelled(self, client):
        cls = ActivityClassFactory()  # term started a week ago
        generate_sessions(cls)
        today = timezone.localdate()
        assert cls.sessions.filter(date__lt=today).exists()
        upcoming = list(cls.sessions.filter(date__gte=today).order_by("date"))
        upcoming[0].cancelled = True
        upcoming[0].save()

        content = client.get(reverse("catalogue")).content.decode()
        assert "Next class" in content
        assert "First class" not in content
        assert upcoming[1].date.strftime("%a %-d %b") in content

    def test_no_sessions_means_no_date_chip(self, client):
        cls = ActivityClassFactory()
        assert not ClassSession.objects.filter(activity_class=cls).exists()
        content = client.get(reverse("catalogue")).content.decode()
        assert "Next class" not in content and "First class" not in content
        assert cls.next_session_label is None  # the unannotated fallback path
