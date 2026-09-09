"""Rich-text announcements: the HTML that may be written, and how it is sent.

An announcement body is HTML written in a toolbar editor by a school admin or a
provider, then emailed to families. Two things keep that safe and readable:

* ``clean_html`` reduces whatever arrives to a small allowlist of tags that
  every email client renders — paragraphs, emphasis, two heading sizes, lists,
  links and hosted images. Scripts, styles, classes, event handlers, ``data:``
  images and unknown tags are removed, not escaped. Providers are a lower
  trust tier than admins, so this runs on every composer and again in the
  service, and the stored ``Broadcast.body_html`` is always the cleaned form.

* ``compose_email_html`` turns the rendered plain-text notification template
  into the HTML part of the email and drops the body in where ``{{ body }}``
  stood, applying the inline styles email clients honour (many strip
  ``<style>`` blocks; none strip a ``style`` attribute). The stored HTML stays
  style-free so the admin's history page and any future rendering are not
  polluted with email presentation.

``html_to_text`` is the plain-text reading of the same HTML — what WhatsApp
receives, what ``Broadcast.body`` records, and the ``text/plain`` half of the
email for clients that ask for it.
"""
import re
from html import escape
from html.parser import HTMLParser

import nh3
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import linebreaks, urlize
from django.utils.safestring import mark_safe

ALLOWED_TAGS = {
    "p", "br", "strong", "b", "em", "i", "u", "s", "a",
    "ul", "ol", "li", "h2", "h3", "blockquote", "img",
}
ALLOWED_ATTRIBUTES = {"a": {"href"}, "img": {"src", "alt", "width", "height"}}
URL_SCHEMES = {"http", "https", "mailto"}

# Stands in for {{ body }} while the plain-text template renders, then gives
# way to the HTML. Alphanumeric only, so escaping and urlize leave it alone.
BODY_TOKEN = "RICHTEXTBODYPLACEHOLDER7f3a9c"

# Presentation the email needs and the sanitiser (rightly) will not store.
EMAIL_STYLES = {
    "p": "margin:0 0 1em;",
    "h2": "margin:1.2em 0 0.5em;font-size:22px;line-height:1.3;",
    "h3": "margin:1.2em 0 0.5em;font-size:18px;line-height:1.3;",
    "ul": "margin:0 0 1em;padding-left:1.4em;",
    "ol": "margin:0 0 1em;padding-left:1.4em;",
    "li": "margin:0 0 0.3em;",
    "blockquote": "margin:0 0 1em;padding:0 0 0 1em;border-left:3px solid #d0d5dd;color:#475467;",
    "img": "max-width:100%;height:auto;display:block;margin:12px 0;",
    "a": "color:#0072bb;",
}

_EMPTY_PARAGRAPH = re.compile(r"<p>(?:\s|&nbsp;|<br\s*/?>)*</p>", re.IGNORECASE)
_LEADING_EMPTY = re.compile(r"^(?:\s*<p>(?:\s|&nbsp;|<br\s*/?>)*</p>)+", re.IGNORECASE)
_TRAILING_EMPTY = re.compile(r"(?:<p>(?:\s|&nbsp;|<br\s*/?>)*</p>\s*)+$", re.IGNORECASE)
_IMG_WITHOUT_SRC = re.compile(r"<img(?![^>]*\bsrc=)[^>]*>", re.IGNORECASE)
_ANCHOR_WITHOUT_HREF = re.compile(
    r"<a(?![^>]*\bhref=)[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL
)
_STYLED_TAG = re.compile(r"<(p|h2|h3|ul|ol|li|blockquote|img|a)\b")


def _attribute_filter(tag, attr, value):
    if tag == "img" and attr == "src" and not value.startswith(("http://", "https://")):
        # nh3 has already rejected data: and javascript:; a relative image
        # would not load from an inbox either.
        return None
    if tag == "img" and attr in ("width", "height") and not value.isdigit():
        return None
    return value


def clean_html(html):
    """Reduce HTML to the email-safe allowlist. Idempotent.

    Headings the toolbar does not offer are demoted rather than unwrapped
    (nh3 keeps the text of a dropped tag, which would run a pasted title into
    the next paragraph). Quill's ``getSemanticHTML`` writes ``&nbsp;`` for
    runs of spaces; between words that is just a space.
    """
    if not html:
        return ""
    html = re.sub(r"<(/?)h1\b", r"<\1h2", html, flags=re.IGNORECASE)
    html = re.sub(r"<(/?)h[4-6]\b", r"<\1h3", html, flags=re.IGNORECASE)
    html = re.sub(r"(?<=\S)(?:&nbsp;|\xa0)(?=\S)", " ", html)
    cleaned = nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        attribute_filter=_attribute_filter,
        url_schemes=URL_SCHEMES,
        # A pasted internal link ("/me/") must work from an inbox.
        url_relative=("rewrite_with_base", settings.SITE_URL.rstrip("/") + "/"),
        link_rel="noopener noreferrer",
        strip_comments=True,
    )
    cleaned = _IMG_WITHOUT_SRC.sub("", cleaned)
    cleaned = _ANCHOR_WITHOUT_HREF.sub(r"\1", cleaned)
    cleaned = _LEADING_EMPTY.sub("", cleaned)
    cleaned = _TRAILING_EMPTY.sub("", cleaned)
    # Two blank lines in a row read as one in an email; more is a mistake.
    cleaned = re.sub(
        rf"(?:{_EMPTY_PARAGRAPH.pattern}\s*){{2,}}", "<p><br></p>", cleaned, flags=re.IGNORECASE
    )
    return cleaned.strip()


def is_blank(html):
    """True when the message has neither words nor a picture."""
    return html_to_text(html) == "" and "<img" not in (html or "").lower()


class _TextExtractor(HTMLParser):
    """Plain-text reading of clean HTML: paragraphs, bullets, links spelled out."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.lists = []  # stack of [tag, counter]
        self.link = None  # [href, text-so-far] while inside <a>

    def _newline(self, count=1):
        text = "".join(self.parts)
        trailing = len(text) - len(text.rstrip("\n"))
        if trailing < count:
            self.parts.append("\n" * (count - trailing))

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ("p", "h2", "h3", "blockquote"):
            self._newline(2)
        elif tag in ("ul", "ol"):
            self.lists.append([tag, 0])
            self._newline(2 if len(self.lists) == 1 else 1)
        elif tag == "li":
            self._newline(1)
            indent = "  " * (len(self.lists) - 1)
            if self.lists and self.lists[-1][0] == "ol":
                self.lists[-1][1] += 1
                self.parts.append(f"{indent}{self.lists[-1][1]}. ")
            else:
                self.parts.append(f"{indent}- ")
        elif tag == "br":
            self.parts.append("\n")
        elif tag == "a":
            self.link = [attrs.get("href", ""), ""]
        elif tag == "img":
            alt = (attrs.get("alt") or "").strip()
            if alt:
                self.parts.append(f"[{alt}]")

    def handle_endtag(self, tag):
        if tag in ("p", "h2", "h3", "blockquote"):
            self._newline(2)
        elif tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            self._newline(2 if not self.lists else 1)
        elif tag == "li":
            self._newline(1)
        elif tag == "a" and self.link is not None:
            href, text = self.link
            self.link = None
            text = text.strip()
            self.parts.append(text)
            if href and not href.startswith("mailto:") and text and text != href:
                self.parts.append(f" ({href})")
            elif href and not text:
                self.parts.append(href)

    def handle_data(self, data):
        data = data.replace("\xa0", " ")
        data = re.sub(r"[ \t\r\n]+", " ", data)
        if self.link is not None:
            self.link[1] += data
            return
        so_far = "".join(self.parts)
        if not so_far or so_far.endswith("\n"):
            data = data.lstrip()
        if data:
            self.parts.append(data)

    def text(self):
        text = "".join(self.parts)
        text = "\n".join(line.rstrip() for line in text.split("\n"))
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def html_to_text(html):
    """The plain-text version of a rich-text body."""
    if not html:
        return ""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return parser.text()


def text_to_html(text):
    """Escape a rendered plain-text email and give it paragraphs and links."""
    return linebreaks(urlize(escape(text), autoescape=False), autoescape=False)


def inline_styles(html):
    """Add the email presentation to clean HTML.

    A regex is enough: nh3 serialises attribute values with ``<`` escaped, so
    an opening tag can only ever be a real opening tag.
    """
    return _STYLED_TAG.sub(lambda m: f'<{m.group(1)} style="{EMAIL_STYLES[m.group(1)]}"', html)


def compose_email_html(rendered_text, body_html):
    """The HTML part of an announcement email.

    ``rendered_text`` is the plain-text notification template rendered with
    BODY_TOKEN standing where ``{{ body }}`` was; ``body_html`` is the cleaned
    announcement. The body never passes through the template engine, so an
    author's ``{{`` is inert and nothing is escaped twice.
    """
    html = text_to_html(rendered_text)
    paragraph = f"<p>{BODY_TOKEN}</p>"
    if paragraph in html:
        html = html.replace(paragraph, body_html)
    else:
        # The template author put {{ body }} mid-line; drop the HTML in there.
        html = html.replace(BODY_TOKEN, body_html)
    content = inline_styles(html)
    return render_to_string("notifications/email/base.html", {"content": mark_safe(content)})
