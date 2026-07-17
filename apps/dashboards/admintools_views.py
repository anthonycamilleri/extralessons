from django import forms
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.formats import date_format
from django.views.decorators.http import require_POST

from apps.accounts.permissions import admin_required
from apps.catalog.models import ActivityClass
from apps.enrollments import services as enrollment_services
from apps.enrollments.models import Enrollment
from apps.enrollments.services import EnrollmentError
from apps.notifications import services as notification_services
from apps.notifications.models import Broadcast


@admin_required
def requests_queue(request):
    """Pending enrollment requests across all classes, oldest first."""
    pending = (
        Enrollment.objects.filter(status=Enrollment.Status.REQUESTED)
        .select_related("child", "activity_class__provider", "activity_class__term")
        .prefetch_related("child__guardians")
        .order_by("created_at")
    )
    classes = list(
        ActivityClass.objects.filter(term__is_active=True)
        .with_counts()
        .select_related("provider", "term")
        .order_by("title")
    )
    # Availability per pending row from the already-annotated classes list —
    # avoids a COUNT query per pending request in the template.
    places_free = {cls.pk: cls.places_free for cls in classes}
    pending = list(pending)
    for enrollment in pending:
        enrollment.places_free = places_free.get(
            enrollment.activity_class_id,
            enrollment.activity_class.places_free_now(),
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
        cls.enrollments.waitlist_fifo()
        .select_related("child")
        .prefetch_related("child__guardians")
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


class AdminBroadcastForm(forms.Form):
    scope = forms.ChoiceField(
        choices=Broadcast.Scope.choices,
        initial=Broadcast.Scope.ALL_CLASSES,
        widget=forms.RadioSelect,
        label="Audience",
    )
    classes = forms.ModelMultipleChoiceField(
        queryset=ActivityClass.objects.filter(term__is_active=True).order_by("title"),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Classes (when audience is 'Selected classes')",
    )
    subject = forms.CharField(max_length=200)
    body = forms.CharField(widget=forms.Textarea, label="Message")

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("scope") == Broadcast.Scope.SELECTED_CLASSES and not cleaned.get(
            "classes"
        ):
            self.add_error("classes", "Pick at least one class.")
        return cleaned


@admin_required
def broadcast(request):
    form = AdminBroadcastForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        _, count = notification_services.create_broadcast(
            sender=request.user,
            scope=form.cleaned_data["scope"],
            subject=form.cleaned_data["subject"],
            body=form.cleaned_data["body"],
            classes=form.cleaned_data["classes"],
        )
        messages.success(
            request,
            f"Announcement queued for {notification_services.family_count_phrase(count)}.",
        )
        return redirect("admintools_requests")
    return render(request, "dashboards/admintools/broadcast.html", {"form": form})


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
        deadline = date_format(
            timezone.localtime(enrollment.offer_expires_at), "l j F, H:i"
        )
        messages.success(
            request,
            f"Seat offered to {enrollment.child.full_name}'s family — they have "
            f"until {deadline} to confirm.",
        )
    return redirect("admintools_waitlist", class_id=enrollment.activity_class_id)
