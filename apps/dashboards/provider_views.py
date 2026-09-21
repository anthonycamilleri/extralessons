"""The provider dashboard: classes, registers, attendance, announcements, and
the provider's instructors.

Two kinds of account come through here, told apart by ActivityClass.run_by:
a provider's own accounts (Provider.members), who see every class and manage
the instructors; and instructors, who see the classes assigned to them. The
class pages do not care which one is asking, only that the class is theirs.
"""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from datetime import timedelta

from django.db import transaction
from django.db.models import Count, Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.models import User
from apps.accounts.permissions import provider_required
from apps.catalog.models import ActivityClass, ClassSession, Instructor, Provider
from apps.enrollments.models import Attendance, Enrollment
from apps.notifications import services as notification_services
from apps.notifications.forms import RichTextField
from apps.notifications.models import Broadcast

from .provider_forms import (
    CERTIFICATE_TYPES,
    InstructorAccountForm,
    InstructorClassesForm,
    InstructorProfileForm,
)


def _own_classes(user):
    return (
        ActivityClass.objects.run_by(user)
        .select_related("provider", "term", "term__school_year")
        .order_by("-term__start_date", "weekday", "start_time")
    )


def _managed_providers(user):
    return Provider.objects.managed_by(user).order_by("name")


def _managed_instructor_or_404(user, instructor_id):
    return get_object_or_404(
        Instructor.objects.managed_by(user).select_related("user", "provider"),
        pk=instructor_id,
    )


# How far back the home page chases a register that was never taken. A week:
# long enough to catch a forgotten lesson, short enough that the list is
# never a wall of old dates the provider has already given up on.
UNTAKEN_LOOKBACK_DAYS = 7


def _with_attendance_counts(sessions):
    """Annotate sessions with how many marks were saved and how many were present.

    A session with marks is one whose register was taken; the two counts read
    as "12 of 14 present". Present is counted with a filtered aggregate so a
    single query covers both.
    """
    return sessions.annotate(
        taken_count=Count("attendance"),
        present_count=Count("attendance", filter=Q(attendance__present=True)),
    )


def _last_marks(sessions):
    """Who last saved each session's register, and when: {session_id: Attendance}.

    marked_at is auto_now, so the newest row of a session is its last save.
    One query for the whole class rather than one subquery per session.
    """
    last = {}
    marks = (
        Attendance.objects.filter(session__in=sessions)
        .select_related("marked_by")
        .order_by("marked_at")
    )
    for mark in marks:
        last[mark.session_id] = mark
    return last


def _live_sessions(user):
    """Sessions a provider can still act on: their published classes, not cancelled."""
    return (
        ClassSession.objects.filter(
            activity_class__in=_own_classes(user).filter(
                status=ActivityClass.Status.PUBLISHED
            ),
            cancelled=False,
        )
        .select_related("activity_class")
        .order_by("date", "activity_class__start_time", "activity_class__title")
    )


@provider_required
def home(request):
    classes = _own_classes(request.user).with_counts().prefetch_related("instructors__user")
    today = timezone.localdate()
    live = _with_attendance_counts(_live_sessions(request.user))
    todays_sessions = list(live.filter(date=today))
    # Registers not taken for lessons already held: what the coach forgot.
    untaken = list(
        live.filter(
            date__lt=today, date__gte=today - timedelta(days=UNTAKEN_LOOKBACK_DAYS)
        ).filter(taken_count=0)
    )
    next_session = None
    if not todays_sessions:
        next_session = live.filter(date__gt=today).first()
    return render(
        request,
        "dashboards/provider/home.html",
        {
            "classes": classes,
            "managed_providers": _managed_providers(request.user),
            "my_profiles": Instructor.objects.for_user(request.user).select_related("provider"),
            "today": today,
            "todays_sessions": todays_sessions,
            "untaken_sessions": untaken,
            "next_session": next_session,
        },
    )


@provider_required
def class_detail(request, class_id):
    cls = get_object_or_404(_own_classes(request.user).with_counts(), pk=class_id)
    roster = (
        cls.enrollments.filter(status=Enrollment.Status.ENROLLED)
        .select_related("child")
        .prefetch_related("child__guardians")
        .order_by("child__first_name", "child__last_name")
    )
    waitlisted = cls.enrollments.waitlist_fifo().select_related("child")
    today = timezone.localdate()
    sessions = list(_with_attendance_counts(cls.sessions.all()))
    last_marks = _last_marks(sessions)
    for session in sessions:
        session.last_mark = last_marks.get(session.pk)
    next_session = cls.sessions.filter(cancelled=False, date__gte=today).first()
    return render(
        request,
        "dashboards/provider/class_detail.html",
        {
            "cls": cls,
            "roster": roster,
            "waitlisted": waitlisted,
            "sessions": sessions,
            "next_session": next_session,
            "holidays": cls.skipped_holidays(),
            "today": today,
            "instructors": cls.instructors.select_related("user"),
            "manages_provider": cls.provider.is_managed_by(request.user),
        },
    )


@provider_required
def attendance(request, class_id, session_id):
    cls = get_object_or_404(_own_classes(request.user), pk=class_id)
    session = get_object_or_404(ClassSession, pk=session_id, activity_class=cls)
    roster = (
        cls.enrollments.filter(status=Enrollment.Status.ENROLLED)
        .select_related("child")
        .order_by("child__first_name", "child__last_name")
    )
    # Where "Back" and the post-save redirect go. The home page's Today panel
    # links here with ?back=home so a coach working through the day's classes
    # lands back on the list rather than on this class's page.
    back_home = request.GET.get("back") == "home"

    if request.method == "POST":
        present_ids = {
            int(value) for value in request.POST.getlist("present") if value.isdigit()
        }
        with transaction.atomic():
            for enrollment in roster:
                Attendance.objects.update_or_create(
                    session=session,
                    child=enrollment.child,
                    defaults={
                        "present": enrollment.child_id in present_ids,
                        "marked_by": request.user,
                    },
                )
        present_count = sum(1 for e in roster if e.child_id in present_ids)
        messages.success(
            request,
            f"Attendance saved for {cls.title}, {session.date:%-d %B}: "
            f"{present_count} of {len(roster)} present.",
        )
        if back_home:
            return redirect("provider_home")
        return redirect("provider_class", class_id=cls.pk)

    existing = {
        a.child_id: a.present for a in Attendance.objects.filter(session=session)
    }
    rows = [
        {
            "enrollment": enrollment,
            # Not yet marked children start ticked: the coach unticks absentees.
            "present": existing.get(enrollment.child_id, True),
            "marked": enrollment.child_id in existing,
        }
        for enrollment in roster
    ]
    last_mark = _last_marks([session]).get(session.pk)
    siblings = cls.sessions.filter(cancelled=False)
    return render(
        request,
        "dashboards/provider/attendance.html",
        {
            "cls": cls,
            "session": session,
            "rows": rows,
            "taken": bool(existing),
            "last_mark": last_mark,
            "present_count": sum(1 for row in rows if row["present"]),
            "previous_session": siblings.filter(date__lt=session.date).order_by("-date").first(),
            "next_session": siblings.filter(date__gt=session.date).first(),
            "today": timezone.localdate(),
            "back_home": back_home,
            "back_url": reverse("provider_home") if back_home else reverse("provider_class", args=[cls.pk]),
        },
    )


class ProviderBroadcastForm(forms.Form):
    classes = forms.ModelMultipleChoiceField(
        queryset=ActivityClass.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label="Send to families of",
    )
    audience = forms.ChoiceField(
        choices=Broadcast.Audience.choices,
        widget=forms.RadioSelect,
        initial=Broadcast.Audience.EVERYONE,
        label="Who gets it",
        help_text="Everyone with a live place in those classes — enrolled, waiting, "
        "offered a seat, or not reviewed yet — or only the families still waiting. "
        "The third adds the places since cancelled, and is the only audience a "
        "cancelled class still has.",
    )
    subject = forms.CharField(max_length=200)
    body_html = RichTextField()

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Scope strictly to the provider's own classes; re-validated on POST.
        self.fields["classes"].queryset = _own_classes(user).filter(
            status__in=[ActivityClass.Status.PUBLISHED, ActivityClass.Status.CANCELLED]
        )


@provider_required
def broadcast(request):
    form = ProviderBroadcastForm(request.user, request.POST or None)
    if request.method == "POST" and form.is_valid():
        _, count = notification_services.create_broadcast(
            sender=request.user,
            scope=Broadcast.Scope.SELECTED_CLASSES,
            subject=form.cleaned_data["subject"],
            body_html=form.cleaned_data["body_html"],
            classes=form.cleaned_data["classes"],
            audience=form.cleaned_data["audience"],
        )
        # A waiting-list message can match nobody; a success note would read
        # as if it had gone out.
        if count:
            messages.success(
                request,
                f"Message queued for {notification_services.family_count_phrase(count)}.",
            )
        else:
            messages.warning(
                request, "Nobody matched that audience, so the message was not sent to anyone."
            )
        return redirect("provider_home")
    return render(request, "dashboards/provider/broadcast.html", {"form": form})


# -- Instructors -------------------------------------------------------------
#
# Managed by the provider's own accounts (Provider.members). An instructor who
# is not also a member sees none of these pages except their own profile.


@provider_required
def instructors(request):
    """Every instructor of every provider this account runs, with their
    classes and where their certificate stands."""
    providers = list(_managed_providers(request.user))
    if not providers:
        return render(request, "dashboards/provider/instructors.html", {"providers": []})
    for provider in providers:
        provider.instructor_rows = list(
            provider.instructors.select_related("user").prefetch_related("classes__term")
        )
        provider.self_is_instructor = any(
            row.user_id == request.user.pk for row in provider.instructor_rows
        )
    return render(request, "dashboards/provider/instructors.html", {"providers": providers})


@provider_required
def instructor_add(request, provider_id):
    provider = get_object_or_404(_managed_providers(request.user), pk=provider_id)
    form = InstructorAccountForm(provider, request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            user = form.existing_user
            created = user is None
            if created:
                user = User.objects.create_user(
                    form.cleaned_data["email"],
                    password=None,  # unusable until the invitation link sets one
                    role=User.Role.PROVIDER,
                    first_name=form.cleaned_data["first_name"],
                    last_name=form.cleaned_data["last_name"],
                )
            instructor = Instructor.objects.create(provider=provider, user=user)
            instructor.classes.set(form.cleaned_data["classes"])
            if created:
                notification_services.queue_instructor_invite(instructor, request.user)
        if created:
            messages.success(
                request,
                f"{instructor.display_name} has been added. We have emailed {user.email} "
                "a link to choose a password.",
            )
        else:
            messages.success(
                request,
                f"{instructor.display_name} already had an account and is now one of "
                f"{provider.name}'s instructors.",
            )
        return redirect("provider_instructor", instructor_id=instructor.pk)
    return render(
        request,
        "dashboards/provider/instructor_form.html",
        {"form": form, "provider": provider},
    )


@provider_required
@require_POST
def instructor_add_self(request, provider_id):
    """The provider account is also an instructor: give it a profile."""
    provider = get_object_or_404(_managed_providers(request.user), pk=provider_id)
    instructor, created = Instructor.objects.get_or_create(provider=provider, user=request.user)
    if created:
        messages.success(
            request,
            f"You are now listed as an instructor with {provider.name}. Fill in your "
            "profile and pick the classes you teach.",
        )
    return redirect("provider_instructor", instructor_id=instructor.pk)


@provider_required
def instructor_detail(request, instructor_id):
    """A manager's view of one instructor: profile, certificate, classes."""
    instructor = _managed_instructor_or_404(request.user, instructor_id)
    form = InstructorClassesForm(instructor, request.POST or None)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, f"Classes updated for {instructor.display_name}.")
        return redirect("provider_instructor", instructor_id=instructor.pk)
    return render(
        request,
        "dashboards/provider/instructor_detail.html",
        {
            "instructor": instructor,
            "form": form,
            "classes": instructor.classes.select_related("term").order_by(
                "-term__start_date", "weekday", "start_time"
            ),
            "never_logged_in": instructor.user.last_login is None,
            "is_self": instructor.user_id == request.user.pk,
        },
    )


@provider_required
@require_POST
def instructor_resend_invite(request, instructor_id):
    instructor = _managed_instructor_or_404(request.user, instructor_id)
    if instructor.user.last_login is not None:
        messages.info(
            request,
            f"{instructor.display_name} has already logged in. If they have forgotten "
            "their password, the login page has a link to reset it.",
        )
    else:
        notification_services.queue_instructor_invite(instructor, request.user)
        messages.success(request, f"Invitation sent again to {instructor.user.email}.")
    return redirect("provider_instructor", instructor_id=instructor.pk)


@provider_required
@require_POST
def instructor_remove(request, instructor_id):
    """Take an instructor off the provider: profile, certificate and class
    assignments go; the account is switched off if nothing else uses it."""
    instructor = _managed_instructor_or_404(request.user, instructor_id)
    user = instructor.user
    name = instructor.display_name
    files = [
        (field.storage, field.name)
        for field in (instructor.conduct_certificate, instructor.photo)
        if field
    ]
    with transaction.atomic():
        instructor.delete()
        still_used = (
            user.provider_orgs.exists()
            or Instructor.objects.filter(user=user).exists()
            or user.pk == request.user.pk
        )
        if not still_used and user.is_active:
            user.is_active = False
            user.save(update_fields=["is_active"])
    for storage, file_name in files:
        storage.delete(file_name)
    messages.success(request, f"{name} is no longer one of your instructors.")
    return redirect("provider_instructors")


@provider_required
def my_profile(request):
    """The instructor's own profile, wherever it is: straight to the one
    profile most people have, a choice if they teach for several providers."""
    profiles = list(Instructor.objects.for_user(request.user).select_related("provider"))
    if len(profiles) == 1:
        return redirect("provider_instructor_profile", instructor_id=profiles[0].pk)
    if not profiles:
        messages.info(
            request,
            "You do not have an instructor profile yet. Your provider adds you as an "
            "instructor from their dashboard; if you run the provider, use "
            "“I teach too” on the Instructors page.",
        )
        return redirect("provider_home")
    return render(request, "dashboards/provider/my_profiles.html", {"profiles": profiles})


@provider_required
def instructor_profile(request, instructor_id):
    """Edit the profile: the instructor themself, or a manager on their behalf."""
    instructor = get_object_or_404(
        Instructor.objects.visible_to(request.user).select_related("user", "provider"),
        pk=instructor_id,
    )
    form = InstructorProfileForm(request.POST or None, request.FILES or None, instance=instructor)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile saved.")
        if instructor.user_id == request.user.pk:
            return redirect("provider_home")
        return redirect("provider_instructor", instructor_id=instructor.pk)
    return render(
        request,
        "dashboards/provider/instructor_profile.html",
        {
            "form": form,
            "instructor": instructor,
            "is_self": instructor.user_id == request.user.pk,
            "manages_provider": instructor.provider.is_managed_by(request.user),
        },
    )


@login_required
def instructor_certificate(request, instructor_id):
    """Hand out the certificate of police conduct, to the few who may see it.

    Not a provider_required view: the school office (admin role) opens it
    from the admin as well. Everyone else, a parent included, is told it does
    not exist rather than that it is forbidden.
    """
    instructor = get_object_or_404(
        Instructor.objects.select_related("user", "provider"), pk=instructor_id
    )
    if not instructor.may_be_opened_by(request.user) or not instructor.has_certificate:
        raise Http404
    stored = instructor.conduct_certificate
    extension = "." + stored.name.rsplit(".", 1)[-1].lower() if "." in stored.name else ""
    content_type = CERTIFICATE_TYPES.get(extension, "application/octet-stream")
    filename = f"police-conduct-{instructor.user.last_name or 'certificate'}{extension}".lower()
    response = FileResponse(
        stored.open("rb"), content_type=content_type, as_attachment=True, filename=filename
    )
    response["Cache-Control"] = "private, no-store"
    return response
