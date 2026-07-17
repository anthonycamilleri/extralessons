"""Create demo data for local development: accounts, a term, and classes.

Idempotent — safe to run repeatedly. All demo passwords are 'demo1234'.
"""
import datetime

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import Child, Guardian, SiteConfig, User
from apps.catalog.models import ActivityClass, Provider, Term, generate_sessions

PASSWORD = "demo1234"


class Command(BaseCommand):
    help = "Seed demo data for local development (idempotent)."

    def handle(self, *args, **options):
        config = SiteConfig.get()
        config.school_name = "St Example Primary"
        config.catalogue_intro = (
            "Browse this term's extra-curricular activities and enroll your children. "
            "Places are confirmed by the school office."
        )
        config.save()

        admin = self._user("admin@school.test", User.Role.ADMIN, "Amy", "Admin",
                           is_staff=True, is_superuser=True)
        coach = self._user("coach@provider.test", User.Role.PROVIDER, "Carlos", "Coach")
        tutor = self._user("tutor@provider.test", User.Role.PROVIDER, "Tina", "Tutor")
        parent1 = self._user("parent1@family.test", User.Role.PARENT, "Paula", "Parent")
        parent2 = self._user("parent2@family.test", User.Role.PARENT, "Peter", "Parent")

        sports, _ = Provider.objects.get_or_create(
            name="AllStars Sports",
            defaults={"description": "Football and athletics coaching.",
                      "contact_email": "hello@allstars.test"},
        )
        sports.members.add(coach)
        arts, _ = Provider.objects.get_or_create(
            name="Bright Minds",
            defaults={"description": "Chess, drama and robotics clubs.",
                      "contact_email": "info@brightminds.test"},
        )
        arts.members.add(tutor)

        today = timezone.localdate()
        term, _ = Term.objects.get_or_create(
            name=f"Demo Term {today.year}",
            defaults={
                "start_date": today - datetime.timedelta(days=7),
                "end_date": today + datetime.timedelta(days=70),
                "is_active": True,
            },
        )

        classes = [
            (sports, "Football Juniors", "football-juniors", 5, 8, 12, 0,
             "Fun, skills-focused football for beginners. All levels welcome.",
             "Bring shin pads, water bottle and trainers."),
            (sports, "Athletics Club", "athletics-club", 8, 11, 20, 2,
             "Running, jumping and throwing — a taste of every discipline.",
             "Outdoor kit; sessions run rain or shine."),
            (arts, "Chess Club", "chess-club", 6, 12, 2, 1,
             "Learn openings, tactics and play friendly tournaments.",
             "Beginners welcome, boards provided."),
            (arts, "Drama Workshop", "drama-workshop", 7, 12, 16, 3,
             "Improvisation and stagecraft leading to an end-of-term show.",
             "Comfortable clothes. Parents are invited to the final performance."),
        ]
        for provider, title, slug, amin, amax, cap, weekday, desc, extra in classes:
            cls, created = ActivityClass.objects.get_or_create(
                term=term,
                slug=slug,
                defaults={
                    "provider": provider,
                    "title": title,
                    "description": desc,
                    "extra_details": extra,
                    "age_min": amin,
                    "age_max": amax,
                    "capacity": cap,
                    "weekday": weekday,
                    "start_time": datetime.time(15, 30),
                    "end_time": datetime.time(16, 30),
                    "location": "School grounds",
                    "status": ActivityClass.Status.PUBLISHED,
                },
            )
            if created:
                generate_sessions(cls)

        self._child(parent1, "Lena", "Parent", 8)
        self._child(parent1, "Marco", "Parent", 6)
        self._child(parent2, "Sofia", "Parent", 10)

        self.stdout.write(self.style.SUCCESS(
            "Demo data ready. Accounts (password 'demo1234'):\n"
            "  admin@school.test    — school admin (staff)\n"
            "  coach@provider.test  — provider (AllStars Sports)\n"
            "  tutor@provider.test  — provider (Bright Minds)\n"
            "  parent1@family.test  — parent with 2 children\n"
            "  parent2@family.test  — parent with 1 child"
        ))

    def _user(self, email, role, first, last, **extra):
        user, created = User.objects.get_or_create(
            email=email,
            defaults={"role": role, "first_name": first, "last_name": last, **extra},
        )
        if created:
            user.set_password(PASSWORD)
            user.save()
        return user

    def _child(self, parent, first, last, age):
        today = timezone.localdate()
        dob = today.replace(year=today.year - age)
        child, created = Child.objects.get_or_create(
            first_name=first, last_name=last, date_of_birth=dob
        )
        if created or not child.guardian_links.filter(user=parent).exists():
            Guardian.objects.get_or_create(child=child, user=parent,
                                           defaults={"is_primary": True})
        return child
