import pytest
from django.urls import reverse

from apps.accounts.models import SiteConfig
from apps.enrollments import services
from apps.enrollments.models import Enrollment

from .factories import (
    ActivityClassFactory,
    AdminFactory,
    ChildFactory,
    UserFactory,
)

pytestmark = pytest.mark.django_db


class TestPublicCatalogue:
    def test_catalogue_lists_published_classes_with_availability(self, client):
        cls = ActivityClassFactory(title="Chess Club", capacity=5)
        ActivityClassFactory(title="Hidden Draft", status="DRAFT")

        response = client.get(reverse("catalogue"))

        content = response.content.decode()
        assert response.status_code == 200
        assert "Chess Club" in content
        assert "5 of 5 places available" in content
        assert "Hidden Draft" not in content

    def test_class_detail_shows_description(self, client):
        cls = ActivityClassFactory(description="Learn openings and tactics.")
        response = client.get(cls.get_absolute_url())
        assert response.status_code == 200
        assert "Learn openings and tactics." in response.content.decode()


class TestCatalogueFilters:
    @pytest.fixture
    def three(self):
        return {
            "chess": ActivityClassFactory(
                title="Monday Chess", weekday=0, location="Library", age_min=6, age_max=10
            ),
            "judo": ActivityClassFactory(
                title="Tuesday Judo", weekday=1, location="Library", age_min=8, age_max=12
            ),
            "football": ActivityClassFactory(
                title="Tuesday Football", weekday=1, location="Sports hall", age_min=5, age_max=8
            ),
        }

    def test_day_filter(self, client, three):
        content = client.get(reverse("catalogue"), {"day": 1}).content.decode()
        assert "Tuesday Judo" in content
        assert "Tuesday Football" in content
        assert "Monday Chess" not in content

    def test_monday_is_a_real_filter(self, client, three):
        # Monday is weekday 0, which is falsy — the filter must still apply.
        content = client.get(reverse("catalogue"), {"day": 0}).content.decode()
        assert "Monday Chess" in content
        assert "Tuesday Judo" not in content

    def test_location_filter(self, client, three):
        content = client.get(reverse("catalogue"), {"location": "Sports hall"}).content.decode()
        assert "Tuesday Football" in content
        assert "Monday Chess" not in content

    def test_age_filter_matches_the_class_age_range(self, client, three):
        content = client.get(reverse("catalogue"), {"age": 11}).content.decode()
        assert "Tuesday Judo" in content
        assert "Monday Chess" not in content
        assert "Tuesday Football" not in content

    def test_filters_combine(self, client, three):
        content = client.get(
            reverse("catalogue"), {"day": 1, "location": "Library"}
        ).content.decode()
        assert "Tuesday Judo" in content
        assert "Tuesday Football" not in content
        assert "Monday Chess" not in content

    @pytest.mark.parametrize(
        "params",
        [
            {"day": "banana"},
            {"day": 9},
            {"day": -1},
            {"age": "old"},
            {"age": 99},
            {"location": "Narnia"},
        ],
    )
    def test_unusable_filters_fall_back_to_everything(self, client, three, params):
        response = client.get(reverse("catalogue"), params)
        content = response.content.decode()
        assert response.status_code == 200
        assert all(cls.title in content for cls in three.values())

    def test_filter_options_never_expose_unpublished_classes(self, client, three):
        ActivityClassFactory(title="Secret Draft", status="DRAFT", location="Head's office")
        content = client.get(reverse("catalogue")).content.decode()
        assert "Head&#x27;s office" not in content
        assert "Head's office" not in content

    def test_empty_result_offers_a_way_back(self, client, three):
        content = client.get(reverse("catalogue"), {"day": 0, "age": 5}).content.decode()
        assert "Nothing matches those filters" in content
        assert "check back soon" not in content

    def test_clear_button_is_always_present_but_disabled_until_filtering(self, client, three):
        idle = client.get(reverse("catalogue")).content.decode()
        assert ">Clear filters<" in idle
        assert 'aria-disabled="true"' in idle

        active = client.get(reverse("catalogue"), {"day": 0}).content.decode()
        assert ">Clear filters<" in active
        assert 'aria-disabled="true"' not in active

    def test_filtered_page_keeps_the_availability_wording(self, client):
        ActivityClassFactory(title="Monday Chess", weekday=0, capacity=5)
        content = client.get(reverse("catalogue"), {"day": 0}).content.decode()
        assert "5 of 5 places available" in content

    def test_filters_are_remembered_on_the_next_visit(self, client, three):
        client.get(reverse("catalogue"), {"day": 1, "location": "Library"})
        assert "catalogue_filters" in client.cookies
        assert client.cookies["catalogue_filters"]["max-age"] == 365 * 24 * 60 * 60

        content = client.get(reverse("catalogue")).content.decode()
        assert "Tuesday Judo" in content
        assert "Tuesday Football" not in content
        assert "Monday Chess" not in content
        assert "filters from your last visit" in content
        # The form reflects the remembered selection too.
        assert 'id="day-1" value="1" checked' in content
        assert '<option value="Library" selected>' in content

    def test_remembered_filters_are_not_claimed_on_an_explicit_url(self, client, three):
        content = client.get(reverse("catalogue"), {"day": 1}).content.decode()
        assert "filters from your last visit" not in content

    def test_an_explicit_url_beats_the_remembered_filters(self, client, three):
        client.get(reverse("catalogue"), {"day": 1})
        content = client.get(reverse("catalogue"), {"day": 0}).content.decode()
        assert "Monday Chess" in content
        assert "Tuesday Judo" not in content
        # ...and becomes the new remembered selection.
        content = client.get(reverse("catalogue")).content.decode()
        assert "Monday Chess" in content
        assert "Tuesday Judo" not in content

    def test_choosing_any_everywhere_forgets_the_filters(self, client, three):
        client.get(reverse("catalogue"), {"day": 1})
        # What the form submits when every control is back on "Any".
        client.get(reverse("catalogue"), {"day": "", "location": "", "age": ""})
        assert not client.cookies.get("catalogue_filters", None) or (
            client.cookies["catalogue_filters"].value == ""
        )
        content = client.get(reverse("catalogue")).content.decode()
        assert all(cls.title in content for cls in three.values())

    def test_clear_forgets_the_filters_and_returns_to_the_catalogue(self, client, three):
        client.get(reverse("catalogue"), {"day": 1})

        response = client.get(reverse("catalogue"), {"clear": ""})
        assert response.status_code == 302
        assert response["Location"] == reverse("catalogue")

        content = client.get(reverse("catalogue")).content.decode()
        assert all(cls.title in content for cls in three.values())
        assert "filters from your last visit" not in content

    def test_a_stale_remembered_filter_is_dropped(self, client, three):
        # A day the catalogue no longer offers, e.g. after a term change.
        client.cookies["catalogue_filters"] = "day=4&location=Narnia"
        content = client.get(reverse("catalogue")).content.decode()
        assert all(cls.title in content for cls in three.values())
        assert "filters from your last visit" not in content
        assert client.cookies["catalogue_filters"].value == ""

    def test_remembered_location_survives_a_space_and_accents(self, client):
        ActivityClassFactory(title="Judo", location="Športna dvorana")
        ActivityClassFactory(title="Chess", location="Library")
        client.get(reverse("catalogue"), {"location": "Športna dvorana"})
        content = client.get(reverse("catalogue")).content.decode()
        assert "Judo" in content
        assert "Chess" not in content

    def test_catalogue_stays_within_its_query_budget(
        self, client, three, django_assert_max_num_queries
    ):
        SiteConfig.get()  # created on first access; not part of what we measure
        # One query builds every filter option, one fetches the classes.
        with django_assert_max_num_queries(3):
            client.get(reverse("catalogue"), {"day": 1})


class TestSignupAndFamily:
    def test_signup_creates_parent_account(self, client):
        response = client.post(
            reverse("signup"),
            {
                "email": "new@parent.test",
                "first_name": "New",
                "last_name": "Parent",
                "phone_e164": "",
                "password1": "s3cure-pass-123",
                "password2": "s3cure-pass-123",
            },
        )
        assert response.status_code == 302
        assert response.url == reverse("parent_home")

    def test_parent_can_add_child(self, client):
        parent = UserFactory()
        client.force_login(parent)
        response = client.post(
            reverse("child_add"),
            {"first_name": "Ada", "last_name": "Test", "school_class": "P2E", "date_of_birth": "2018-04-01", "notes": ""},
        )
        assert response.status_code == 302
        assert parent.children.filter(first_name="Ada").exists()


class TestRegisterFlowWhenNotLoggedIn:
    """A parent who is not logged in can still start registering from any
    class, and every door (log in, sign up, add a child) leads back to that
    class's register form."""

    def _back_here(self, cls):
        return f"next={cls.get_absolute_url()}%23register"

    def test_catalogue_card_always_offers_a_register_button(self, client):
        cls = ActivityClassFactory(title="Chess Club", capacity=5)
        full = ActivityClassFactory(title="Robotics", capacity=1)
        services.register(ChildFactory(), full)
        services.approve_request(Enrollment.objects.get(activity_class=full), AdminFactory())

        content = client.get(reverse("catalogue")).content.decode()

        assert f'href="{cls.get_absolute_url()}#register"' in content
        assert content.count("#register") == 2
        assert "Join waiting list" in content

    def test_no_template_comment_leaks_into_the_pages(self, client):
        # A {# #} comment that spans two lines is rendered as text by Django.
        cls = ActivityClassFactory()
        for url in (reverse("catalogue"), cls.get_absolute_url()):
            content = client.get(url).content.decode()
            assert "{#" not in content and "#}" not in content, url
            assert "{%" not in content, url

    def test_class_page_offers_login_and_signup_that_return_here(self, client):
        cls = ActivityClassFactory()
        content = client.get(cls.get_absolute_url()).content.decode()

        assert f'{reverse("login")}?{self._back_here(cls)}' in content
        assert f'{reverse("signup")}?{self._back_here(cls)}' in content
        assert "Log in to register" in content
        assert "Create an account" in content

    def test_class_page_without_open_signup_points_to_the_office(self, client):
        config = SiteConfig.get()
        config.signup_open = False
        config.save()
        cls = ActivityClassFactory()
        content = client.get(cls.get_absolute_url()).content.decode()
        assert "Log in to register" in content
        assert reverse("signup") not in content
        assert "school office" in content

    def test_login_page_keeps_the_return_address_on_its_signup_link(self, client):
        cls = ActivityClassFactory()
        response = client.get(reverse("login"), {"next": cls.get_absolute_url() + "#register"})
        content = response.content.decode()
        assert f'{reverse("signup")}?{self._back_here(cls)}' in content

    def test_signup_page_keeps_the_return_address_on_its_login_link(self, client):
        cls = ActivityClassFactory()
        response = client.get(reverse("signup"), {"next": cls.get_absolute_url() + "#register"})
        content = response.content.decode()
        assert f'{reverse("login")}?{self._back_here(cls)}' in content

    def test_signup_returns_to_the_class(self, client):
        cls = ActivityClassFactory()
        response = client.post(
            reverse("signup"),
            {
                "email": "new@parent.test",
                "first_name": "New",
                "last_name": "Parent",
                "phone_e164": "",
                "password1": "s3cure-pass-123",
                "password2": "s3cure-pass-123",
                "next": cls.get_absolute_url() + "#register",
            },
        )
        assert response.status_code == 302
        assert response.url == cls.get_absolute_url() + "#register"

    def test_signup_ignores_an_offsite_return_address(self, client):
        response = client.post(
            reverse("signup"),
            {
                "email": "new@parent.test",
                "first_name": "New",
                "last_name": "Parent",
                "phone_e164": "",
                "password1": "s3cure-pass-123",
                "password2": "s3cure-pass-123",
                "next": "https://evil.example/phish",
            },
        )
        assert response.status_code == 302
        assert response.url == reverse("parent_home")

    def test_login_returns_to_the_class(self, client):
        parent = UserFactory(email="p@family.test")
        parent.set_password("s3cure-pass-123")
        parent.save()
        cls = ActivityClassFactory()
        response = client.post(
            reverse("login"),
            {"username": "p@family.test", "password": "s3cure-pass-123", "next": cls.get_absolute_url() + "#register"},
        )
        assert response.status_code == 302
        assert response.url == cls.get_absolute_url() + "#register"

    def test_new_parent_is_sent_to_add_a_child_and_comes_back(self, client):
        parent = UserFactory()
        client.force_login(parent)
        cls = ActivityClassFactory()

        content = client.get(cls.get_absolute_url()).content.decode()
        assert f'{reverse("child_add")}?{self._back_here(cls)}' in content
        assert "Add a child" in content

        response = client.post(
            reverse("child_add") + f"?next={cls.get_absolute_url()}%23register",
            {"first_name": "Ada", "last_name": "Test", "school_class": "P2E", "date_of_birth": "2018-04-01", "notes": ""},
        )
        assert response.status_code == 302
        assert response.url == cls.get_absolute_url() + "#register"

        content = client.get(cls.get_absolute_url()).content.decode()
        assert 'name="child"' in content  # the register form, with Ada in it
        assert "Ada Test" in content

    def test_child_add_without_return_address_goes_home(self, client):
        client.force_login(UserFactory())
        response = client.post(
            reverse("child_add"),
            {"first_name": "Ada", "last_name": "Test", "school_class": "P2E", "date_of_birth": "2018-04-01", "notes": ""},
        )
        assert response.url == reverse("parent_home")


class TestEnrollmentViews:
    def test_parent_can_request_place(self, client):
        parent = UserFactory()
        child = ChildFactory(parent=parent)
        cls = ActivityClassFactory()
        client.force_login(parent)

        response = client.post(
            reverse("enroll", args=[cls.pk]), {"child": child.pk, "terms_accepted": "1"}
        )

        assert response.status_code == 302
        assert Enrollment.objects.filter(
            child=child, activity_class=cls, status=Enrollment.Status.REQUESTED
        ).exists()

    def test_parent_cannot_touch_other_familys_enrollment(self, client):
        enrollment = services.register(ChildFactory(), ActivityClassFactory())
        stranger = UserFactory()
        client.force_login(stranger)

        response = client.post(reverse("enrollment_cancel", args=[enrollment.pk]))

        assert response.status_code == 404
        enrollment.refresh_from_db()
        assert enrollment.status == Enrollment.Status.REQUESTED

    def test_parent_cannot_enroll_someone_elses_child(self, client):
        other_child = ChildFactory()
        cls = ActivityClassFactory()
        stranger = UserFactory()
        client.force_login(stranger)

        response = client.post(
            reverse("enroll", args=[cls.pk]), {"child": other_child.pk, "terms_accepted": "1"}
        )

        assert response.status_code == 404
        assert not Enrollment.objects.filter(child=other_child).exists()

    def test_offer_confirm_via_dashboard(self, client):
        admin = AdminFactory()
        parent = UserFactory()
        child = ChildFactory(parent=parent)
        cls = ActivityClassFactory(capacity=1)
        blocker = services.approve_request(
            services.register(ChildFactory(), cls), admin
        )
        waitlisted = services.approve_request(services.register(child, cls), admin)
        services.cancel(blocker, Enrollment.CancelReason.PARENT)
        offered = services.offer_seat(waitlisted, admin)

        client.force_login(parent)
        response = client.post(reverse("offer_confirm", args=[offered.pk]))

        assert response.status_code == 302
        offered.refresh_from_db()
        assert offered.status == Enrollment.Status.ENROLLED


class TestAdminTools:
    def test_requests_queue_requires_admin(self, client):
        client.force_login(UserFactory())
        assert client.get(reverse("admintools_requests")).status_code == 403

    def test_admin_can_approve_from_queue(self, client):
        admin = AdminFactory()
        enrollment = services.register(ChildFactory(), ActivityClassFactory())
        client.force_login(admin)

        response = client.post(
            reverse("admintools_request_approve", args=[enrollment.pk])
        )

        assert response.status_code == 302
        enrollment.refresh_from_db()
        assert enrollment.status == Enrollment.Status.ENROLLED

    def test_admin_can_offer_seat_from_waitlist_page(self, client):
        admin = AdminFactory()
        cls = ActivityClassFactory(capacity=1)
        enrolled = services.approve_request(services.register(ChildFactory(), cls), admin)
        waitlisted = services.approve_request(services.register(ChildFactory(), cls), admin)
        services.cancel(enrolled, Enrollment.CancelReason.PARENT)
        client.force_login(admin)

        page = client.get(reverse("admintools_waitlist", args=[cls.pk]))
        assert page.status_code == 200

        response = client.post(
            reverse("admintools_waitlist_offer", args=[waitlisted.pk])
        )
        assert response.status_code == 302
        waitlisted.refresh_from_db()
        assert waitlisted.status == Enrollment.Status.OFFERED
