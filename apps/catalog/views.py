from django.shortcuts import get_object_or_404, render

from apps.accounts.models import Child, User

from .models import ActivityClass


def catalogue(request):
    classes = (
        ActivityClass.objects.published()
        .with_counts()
        .select_related("provider", "term")
        .order_by("weekday", "start_time", "title")
    )
    return render(request, "catalog/catalogue.html", {"classes": classes})


def class_detail(request, term_id, slug):
    cls = get_object_or_404(
        ActivityClass.objects.published().with_counts().select_related("provider", "term"),
        term_id=term_id,
        slug=slug,
    )
    children = []
    if request.user.is_authenticated and request.user.role == User.Role.PARENT:
        children = Child.objects.for_guardian(request.user)
    return render(
        request,
        "catalog/class_detail.html",
        {"cls": cls, "children": children},
    )
