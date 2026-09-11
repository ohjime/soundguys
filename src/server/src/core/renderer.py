"""Markdown → safe HTML rendering for posts.

Pipeline (order matters):
  1. Extract shortcode lines ([[name attr="…"]]) and render their cotton
     component templates, leaving nonce'd placeholders in the text.
  2. Render markdown to HTML.
  3. Sanitize with an explicit nh3 allowlist — author markdown can never emit
     scripts, event handlers, or iframes.
  4. Send the links that leave the site to a new tab, so that following one
     never takes the reader out of the running app.
  5. Re-inject the rendered component HTML over the placeholders. Component
     templates are ours (trusted), and their attr values pass through Django
     template autoescaping, so this HTML may safely contain iframes etc.
  6. Rewrite the writer's `#layer-…` links into controls that drive the card
     the post is written about. Same trust argument as step 5, and it has to
     come after the sanitizer for the same reason: the markup carries data
     attributes and a class that nh3 would otherwise strip.
"""

import re
import secrets
from dataclasses import dataclass

import markdown as md
import nh3
from django.template.loader import render_to_string
from django.utils.safestring import SafeString, mark_safe
from django.utils.text import slugify

SHORTCODE_LINE_RE = re.compile(r'^\[\[(\w+)((?:\s+\w+="[^"]*")*)\s*\]\]$')
ATTR_RE = re.compile(r'(\w+)="([^"]*)"')

ALLOWED_TAGS = {
    "p", "h1", "h2", "h3", "h4", "blockquote", "pre", "code", "em", "strong",
    "del", "ul", "ol", "li", "a", "hr", "br", "table", "thead",
    "tbody", "tr", "th", "td",
}
ALLOWED_ATTRIBUTES = {"a": {"href", "title"}}
ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}

# A link the writer aimed at one layer of the post's own mix, written as an
# ordinary markdown link to a fragment: [the rain](#layer-2). A fragment is
# what makes this work at all — nh3 keeps relative hrefs untouched, while an
# invented scheme (layer:2) would be dropped along with the link.
LAYER_LINK_RE = re.compile(
    r'<a\b(?P<before>[^>]*?)href="#layer-(?P<key>[^"]+)"(?P<after>[^>]*)>'
    r"(?P<text>.*?)</a>",
    re.DOTALL | re.IGNORECASE,
)
LAYER_LINK_TEMPLATE = "core/post_layer_link.html"

# A link the writer aimed off the site. A post is read with its mix playing,
# and the whole front end is one page, so following such a link in this tab
# ends the listen and drops everything the reader had built — external links
# therefore open in a new one, without the writer having to ask. nh3 puts
# `rel="noopener noreferrer"` on every link below, which is what makes that
# safe to do by default.
#
# The match is zero-width past `<a`: only the tag name is rewritten, so the
# sanitizer's own attribute order and quoting come through untouched. Running
# directly after nh3 is what keeps the lookahead honest — a link is down to
# `href` and `title` by then, so no target can already be there to duplicate,
# and a title's own quotes have been escaped, so `href="` can only be the
# attribute itself and never something quoted inside one.
#
# Anything that isn't http(s) is deliberately left alone: `#layer-…` is a
# control over the card on this page (see below), a relative href is another
# page of the app, and a `mailto:` hands over to a mail client rather than
# navigating anywhere — a new tab for one of those would just sit there empty.
EXTERNAL_LINK_RE = re.compile(r'<a\b(?=[^>]*\shref="https?://)', re.IGNORECASE)


@dataclass(frozen=True)
class Shortcode:
    template: str
    allowed_attrs: frozenset[str]


# Adding a component = one cotton-using template file + one entry here.
SHORTCODES = {
    "youtube": Shortcode(
        template="core/post_shortcodes/youtube.html",
        allowed_attrs=frozenset({"id", "caption"}),
    ),
}


def _extract_shortcodes(body):
    """Replace known shortcode lines with placeholders; return (text, {placeholder: html})."""
    nonce = secrets.token_hex(4)
    rendered = {}
    lines = []
    for line in body.splitlines():
        match = SHORTCODE_LINE_RE.match(line.strip())
        shortcode = SHORTCODES.get(match.group(1)) if match else None
        if not shortcode:
            lines.append(line)
            continue
        attrs = {
            key: value
            for key, value in ATTR_RE.findall(match.group(2))
            if key in shortcode.allowed_attrs
        }
        token = f"@@sc-{nonce}-{len(rendered)}@@"
        rendered[token] = render_to_string(shortcode.template, attrs)
        # Blank lines guarantee the placeholder becomes its own paragraph.
        lines.extend(["", token, ""])
    return "\n".join(lines), rendered


def _open_external_links_in_new_tab(html):
    """Give every off-site link a target, leaving on-page links to their job."""
    return EXTERNAL_LINK_RE.sub('<a target="_blank"', html)


def _layer_slug(value):
    """The comparable form of a layer title, or of a key naming one.

    Underscores are folded in with the spaces and punctuation slugify already
    flattens, because a fragment is a place people reach for one — `slugify`
    on its own keeps them, and `#layer-Rain_on_Tin` would then miss a layer
    called "Rain on Tin" for no reason a writer could see.
    """
    return slugify(str(value).replace("_", "-"))


def _layer_index(key, layers):
    """Which layer a `#layer-…` key names, or None when this mix has no such layer.

    Two forms, so a writer can use whichever reads better next to the mix they
    are describing: the number the card's layer pill shows (`#layer-3`), or the
    layer's sound title slugified (`#layer-rain-on-tin`), which says what it
    means and survives the mix being ordered differently.
    """
    if key.isascii() and key.isdigit():
        # The pill counts from one; `layers` is indexed from zero.
        index = int(key) - 1
        return index if 0 <= index < len(layers) else None
    wanted = _layer_slug(key)
    if not wanted:
        return None
    for index, layer in enumerate(layers):
        if _layer_slug(layer.get("sound_title", "")) == wanted:
            return index
    return None


def _link_layers(html, layers, card_anchor):
    """Turn the writer's `#layer-…` links into controls over the post's card.

    A key naming no layer in this mix keeps its words and loses its link. That
    is the case a post falls into when its cosound is swapped for one built
    from other sounds, and a control that cannot reach anything is worse to
    press than plain prose.

    With no mix or no card to point at — `render_markdown` called on its own —
    every such link degrades the same way, because there is nothing on the page
    for one to do.
    """
    if not layers or not card_anchor:
        return LAYER_LINK_RE.sub(lambda match: match.group("text"), html)

    def replace(match):
        index = _layer_index(match.group("key"), layers)
        if index is None:
            return match.group("text")
        return render_to_string(
            LAYER_LINK_TEMPLATE,
            {
                "index": index,
                # Already through nh3 with the rest of the document, so the
                # writer's emphasis inside the link text survives being
                # re-rendered here.
                "label": mark_safe(match.group("text")),
                "layer_title": layers[index].get("sound_title", ""),
                "card_anchor": card_anchor,
            },
            # Rendering leaves a trailing newline, which would show up as a
            # space in the middle of a sentence.
        ).strip()

    return LAYER_LINK_RE.sub(replace, html)


def render_markdown(body: str, layers=(), card_anchor="") -> SafeString:
    """Render a post body.

    `layers` must be the very list the page mounts the soundscape store with —
    a layer link carries an index into it — and `card_anchor` the id of the
    element holding that mix's card, which is where a link scrolls to.
    """
    text, shortcodes = _extract_shortcodes(body)
    html = md.markdown(text, extensions=["fenced_code", "tables"])
    html = nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer",
    )
    html = _open_external_links_in_new_tab(html)
    for token, component_html in shortcodes.items():
        paragraph_re = re.compile(rf"<p>\s*{re.escape(token)}\s*</p>")
        if paragraph_re.search(html):
            html = paragraph_re.sub(lambda m: component_html, html, count=1)
        else:
            html = html.replace(token, component_html, 1)
    html = _link_layers(html, layers, card_anchor)
    return mark_safe(html)
