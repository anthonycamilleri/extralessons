from urllib.parse import urlencode

from django.contrib import messages
from django.core.cache import cache
from django.http import Http404, QueryDict
from django.shortcuts import get_object_or_404, redirect, render

from apps.accounts.models import Child, SiteConfig, User
from apps.notifications import services as notification_services

from .forms import ContactForm
from .models import ActivityClass

FILTER_KEYS = ("day", "location", "age")

# Parents come back to the catalogue week after week, so their last selection
# is kept in a plain cookie: no server-side session row per anonymous visitor,
# and it outlives the browser session. The value is the filters' own query
# string; it is re-validated against the live catalogue on every request.
FILTER_COOKIE = "catalogue_filters"
FILTER_COOKIE_MAX_AGE = 365 * 24 * 60 * 60


def _int_or_none(raw):
    """Parse a filter parameter, treating anything unparseable as "no filter".

    A hand-edited, stale or bookmarked URL should degrade to the full
    catalogue rather than raise or render an empty page.
    """
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def catalogue(request):
    if "clear" in request.GET:
        response = redirect("catalogue")
        response.delete_cookie(FILTER_COOKIE, samesite="Lax")
        return response

    # Any filter key in the URL — even empty, as the form submits "Any day" —
    # is an explicit choice. A bare URL (the nav link, a bookmark) picks up the
    # remembered one instead.
    filters_remembered = not any(key in request.GET for key in FILTER_KEYS)
    params = (
        QueryDict(request.COOKIES.get(FILTER_COOKIE, "")) if filters_remembered else request.GET
    )

    base = ActivityClass.objects.published().select_related("provider", "term")

    # One query gives us every filter option and the unfiltered total. The
    # options come from the same published population that is being filtered,
    # so a dropdown can never offer a value with no results, and a draft
    # class can never leak its location onto the public page.
    facets = list(base.values_list("weekday", "location", "age_min", "age_max"))
    total_count = len(facets)
    locations = sorted({location for _, location, _, _ in facets if location})
    weekdays_present = {weekday for weekday, _, _, _ in facets}
    day_choices = [
        (value, label) for value, label in ActivityClass.WEEKDAYS if value in weekdays_present
    ]
    if facets:
        ages = range(
            min(age_min for _, _, age_min, _ in facets),
            max(age_max for _, _, _, age_max in facets) + 1,
        )
    else:
        ages = range(0)

    # Monday is weekday 0, so every check below has to be `is not None`.
    selected_day = _int_or_none(params.get("day"))
    if selected_day is not None and selected_day not in weekdays_present:
        selected_day = None
    selected_age = _int_or_none(params.get("age"))
    if selected_age is not None and selected_age not in ages:
        selected_age = None
    selected_location = params.get("location", "").strip()
    if selected_location not in locations:
        selected_location = ""

    classes = base.with_counts().with_next_session()
    if selected_day is not None:
        classes = classes.filter(weekday=selected_day)
    if selected_location:
        classes = classes.filter(location=selected_location)
    if selected_age is not None:
        classes = classes.filter(age_min__lte=selected_age, age_max__gte=selected_age)
    classes = classes.order_by("weekday", "start_time", "title")

    selected = {
        "day": selected_day,
        "location": selected_location,
        "age": selected_age,
    }
    filters_active = any(value not in (None, "") for value in selected.values())

    response = render(
        request,
        "catalog/catalogue.html",
        {
            "classes": classes,
            "total_count": total_count,
            "day_choices": day_choices,
            "locations": locations,
            "ages": ages,
            "selected_day": selected_day,
            "selected_location": selected_location,
            "selected_age": selected_age,
            "filters_active": filters_active,
            "filters_remembered": filters_remembered and filters_active,
        },
    )

    # Only the validated selection is remembered, so a stale or hand-edited
    # value can never keep coming back; an explicit "show everything" forgets.
    if filters_active:
        response.set_cookie(
            FILTER_COOKIE,
            urlencode({key: value for key, value in selected.items() if value not in (None, "")}),
            max_age=FILTER_COOKIE_MAX_AGE,
            httponly=True,
            samesite="Lax",
            secure=request.is_secure(),
        )
    elif FILTER_COOKIE in request.COOKIES:
        response.delete_cookie(FILTER_COOKIE, samesite="Lax")
    return response


def class_detail(request, term_id, slug):
    cls = get_object_or_404(
        ActivityClass.objects.published()
        .with_counts()
        .with_next_session()
        .select_related("provider", "term", "term__school_year"),
        term_id=term_id,
        slug=slug,
    )
    children = []
    if request.user.is_authenticated and request.user.role == User.Role.PARENT:
        children = Child.objects.for_guardian(request.user)
    return render(
        request,
        "catalog/class_detail.html",
        {"cls": cls, "children": children, "holidays": cls.skipped_holidays()},
    )


def terms(request):
    """The PTA's terms and conditions, written in Markdown in the admin."""
    config = SiteConfig.get()
    if not config.has_terms:
        raise Http404("No terms and conditions have been published.")
    return render(request, "catalog/terms.html", {"terms_html": config.terms_html})


# Best-effort flood protection: enough to stop a bot hammering the form, not a
# security control (the cache is per process, and an IP is not an identity).
# The honeypot in ContactForm catches the ordinary drive-by spammer.
CONTACT_MAX_PER_HOUR = 5
CONTACT_WINDOW_SECONDS = 60 * 60


def _client_ip(request):
    """Best guess at the caller's address, trusting the platform's proxy.

    Render terminates TLS and forwards the client address in
    X-Forwarded-For; the leftmost entry is the one it appended.
    """
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _contact_flooding(request):
    """True when this caller has already sent its hourly allowance."""
    key = f"contact_form:{_client_ip(request)}"
    sent = cache.get(key, 0)
    if sent >= CONTACT_MAX_PER_HOUR:
        return True
    # set() rather than incr(): incr needs the key to exist, and a first
    # message and a fifth should cost the same one round trip.
    cache.set(key, sent + 1, CONTACT_WINDOW_SECONDS)
    return False


def contact(request):
    """Public contact form; messages go to SiteConfig.contact_email.

    A public page rather than a mailto: link, so a parent without a mail
    client set up can still reach the office, and so the message lands in the
    outbox with a delivery record instead of in someone's drafts.
    """
    config = SiteConfig.get()
    if not config.contact_email:
        raise Http404("No contact address has been configured.")

    initial = {}
    if request.user.is_authenticated:
        initial = {
            "name": request.user.get_full_name() or "",
            "email": request.user.email,
        }
    form = ContactForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        if form.looks_automated or _contact_flooding(request):
            # Same thank-you either way: a bot gets no signal, and a parent
            # who double-clicked is not told off for a message we did send.
            messages.success(request, "Thanks — your message is on its way.")
            return redirect("contact")
        notification_services.queue_contact_message(
            name=form.cleaned_data["name"],
            email=form.cleaned_data["email"],
            subject=form.cleaned_data["subject"],
            message=form.cleaned_data["message"],
            submitted_by=request.user.email if request.user.is_authenticated else "",
        )
        messages.success(
            request,
            "Thanks — your message is on its way. We'll reply to "
            f"{form.cleaned_data['email']} as soon as we can.",
        )
        return redirect("contact")
    return render(request, "catalog/contact.html", {"form": form, "config": config})
