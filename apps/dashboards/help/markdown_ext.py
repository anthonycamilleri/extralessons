"""What a help page may say in shorthand, and what it turns into.

Two shorthands, so a guide reads like a document rather than like a template:

* `![The Requests page](requests.png)` — the screenshot that lives beside this
  guide. Production serves static files through WhiteNoise's manifest storage,
  where the real URL carries a content hash, so the name is looked up rather
  than guessed. A name that is not in the manifest is dropped rather than
  raised: an uncaptured screenshot should cost a picture, never the page.
* `[Waiting lists](waiting-lists)` — another guide for the same audience, by
  its slug.

Anything already absolute is left alone.
"""
import logging
from xml.etree import ElementTree

from django.contrib.staticfiles.storage import staticfiles_storage
from markdown.extensions import Extension
from markdown.treeprocessors import Treeprocessor

logger = logging.getLogger(__name__)


def _is_absolute(reference: str) -> bool:
    return not reference or "//" in reference or reference.startswith(("/", "#", "mailto:"))


class _ResolveReferences(Treeprocessor):
    def __init__(self, md, audience: str, topic_url):
        super().__init__(md)
        self.audience = audience
        self.topic_url = topic_url

    def run(self, root: ElementTree.Element) -> None:
        # Collected before anything is swapped: the tree is edited in the loop.
        images = [(parent, img) for parent in root.iter() for img in parent.findall("img")]
        for parent, image in images:
            source = image.get("src", "")
            if _is_absolute(source):
                continue
            try:
                image.set("src", staticfiles_storage.url(f"img/help/{self.audience}/{source}"))
            except ValueError:
                logger.warning("Help screenshot %r is missing for %r", source, self.audience)
                self._replace_with_alt(parent, image)
                continue
            self._as_figure(parent, image)

        for link in root.iter("a"):
            href = link.get("href", "")
            if _is_absolute(href):
                continue
            slug, _, fragment = href.partition("#")
            url = self.topic_url(slug)
            if url is None:
                logger.warning("Help page links to unknown topic %r", slug)
                continue
            link.set("href", f"{url}#{fragment}" if fragment else url)

    @staticmethod
    def _as_figure(parent: ElementTree.Element, image: ElementTree.Element) -> None:
        """A paragraph holding nothing but a screenshot is really a figure.

        Markdown always wraps a lone image in a <p>; as a <figure> it can be
        laid out wider than the text column, and its alt text can be shown as
        the caption it already reads like.
        """
        alone = parent.tag == "p" and len(parent) == 1 and not (parent.text or "").strip()
        if not alone or (image.tail or "").strip():
            return
        parent.tag = "figure"
        caption = ElementTree.SubElement(parent, "figcaption")
        caption.text = image.get("alt") or ""

    @staticmethod
    def _replace_with_alt(parent: ElementTree.Element, image: ElementTree.Element) -> None:
        """Leave the caption behind so the page still reads as prose."""
        note = ElementTree.Element("em")
        note.text = image.get("alt") or ""
        note.tail = image.tail
        parent.insert(list(parent).index(image), note)
        parent.remove(image)


class HelpReferences(Extension):
    """`topic_url(slug)` returns the URL for another guide, or None if unknown."""

    def __init__(self, audience: str, topic_url):
        super().__init__()
        self.audience = audience
        self.topic_url = topic_url

    def extendMarkdown(self, md):
        # Priority 15: after "inline" (20), which is what turns ![]() into an <img>.
        md.treeprocessors.register(
            _ResolveReferences(md, self.audience, self.topic_url), "help_references", 15
        )
