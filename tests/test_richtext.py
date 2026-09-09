"""The rich-text pipeline for announcements: what survives cleaning, how the
HTML reads as plain text, and how it is dropped into the email."""
import pytest
from django.test import override_settings

from apps.notifications import richtext
from apps.notifications.richtext import (
    BODY_TOKEN,
    clean_html,
    compose_email_html,
    html_to_text,
    is_blank,
)


class TestCleanHtml:
    def test_keeps_the_email_safe_allowlist(self):
        html = (
            '<h2>Trip</h2><p>Bring <strong>boots</strong>, <em>a hat</em> and <u>gloves</u>.</p>'
            "<ol><li>one</li></ol><ul><li>two</li></ul>"
            '<blockquote>quoted</blockquote>'
            '<p><a href="https://example.org/kit">the list</a></p>'
            '<p><img src="https://example.org/a.jpg" alt="Boots" width="600" height="400"></p>'
        )
        cleaned = clean_html(html)
        for fragment in (
            "<h2>Trip</h2>", "<strong>boots</strong>", "<em>a hat</em>", "<u>gloves</u>",
            "<ol><li>one</li></ol>", "<ul><li>two</li></ul>", "<blockquote>quoted</blockquote>",
            'href="https://example.org/kit"', 'src="https://example.org/a.jpg"', 'alt="Boots"',
            'width="600"', 'height="400"',
        ):
            assert fragment in cleaned

    def test_strips_scripts_handlers_styles_and_classes(self):
        html = (
            '<p class="ql-align-center" style="color:red" onclick="steal()">Hi <span>there</span></p>'
            "<script>alert(1)</script><style>p{display:none}</style>"
            '<iframe src="https://evil.example"></iframe>'
            "<!-- a comment --><table><tr><td>cell</td></tr></table>"
        )
        cleaned = clean_html(html)
        assert cleaned.startswith("<p>Hi there</p>")
        for forbidden in ("script", "alert", "style", "class", "onclick", "iframe", "<table", "comment"):
            assert forbidden not in cleaned
        assert "cell" in cleaned  # unknown tags lose their tags, not their words

    def test_dangerous_urls_are_removed(self):
        html = (
            '<p><a href="javascript:alert(1)">bad link</a></p>'
            '<p><img src="data:image/png;base64,AAAA" alt="pasted"></p>'
            '<p><img src="javascript:alert(1)"></p>'
        )
        cleaned = clean_html(html)
        assert "javascript" not in cleaned
        assert "data:" not in cleaned
        assert "<img" not in cleaned  # no broken-image icons left behind
        assert "<a" not in cleaned and "bad link" in cleaned  # the words stay, the link goes

    def test_links_get_rel_and_relative_urls_become_absolute(self):
        with override_settings(SITE_URL="https://www.example.org"):
            cleaned = clean_html('<p><a href="/me/">My family</a></p>')
        assert 'href="https://www.example.org/me/"' in cleaned
        assert 'rel="noopener noreferrer"' in cleaned

    def test_relative_image_is_dropped(self):
        # An inbox cannot resolve it; the editor always inserts absolute URLs.
        assert "<img" not in clean_html('<p><img src="/media/x.jpg"></p>')

    def test_non_numeric_dimensions_are_dropped(self):
        cleaned = clean_html('<img src="https://x.org/a.jpg" width="100%" height="12">')
        assert "width" not in cleaned
        assert 'height="12"' in cleaned

    def test_other_heading_sizes_are_demoted_not_unwrapped(self):
        cleaned = clean_html("<h1>Big</h1><h4>Small</h4><h6>Tiny</h6><p>text</p>")
        assert cleaned == "<h2>Big</h2><h3>Small</h3><h3>Tiny</h3><p>text</p>"

    def test_quill_blank_lines_are_trimmed_and_collapsed(self):
        html = "<p><br></p><p>one</p><p><br></p><p><br></p><p><br></p><p>two</p><p><br></p><p><br></p>"
        assert clean_html(html) == "<p>one</p><p><br></p><p>two</p>"

    def test_quill_nbsp_between_words_is_a_space(self):
        assert clean_html("<p>Bring&nbsp;<strong>boots</strong></p>") == "<p>Bring <strong>boots</strong></p>"

    def test_idempotent(self):
        html = '<h1>T</h1><p class="x">a&nbsp;b <a href="/x">l</a></p><p><br></p>'
        once = clean_html(html)
        assert clean_html(once) == once

    def test_empty_input(self):
        assert clean_html("") == ""
        assert clean_html(None) == ""


class TestIsBlank:
    @pytest.mark.parametrize("html", ["", "<p><br></p>", "<p>&nbsp;</p>", "<p></p><p><br></p>"])
    def test_blank(self, html):
        assert is_blank(html)

    @pytest.mark.parametrize(
        "html", ["<p>x</p>", '<p><img src="https://x.org/y.jpg"></p>', "<ul><li>a</li></ul>"]
    )
    def test_not_blank(self, html):
        assert not is_blank(html)


class TestHtmlToText:
    def test_paragraphs_headings_and_breaks(self):
        html = "<h2>Trip</h2><p>Bring <strong>boots</strong>.<br>And a hat.</p><p>Second.</p>"
        assert html_to_text(html) == "Trip\n\nBring boots.\nAnd a hat.\n\nSecond."

    def test_lists(self):
        html = "<p>Kit:</p><ul><li>boots</li><li>hat</li></ul><ol><li>first</li><li>second</li></ol>"
        assert html_to_text(html) == "Kit:\n\n- boots\n- hat\n\n1. first\n2. second"

    def test_links_are_spelled_out_unless_redundant(self):
        html = (
            '<p>See <a href="https://x.org/kit">the list</a> or '
            '<a href="https://x.org">https://x.org</a> or '
            '<a href="mailto:office@x.org">the office</a>.</p>'
        )
        assert html_to_text(html) == "See the list (https://x.org/kit) or https://x.org or the office."

    def test_images_become_their_alt_text_or_nothing(self):
        assert html_to_text('<p><img src="https://x.org/a.jpg" alt="Boots"></p>') == "[Boots]"
        assert html_to_text('<p>Look:</p><p><img src="https://x.org/a.jpg"></p><p>end</p>') == "Look:\n\nend"

    def test_entities_and_whitespace(self):
        assert html_to_text("<p>Fish &amp; chips&nbsp;&mdash; 5&euro;</p>\n\n<p>   spaced   out </p>") == (
            "Fish & chips — 5€\n\nspaced out"
        )

    def test_blank_paragraph_is_a_blank_line_at_most(self):
        assert html_to_text("<p>a</p><p><br></p><p>b</p>") == "a\n\nb"


class TestComposeEmailHtml:
    TEMPLATE_TEXT = (
        "Hi Paula,\n\n" + BODY_TOKEN + "\n\nWarm regards,\nThe Office\n\n—\n"
        "You're receiving this because you have an account: https://www.example.org\n"
        "Questions? Write to office@example.org."
    )

    def test_body_replaces_the_token_and_the_text_is_escaped_and_linked(self):
        html = compose_email_html(self.TEMPLATE_TEXT, "<p>Bring <strong>boots</strong>.</p>")
        assert html.lstrip().startswith("<!DOCTYPE html>")
        assert BODY_TOKEN not in html
        assert "<p style=\"margin:0 0 1em;\">Hi Paula,</p>" in html
        assert "Bring <strong>boots</strong>." in html
        assert "Warm regards,<br>The Office" in html
        assert "You&#x27;re receiving this" in html  # escaped, not raw
        assert '<a style="color:#0072bb;" href="https://www.example.org">https://www.example.org</a>' in html
        assert 'href="mailto:office@example.org"' in html

    def test_images_and_blocks_get_inline_styles(self):
        body = '<h2>Kit</h2><ul><li>a</li></ul><p><img src="https://x.org/a.jpg" alt="Boots"></p>'
        html = compose_email_html(self.TEMPLATE_TEXT, body)
        assert '<img style="max-width:100%;height:auto;display:block;margin:12px 0;" src="https://x.org/a.jpg"' in html
        assert '<h2 style="margin:1.2em 0 0.5em;font-size:22px;line-height:1.3;">Kit</h2>' in html
        assert '<ul style="margin:0 0 1em;padding-left:1.4em;"><li style="margin:0 0 0.3em;">a</li></ul>' in html

    def test_template_syntax_inside_the_body_is_inert(self):
        html = compose_email_html(self.TEMPLATE_TEXT, "<p>{{ site_url }} and {% if x %}</p>")
        assert "{{ site_url }} and {% if x %}" in html

    def test_token_written_mid_line_still_works(self):
        html = compose_email_html(f"Note: {BODY_TOKEN} (end)", "<strong>bold</strong>")
        assert "Note: <strong>bold</strong> (end)" in html
        assert BODY_TOKEN not in html

    def test_stored_html_stays_style_free(self):
        # Presentation is added on the way out only.
        assert "style=" not in richtext.clean_html('<p style="color:red">x</p>')
