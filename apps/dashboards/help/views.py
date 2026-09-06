"""The help pages themselves.

Both views take their audience and their base template as arguments, so the
public `/help/` pages for parents and providers, when they come, are two more
url() lines rather than two more views.
"""
from django.shortcuts import render

from . import registry


def help_index(request, *, audience="admin", base_template="admin/base_site.html"):
    audience = registry.get_audience(audience)
    return render(
        request,
        "admin/help/index.html",
        {
            "audience": audience,
            "base_template": base_template,
            "title": audience.title,
        },
    )


def help_topic(request, slug, *, audience="admin", base_template="admin/base_site.html"):
    audience = registry.get_audience(audience)
    topic = registry.get_topic(audience, slug)
    return render(
        request,
        "admin/help/topic.html",
        {
            "audience": audience,
            "topic": topic,
            "body": registry.render_topic(audience, topic),
            "siblings": registry.siblings(audience, topic),
            "base_template": base_template,
            "title": topic.title,
        },
    )
