import datetime

from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.forms import ChildForm, GuardianInviteForm, ProfileForm
from apps.accounts.models import Child, Guardian, SiteConfig
from apps.accounts.permissions import parent_required
from apps.accounts.redirects import safe_next
from apps.catalog.models import ActivityClass, ClassSession
from apps.enrollments import ages
from apps.enrollments import services as enrollment_services
from apps.enrollments.models import Attendance, Enrollment
from apps.enrollments.services import EnrollmentError
from apps.notifications import services as notification_services


def _own_children(user):
    return Child.objects.for_guardian(user).prefetch_related("guardians")


WEEK_AHEAD_DAYS = 7


def _coming_up(enrolled, today):
    """The family's lessons over the next week, grouped by day.

    Cancelled lessons are listed too, marked as such: "no Chess Club on
    Wednesday" is exactly what a parent opens the page to find out. Each entry
    names the children in that class, so siblings in one club share a line.
    """
    by_class = {}
    for enrollment in enrolled:
        by_class.setdefault(enrollment.activity_class_id, []).append(enrollment.child)
    sessions = (
        ClassSession.objects.filter(
            activity_class_id__in=by_class,
            date__gte=today,
            date__lt=today + datetime.timedelta(days=WEEK_AHEAD_DAYS),
        )
        .select_related("activity_class__provider")
        .order_by("date", "activity_class__start_time", "activity_class__title")
    )
    days = []
    for session in sessions:
        if not days or days[-1]["date"] != session.date:
            offset = (session.date - today).days
            label = {0: "Today", 1: "Tomorrow"}.get(offset)
            days.append({"date": session.date, "label": label, "lessons": []})
        days[-1]["lessons"].append(
            {"session": session, "children": by_class[session.activity_class_id]}
        )
    return days


@parent_required
def home(request):
    children = list(_own_children(request.user))
    enrollments = list(
        Enrollment.objects.filter(
            child__in=children, status__in=Enrollment.ACTIVE_STATUSES
        )
        .select_related("child")
        .order_by("created_at")
    )
    # One query for every class on the page, carrying the next-lesson dates the
    # catalogue already knows how to show.
    classes = {
        cls.pk: cls
        for cls in ActivityClass.objects.filter(
            pk__in={e.activity_class_id for e in enrollments}
        )
        .select_related("provider", "term")
        .with_next_session()
    }
    window_days = SiteConfig.get().withdrawal_window_days
    now = timezone.now()
    for enrollment in enrollments:
        enrollment.activity_class = classes[enrollment.activity_class_id]
        enrollment.withdrawal_open = enrollment.can_withdraw(window_days, now=now)
        # Only a confirmed place has a closing date worth showing.
        enrollment.withdrawal_deadline_at = (
            enrollment.withdrawal_deadline(window_days)
            if enrollment.status == Enrollment.Status.ENROLLED
            else None
        )
    by_child = {child.pk: [] for child in children}
    for enrollment in enrollments:
        by_child[enrollment.child_id].append(enrollment)
    families = [
        {"child": child, "enrollments": by_child[child.pk]} for child in children
    ]
    offers = [e for e in enrollments if e.status == Enrollment.Status.OFFERED]
    enrolled = [e for e in enrollments if e.status == Enrollment.Status.ENROLLED]
    return render(
        request,
        "dashboards/parent/home.html",
        {
            "families": families,
            "offers": offers,
            "has_enrolled": bool(enrolled),
            "coming_up": _coming_up(enrolled, timezone.localdate()),
            "week_ahead_days": WEEK_AHEAD_DAYS,
            "window_days": window_days,
        },
    )


@parent_required
def profile(request):
    form = ProfileForm(request.POST or None, instance=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Profile updated.")
        return redirect("parent_home")
    return render(request, "dashboards/parent/profile.html", {"form": form})


@parent_required
def child_add(request):
    form = ChildForm(request.POST or None)
    # A parent sent here from a class page ("add your children first") goes
    # straight back to that class's register form once the child exists.
    next_url = safe_next(request)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            child = form.save()
            Guardian.objects.create(child=child, user=request.user, is_primary=True)
        messages.success(request, f"{child.full_name} added to your family.")
        return redirect(next_url or "parent_home")
    return render(
        request,
        "dashboards/parent/child_form.html",
        {"form": form, "child": None, "next": next_url or ""},
    )


@parent_required
def child_edit(request, child_id):
    child = get_object_or_404(_own_children(request.user), pk=child_id)
    form = ChildForm(request.POST or None, instance=child)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "Details updated.")
        return redirect("parent_home")
    return render(request, "dashboards/parent/child_form.html", {"form": form, "child": child})


def _own_enrollments(user):
    return Enrollment.objects.filter(child__guardians=user).select_related(
        "child", "activity_class"
    )


@parent_required
@require_POST
def enroll(request, class_id):
    cls = get_object_or_404(ActivityClass.objects.published(), pk=class_id)
    child = get_object_or_404(_own_children(request.user), pk=request.POST.get("child"))
    terms_accepted = bool(request.POST.get("terms_accepted"))
    # The checkbox is `required` in the browser; this is the check that counts.
    if SiteConfig.get().has_terms and not terms_accepted:
        messages.error(
            request, "Please confirm that you have read the terms and conditions."
        )
        return redirect(f"{cls.get_absolute_url()}#register")
    # The class's age range is advice, not a gate: if the child falls outside
    # it, say so once and let the parent decide. The tick comes back as
    # `age_confirmed`, so a direct POST cannot skip the warning by accident.
    age = ages.outside_recommended_range(cls, child)
    if age is not None and not request.POST.get("age_confirmed"):
        return render(
            request,
            "dashboards/parent/enroll_age_confirm.html",
            {
                "cls": cls,
                "child": child,
                "age": age,
                "terms_accepted": terms_accepted,
                "places_free": cls.places_available_now(),
            },
        )
    try:
        enrollment = enrollment_services.register(child, cls, terms_accepted=terms_accepted)
    except EnrollmentError as exc:
        messages.error(request, str(exc))
        return redirect(cls.get_absolute_url())
    messages.success(
        request,
        f"Request received! We'll confirm {child.first_name}'s place in "
        f"{cls.title} as soon as we can — keep an eye on your inbox.",
    )
    return redirect("parent_home")


def _enrollment_action(request, enrollment_id, action, success_message, level=messages.success):
    """Shared shape of every parent enrollment action: scope-check the
    enrollment, run one service call, flash the outcome, return home."""
    enrollment = get_object_or_404(_own_enrollments(request.user), pk=enrollment_id)
    try:
        # Every service hands back the row as it now stands; the message is
        # written from that, so it can describe what actually happened.
        enrollment = action(enrollment) or enrollment
    except EnrollmentError as exc:
        messages.error(request, str(exc))
    else:
        level(request, success_message(enrollment))
    return redirect("parent_home")


@parent_required
@require_POST
def enrollment_cancel(request, enrollment_id):
    """Withdraw, or ask to cancel: the service decides which applies, and the
    message tells the parent which one happened."""

    def outcome(enrollment):
        child, title = enrollment.child.first_name, enrollment.activity_class.title
        if enrollment.status == Enrollment.Status.CANCELLED:
            return f"{child} has been withdrawn from {title}. The place is free for another family."
        return (
            f"We've passed your request to cancel {child}'s place in {title} to the "
            f"office. Until they confirm — you'll get an email — the place is still "
            f"{child}'s, so please keep attending."
        )

    return _enrollment_action(
        request,
        enrollment_id,
        lambda e: enrollment_services.parent_cancel(e, actor=request.user),
        outcome,
    )


@parent_required
@require_POST
def offer_confirm(request, enrollment_id):
    return _enrollment_action(
        request,
        enrollment_id,
        enrollment_services.confirm_offer,
        lambda e: (
            f"Confirmed — {e.child.first_name} is enrolled in {e.activity_class.title}!"
        ),
    )


@parent_required
@require_POST
def offer_decline(request, enrollment_id):
    return _enrollment_action(
        request,
        enrollment_id,
        enrollment_services.decline_offer,
        lambda e: "Offer declined — the seat will go to another family.",
        level=messages.info,
    )


@parent_required
def enrollment_attendance(request, enrollment_id):
    """Attendance history for one of the family's enrollments."""
    enrollment = get_object_or_404(
        _own_enrollments(request.user).select_related(
            "activity_class__provider", "activity_class__term"
        ),
        pk=enrollment_id,
    )
    today = timezone.localdate()
    sessions = enrollment.activity_class.sessions.filter(cancelled=False)
    marks = {
        a.session_id: a.present
        for a in Attendance.objects.filter(
            child=enrollment.child, session__in=sessions
        )
    }
    rows = []
    present_count = taken_count = 0
    for session in sessions:
        if session.pk in marks:
            state = "present" if marks[session.pk] else "absent"
            taken_count += 1
            present_count += int(marks[session.pk])
        elif session.date > today:
            state = "upcoming"
        else:
            state = "not_taken"
        rows.append({"session": session, "state": state})
    return render(
        request,
        "dashboards/parent/attendance.html",
        {
            "enrollment": enrollment,
            "rows": rows,
            "present_count": present_count,
            "taken_count": taken_count,
        },
    )


@parent_required
def child_invite_guardian(request, child_id):
    child = get_object_or_404(_own_children(request.user), pk=child_id)
    form = GuardianInviteForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        invite = form.save(commit=False)
        invite.child = child
        invite.invited_by = request.user
        if child.guardians.filter(email__iexact=invite.email).exists():
            messages.info(request, "That person already manages this child.")
            return redirect("parent_home")
        with transaction.atomic():
            invite.save()
            notification_services.queue_guardian_invite(invite)
        messages.success(request, f"Invitation sent to {invite.email}.")
        return redirect("parent_home")
    return render(
        request,
        "dashboards/parent/invite_guardian.html",
        {"form": form, "child": child},
    )
