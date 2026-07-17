from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.accounts.forms import ChildForm, GuardianInviteForm, ProfileForm
from apps.accounts.models import Child, Guardian
from apps.accounts.permissions import parent_required
from apps.catalog.models import ActivityClass
from apps.enrollments import services as enrollment_services
from apps.enrollments.models import Attendance, Enrollment
from apps.enrollments.services import EnrollmentError
from apps.notifications import services as notification_services


def _own_children(user):
    return Child.objects.filter(guardians=user).prefetch_related("guardians")


@parent_required
def home(request):
    children = _own_children(request.user)
    enrollments = (
        Enrollment.objects.filter(
            child__in=children, status__in=Enrollment.ACTIVE_STATUSES
        )
        .select_related("child", "activity_class__provider", "activity_class__term")
        .order_by("child__first_name", "created_at")
    )
    offers = [e for e in enrollments if e.status == Enrollment.Status.OFFERED]
    return render(
        request,
        "dashboards/parent/home.html",
        {"children": children, "enrollments": enrollments, "offers": offers},
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
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            child = form.save()
            Guardian.objects.create(child=child, user=request.user, is_primary=True)
        messages.success(request, f"{child.full_name} added to your family.")
        return redirect("parent_home")
    return render(request, "dashboards/parent/child_form.html", {"form": form, "child": None})


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
    try:
        enrollment = enrollment_services.register(child, cls)
    except EnrollmentError as exc:
        messages.error(request, str(exc))
        return redirect(cls.get_absolute_url())
    messages.success(
        request,
        f"Request received! The school office will confirm {child.first_name}'s "
        f"place in {cls.title} shortly.",
    )
    return redirect("parent_home")


@parent_required
@require_POST
def enrollment_cancel(request, enrollment_id):
    enrollment = get_object_or_404(_own_enrollments(request.user), pk=enrollment_id)
    try:
        enrollment_services.cancel(
            enrollment, Enrollment.CancelReason.PARENT, actor=request.user
        )
    except EnrollmentError as exc:
        messages.error(request, str(exc))
        return redirect("parent_home")
    messages.success(
        request,
        f"{enrollment.child.first_name}'s registration for "
        f"{enrollment.activity_class.title} has been cancelled.",
    )
    return redirect("parent_home")


@parent_required
@require_POST
def offer_confirm(request, enrollment_id):
    enrollment = get_object_or_404(_own_enrollments(request.user), pk=enrollment_id)
    try:
        enrollment_services.confirm_offer(enrollment)
    except EnrollmentError as exc:
        messages.error(request, str(exc))
        return redirect("parent_home")
    messages.success(
        request,
        f"Confirmed — {enrollment.child.first_name} is enrolled in "
        f"{enrollment.activity_class.title}!",
    )
    return redirect("parent_home")


@parent_required
@require_POST
def offer_decline(request, enrollment_id):
    enrollment = get_object_or_404(_own_enrollments(request.user), pk=enrollment_id)
    try:
        enrollment_services.decline_offer(enrollment)
    except EnrollmentError as exc:
        messages.error(request, str(exc))
        return redirect("parent_home")
    messages.info(request, "Offer declined — the seat will go to another family.")
    return redirect("parent_home")


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
