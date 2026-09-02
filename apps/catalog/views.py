from django.http import Http404
from django.shortcuts import get_object_or_404, render

from apps.accounts.models import Child, SiteConfig, User

from .models import ActivityClass


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
    selected_day = _int_or_none(request.GET.get("day"))
    if selected_day is not None and selected_day not in weekdays_present:
        selected_day = None
    selected_age = _int_or_none(request.GET.get("age"))
    if selected_age is not None and selected_age not in ages:
        selected_age = None
    selected_location = request.GET.get("location", "").strip()
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

    return render(
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
            "filters_active": (
                selected_day is not None or selected_age is not None or bool(selected_location)
            ),
        },
    )


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
