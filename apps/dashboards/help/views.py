"""The help pages themselves.

Both views take their audience and their templates as arguments, so the
provider guides are two url() lines in provider_urls.py (through the
provider_required wrappers in provider_views.py) and the admin guides two
lines in admin_site.py, over the same two functions.
"""
from django.shortcuts import render

from . import registry


def help_index(
    request,
    *,
    audience="admin",
    base_template="admin/base_site.html",
    template="admin/help/index.html",
):
    audience = registry.get_audience(audience)
    return render(
        request,
        template,
        {
            "audience": audience,
            "base_template": base_template,
            "title": audience.title,
        },
    )


def help_topic(
    request,
    slug,
    *,
    audience="admin",
    base_template="admin/base_site.html",
    template="admin/help/topic.html",
):
    audience = registry.get_audience(audience)
    topic = registry.get_topic(audience, slug)
    return render(
        request,
        template,
        {
            "audience": audience,
            "topic": topic,
            "body": registry.render_topic(audience, topic),
            "siblings": registry.siblings(audience, topic),
            "base_template": base_template,
            "title": topic.title,
        },
    )
