"""What help exists, for whom, and how a page is rendered.

One registry, keyed by audience, so the parent and provider guides that come
later are a new entry and a new folder of Markdown — not a second mechanism.
"""
from dataclasses import dataclass
from pathlib import Path

import markdown
from django.http import Http404
from django.urls import reverse
from django.utils.safestring import SafeString, mark_safe

from .markdown_ext import HelpReferences

CONTENT_ROOT = Path(__file__).resolve().parent / "content"


@dataclass(frozen=True)
class Topic:
    slug: str
    title: str
    summary: str

    @property
    def filename(self) -> str:
        return f"{self.slug}.md"


@dataclass(frozen=True)
class Audience:
    """One set of guides: who they are for, and where their files live.

    ``key`` doubles as the content folder (``content/<key>/``) and as the
    screenshot folder (``static/img/help/<key>/``).
    """

    key: str
    title: str
    intro: str
    topics: tuple[Topic, ...]
    url_name: str = "admin:help_topic"

    def path(self, topic: Topic) -> Path:
        return CONTENT_ROOT / self.key / topic.filename

    def url(self, slug: str) -> str | None:
        """Where another guide of this audience lives, or None if there is none."""
        if not any(topic.slug == slug for topic in self.topics):
            return None
        return reverse(self.url_name, args=[slug])


ADMIN = Audience(
    key="admin",
    title="Help for the school office",
    intro=(
        "Short guides to the jobs the office does most: reviewing who gets a "
        "place, keeping a class and its dates right, working a waiting list, "
        "and writing to families."
    ),
    topics=(
        Topic(
            slug="approving-requests",
            title="Approving requests",
            summary="Approve once you know the class will run — the family is told straight away.",
        ),
        Topic(
            slug="class-details-and-dates",
            title="Changing a class and its dates",
            summary="Edit the details safely, and change the lesson dates without disturbing the term.",
        ),
        Topic(
            slug="waiting-lists",
            title="Waiting lists and offers",
            summary="How children join a waiting list, and how you hand a freed seat to one of them.",
        ),
        Topic(
            slug="announcements",
            title="Sending an announcement",
            summary="Write to the families of one class or all of them, and check it arrived.",
        ),
    ),
)

AUDIENCES = {audience.key: audience for audience in (ADMIN,)}


def get_audience(key: str) -> Audience:
    try:
        return AUDIENCES[key]
    except KeyError:
        raise Http404(f"No help is written for {key!r}.") from None


def get_topic(audience: Audience, slug: str) -> Topic:
    for topic in audience.topics:
        if topic.slug == slug:
            return topic
    raise Http404(f"No help page called {slug!r}.")


def render_topic(audience: Audience, topic: Topic) -> SafeString:
    """The page as HTML.

    Read and rendered per request: these are four small files on a route only
    the office can reach, and a cache would only add a way for edits to go
    unnoticed.
    """
    source = audience.path(topic).read_text(encoding="utf-8")
    return mark_safe(  # repo-authored, like the admin-written terms at /terms/
        markdown.markdown(
            source,
            extensions=[
                "extra",
                "sane_lists",
                "toc",
                HelpReferences(audience.key, audience.url),
            ],
        )
    )


def siblings(audience: Audience, topic: Topic) -> tuple[Topic, ...]:
    return tuple(other for other in audience.topics if other.slug != topic.slug)
