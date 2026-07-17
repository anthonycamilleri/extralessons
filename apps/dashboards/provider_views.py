from django import forms
from django.contrib import messages
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from apps.accounts.permissions import provider_required
from apps.catalog.models import ActivityClass, ClassSession
from apps.enrollments.models import Attendance, Enrollment
from apps.notifications import services as notification_services
from apps.notifications.models import Broadcast


def _own_classes(user):
    return (
        ActivityClass.objects.filter(provider__members=user)
        .select_related("provider", "term")
        .order_by("-term__start_date", "weekday", "start_time")
    )


@provider_required
def home(request):
    classes = _own_classes(request.user).with_counts()
    return render(request, "dashboards/provider/home.html", {"classes": classes})


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
    sessions = cls.sessions.all()
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
            "today": today,
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
        messages.success(request, f"Attendance saved for {session.date}.")
        return redirect("provider_class", class_id=cls.pk)

    existing = {
        a.child_id: a.present for a in Attendance.objects.filter(session=session)
    }
    rows = [
        {
            "enrollment": enrollment,
            "present": existing.get(enrollment.child_id),
            "marked": enrollment.child_id in existing,
        }
        for enrollment in roster
    ]
    return render(
        request,
        "dashboards/provider/attendance.html",
        {"cls": cls, "session": session, "rows": rows, "taken": bool(existing)},
    )


class ProviderBroadcastForm(forms.Form):
    classes = forms.ModelMultipleChoiceField(
        queryset=ActivityClass.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label="Send to families of",
    )
    subject = forms.CharField(max_length=200)
    body = forms.CharField(widget=forms.Textarea, label="Message")

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
            body=form.cleaned_data["body"],
            classes=form.cleaned_data["classes"],
        )
        messages.success(
            request,
            f"Message queued for {notification_services.family_count_phrase(count)}.",
        )
        return redirect("provider_home")
    return render(request, "dashboards/provider/broadcast.html", {"form": form})
