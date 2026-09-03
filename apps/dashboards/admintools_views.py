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


# A super admin who also has classes of their own can narrow the dashboard to
# just those. The choice is remembered in a cookie, like the catalogue filters.
SCOPE_COOKIE = "admin_scope"
SCOPE_COOKIE_MAX_AGE = 365 * 24 * 60 * 60
SCOPES = ("mine", "all")


def _my_classes(user):
    """The classes this admin acts on: every class for a super admin, only
    the assigned ones for anyone else.

    Every admin-tools action goes through here, so an admin can neither see
    nor approve, reject, offer or message anything outside their classes — a
    request for another class 404s like any other row outside one's scope.
    """
    return ActivityClass.objects.managed_by(user)


def _my_enrollments(user):
    return Enrollment.objects.filter(activity_class__in=_my_classes(user))


def _has_scope_toggle(user):
    """Only a super admin with classes of their own has two views to choose from."""
    return user.is_super_admin and user.managed_classes.exists()


def _scope(request):
    """"mine" or "all" for a super admin with the toggle; None otherwise.

    An explicit ?scope= wins, then the remembered choice; a super admin
    starts on "all", because seeing everything is what makes them one.
    """
    if not _has_scope_toggle(request.user):
        return None
    chosen = request.GET.get("scope") or request.COOKIES.get(SCOPE_COOKIE)
    return chosen if chosen in SCOPES else "all"


def _visible_classes(request):
    """What the lists show: the actionable classes, narrowed to "mine" on request."""
    classes = _my_classes(request.user)
    if _scope(request) == "mine":
        classes = classes.filter(administrators=request.user)
    return classes


def _remember_scope(request, response):
    chosen = request.GET.get("scope")
    if chosen in SCOPES and _has_scope_toggle(request.user):
        response.set_cookie(
            SCOPE_COOKIE,
            chosen,
            max_age=SCOPE_COOKIE_MAX_AGE,
            httponly=True,
            samesite="Lax",
            secure=request.is_secure(),
        )
    return response


def _scope_context(request):
    """What the templates need to say whose desk this is."""
    user = request.user
    return {
        "scope": _scope(request),
        "is_super_admin": user.is_super_admin,
        "my_classes": user.managed_classes.filter(term__is_active=True).order_by("title"),
    }


@admin_required
def requests_queue(request):
    """Pending enrollment requests across this admin's classes, oldest first."""
    visible = _visible_classes(request)
    pending = (
        Enrollment.objects.filter(
            activity_class__in=visible, status=Enrollment.Status.REQUESTED
        )
        .select_related("child", "activity_class__provider", "activity_class__term")
        .prefetch_related("child__guardians")
        .order_by("created_at")
    )
    classes = list(
        visible.filter(term__is_active=True)
        .with_counts()
        .select_related("provider", "term")
        .prefetch_related("administrators")
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
    response = render(
        request,
        "dashboards/admintools/requests.html",
        {"pending": pending, "classes": classes, **_scope_context(request)},
    )
    return _remember_scope(request, response)


@admin_required
@require_POST
def request_approve(request, enrollment_id):
    enrollment = get_object_or_404(_my_enrollments(request.user), pk=enrollment_id)
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
    enrollment = get_object_or_404(_my_enrollments(request.user), pk=enrollment_id)
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
        _my_classes(request.user).with_counts().select_related("provider", "term"),
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
        queryset=ActivityClass.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Classes (when audience is 'Selected classes')",
    )
    subject = forms.CharField(max_length=200)
    body = forms.CharField(widget=forms.Textarea, label="Message")

    def __init__(self, *args, request, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = request.user
        self.classes = _visible_classes(request)
        self.fields["classes"].queryset = (
            self.classes.filter(term__is_active=True).order_by("title")
        )
        # "All classes" means all the classes on this page: every published
        # class for a super admin viewing everything, only their own otherwise.
        self.narrowed = not (self.user.is_super_admin and _scope(request) != "mine")
        if self.narrowed:
            self.fields["scope"].choices = [
                (Broadcast.Scope.ALL_CLASSES, "All my classes"),
                (Broadcast.Scope.SELECTED_CLASSES, "Selected classes"),
            ]

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("scope") == Broadcast.Scope.SELECTED_CLASSES and not cleaned.get(
            "classes"
        ):
            self.add_error("classes", "Pick at least one class.")
        return cleaned

    def audience(self):
        """(scope, classes) to hand to create_broadcast.

        A narrowed "all classes" is stored as an explicit selection of the
        published classes on the page, so the Broadcast row records exactly
        who was addressed and the fan-out never reaches families outside the
        sender's remit.
        """
        scope = self.cleaned_data["scope"]
        classes = self.cleaned_data["classes"]
        if scope == Broadcast.Scope.ALL_CLASSES and self.narrowed:
            scope = Broadcast.Scope.SELECTED_CLASSES
            classes = self.classes.published()
        return scope, classes


@admin_required
def broadcast(request):
    form = AdminBroadcastForm(request.POST or None, request=request)
    if request.method == "POST" and form.is_valid():
        scope, classes = form.audience()
        _, count = notification_services.create_broadcast(
            sender=request.user,
            scope=scope,
            subject=form.cleaned_data["subject"],
            body=form.cleaned_data["body"],
            classes=classes,
        )
        messages.success(
            request,
            f"Announcement queued for {notification_services.family_count_phrase(count)}.",
        )
        return redirect("admintools_requests")
    response = render(
        request,
        "dashboards/admintools/broadcast.html",
        {"form": form, **_scope_context(request)},
    )
    return _remember_scope(request, response)


@admin_required
@require_POST
def waitlist_offer(request, enrollment_id):
    enrollment = get_object_or_404(
        _my_enrollments(request.user).select_related("activity_class"), pk=enrollment_id
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
