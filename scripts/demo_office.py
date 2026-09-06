"""Fill a freshly seeded demo database with a day's worth of office work.

`seed_demo` gives us a term and a catalogue; the help screenshots need the
states an admin actually looks at — requests waiting for review, a full class
with a waiting list, an offer out for confirmation, a family asking to leave.

Run against a throwaway database, from scripts/capture_help_screenshots.py:

    python scripts/demo_office.py

Prints the id of the class whose roster is worth photographing.
"""
import datetime
import os
import sys
from pathlib import Path

import django

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django.setup()

from django.utils import timezone  # noqa: E402

from apps.accounts.models import Child, Guardian, User  # noqa: E402
from apps.catalog.models import ActivityClass  # noqa: E402
from apps.enrollments import services  # noqa: E402

PASSWORD = "demo1234"

# Ordinary school names, so a screenshot looks like a real register rather
# than like test data.
FAMILIES = [
    ("Novak", "Ana", 8, "P3E"), ("Novak", "Luka", 6, "P1E"),
    ("Horvat", "Mia", 9, "P4S"), ("Kovač", "Jakob", 7, "P2E"),
    ("Zupan", "Ema", 10, "P5E"), ("Bergmann", "Nils", 8, "P3E"),
    ("Dubois", "Camille", 9, "P4E"), ("Rossi", "Matteo", 7, "P2S"),
    ("Silva", "Beatriz", 8, "P3E"), ("Okonkwo", "Chidi", 10, "P5E"),
    ("Fischer", "Lena", 9, "P4E"), ("Marin", "Sofia", 8, "P3S"),
]


def family(surname, first_name, age, school_class):
    parent, created = User.objects.get_or_create(
        email=f"{first_name.lower()}.{surname.lower()}@family.test",
        defaults={"role": User.Role.PARENT, "first_name": "Parent", "last_name": surname},
    )
    if created:
        parent.set_password(PASSWORD)
        parent.save()
    today = timezone.localdate()
    child, _ = Child.objects.get_or_create(
        first_name=first_name,
        last_name=surname,
        defaults={
            "date_of_birth": today.replace(year=today.year - age) - datetime.timedelta(days=40),
            "school_class": school_class,
            "may_leave_alone": age >= 10,
        },
    )
    Guardian.objects.get_or_create(child=child, user=parent, defaults={"is_primary": True})
    return child


def run():
    admin = User.objects.get(email="admin@school.test")
    children = [family(*details) for details in FAMILIES]

    chess = ActivityClass.objects.get(slug="chess-club")
    football = ActivityClass.objects.get(slug="football-juniors")
    coding = ActivityClass.objects.get(slug="coding-club")

    # The roster worth photographing: four children in, one seat offered out
    # and waiting on an answer, three families still queueing, one place still
    # free to give. So a seat is free to offer while parents are shown none
    # available — the distinction the waiting-list guide is about.
    #
    # Built the way a real term builds one: a small class fills, the school
    # finds room for two more, and one of the waiting families is offered a
    # place.
    chess.capacity = 4
    chess.save(update_fields=["capacity"])
    for child in children[:8]:
        services.approve_request(services.register(child, chess, terms_accepted=True), admin)
    chess.capacity = 6
    chess.save(update_fields=["capacity"])
    services.offer_seat(chess.enrollments.filter(status="WAITLISTED").first(), admin)

    # A healthy class, so the roster and the register look normal.
    for child in children[2:8]:
        services.approve_request(services.register(child, football, terms_accepted=True), admin)

    # And the queue an admin opens the site for.
    for child in children[6:]:
        services.register(child, coding, terms_accepted=True)
    services.register(children[0], football, terms_accepted=True)

    # One family asking to leave, so the lower half of the Requests page has
    # something in it. Registered long enough ago that the window has closed.
    leaving = football.enrollments.filter(status="ENROLLED").first()
    leaving.created_at = timezone.now() - datetime.timedelta(days=60)
    leaving.enrolled_at = leaving.created_at
    leaving.save(update_fields=["created_at", "enrolled_at"])
    services.request_cancellation(leaving, leaving.child.guardians.first())

    print(chess.pk)


if __name__ == "__main__":
    run()
