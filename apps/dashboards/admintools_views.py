from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.accounts.permissions import admin_required
from apps.catalog.models import ActivityClass
from apps.enrollments import services as enrollment_services
from apps.enrollments.models import Enrollment
from apps.enrollments.services import EnrollmentError


@admin_required
def requests_queue(request):
    """Pending enrollment requests across all classes, oldest first."""
    pending = (
        Enrollment.objects.filter(status=Enrollment.Status.REQUESTED)
        .select_related("child", "activity_class__provider", "activity_class__term")
        .prefetch_related("child__guardians")
        .order_by("created_at")
    )
    classes = (
        ActivityClass.objects.filter(term__is_active=True)
        .with_counts()
        .select_related("provider", "term")
        .order_by("title")
    )
    return render(
        request,
        "dashboards/admintools/requests.html",
        {"pending": pending, "classes": classes},
    )


@admin_required
@require_POST
def request_approve(request, enrollment_id):
    enrollment = get_object_or_404(Enrollment, pk=enrollment_id)
    try:
        enrollment = enrollment_services.approve_request(enrollment, request.user)
    except EnrollmentError as exc:
        messages.error(request, str(exc))
        return redirect("admintools_requests")
    if enrollment.status == Enrollment.Status.ENROLLED:
        messages.success(
            request,
            f"{enrollment.child.full_name} enrolled in {enrollment.activity_class.title}.",
        )
    else:
        messages.warning(
            request,
            f"{enrollment.activity_class.title} is full — "
            f"{enrollment.child.full_name} was added to the waiting list.",
        )
    return redirect("admintools_requests")


@admin_required
@require_POST
def request_reject(request, enrollment_id):
    enrollment = get_object_or_404(Enrollment, pk=enrollment_id)
    try:
        enrollment_services.reject_request(enrollment, request.user)
    except EnrollmentError as exc:
        messages.error(request, str(exc))
        return redirect("admintools_requests")
    messages.info(
        request,
        f"Request for {enrollment.child.full_name} rejected; the family has been notified.",
    )
    return redirect("admintools_requests")


@admin_required
def waitlist(request, class_id):
    cls = get_object_or_404(
        ActivityClass.objects.with_counts().select_related("provider", "term"),
        pk=class_id,
    )
    waitlisted = (
        cls.enrollments.filter(status=Enrollment.Status.WAITLISTED)
        .select_related("child")
        .prefetch_related("child__guardians")
        .order_by("waitlisted_at", "id")
    )
    offered = (
        cls.enrollments.filter(status=Enrollment.Status.OFFERED)
        .select_related("child")
        .order_by("offer_expires_at")
    )
    return render(
        request,
        "dashboards/admintools/waitlist.html",
        {
            "cls": cls,
            "waitlisted": waitlisted,
            "offered": offered,
            "seats_free": cls.places_free,
        },
    )


@admin_required
@require_POST
def waitlist_offer(request, enrollment_id):
    enrollment = get_object_or_404(
        Enrollment.objects.select_related("activity_class"), pk=enrollment_id
    )
    try:
        enrollment = enrollment_services.offer_seat(enrollment, request.user)
    except EnrollmentError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"Seat offered to {enrollment.child.full_name}'s family — they have "
            f"until {enrollment.offer_expires_at:%A %d %B %H:%M} to confirm.",
        )
    return redirect("admintools_waitlist", class_id=enrollment.activity_class_id)
