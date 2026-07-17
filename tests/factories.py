import datetime

import factory
from django.utils import timezone

from apps.accounts.models import Child, Guardian, User
from apps.catalog.models import ActivityClass, Provider, Term
from apps.enrollments.models import Enrollment


class UserFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = User
        skip_postgeneration_save = True

    email = factory.Sequence(lambda n: f"user{n}@test.example")
    first_name = factory.Sequence(lambda n: f"First{n}")
    last_name = "Tester"
    role = User.Role.PARENT

    @factory.post_generation
    def password(self, create, extracted, **kwargs):
        self.set_password(extracted or "pw")
        if create:
            self.save(update_fields=["password"])


class AdminFactory(UserFactory):
    role = User.Role.ADMIN
    is_staff = True


class ProviderUserFactory(UserFactory):
    role = User.Role.PROVIDER


class ChildFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Child

    first_name = factory.Sequence(lambda n: f"Kid{n}")
    last_name = "Tester"
    date_of_birth = factory.LazyFunction(
        lambda: timezone.localdate() - datetime.timedelta(days=365 * 8)
    )

    @factory.post_generation
    def parent(self, create, extracted, **kwargs):
        if create:
            parent = extracted or UserFactory()
            Guardian.objects.create(child=self, user=parent, is_primary=True)


class ProviderFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Provider

    name = factory.Sequence(lambda n: f"Provider {n}")


class TermFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Term

    name = factory.Sequence(lambda n: f"Term {n}")
    start_date = factory.LazyFunction(lambda: timezone.localdate() - datetime.timedelta(days=7))
    end_date = factory.LazyFunction(lambda: timezone.localdate() + datetime.timedelta(days=60))
    is_active = True


class ActivityClassFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ActivityClass

    provider = factory.SubFactory(ProviderFactory)
    term = factory.SubFactory(TermFactory)
    title = factory.Sequence(lambda n: f"Class {n}")
    slug = factory.Sequence(lambda n: f"class-{n}")
    description = "A test class."
    age_min = 5
    age_max = 12
    capacity = 2
    weekday = 0
    start_time = datetime.time(15, 0)
    end_time = datetime.time(16, 0)
    status = ActivityClass.Status.PUBLISHED


class EnrollmentFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Enrollment

    child = factory.SubFactory(ChildFactory)
    activity_class = factory.SubFactory(ActivityClassFactory)
    status = Enrollment.Status.REQUESTED
