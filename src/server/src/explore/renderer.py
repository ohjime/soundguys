"""Markdown → safe HTML rendering for explore posts.

Pipeline (order matters):
  1. Extract shortcode lines ([[name attr="…"]]) and render their cotton
     component templates, leaving nonce'd placeholders in the text.
  2. Render markdown to HTML.
  3. Sanitize with an explicit nh3 allowlist — author markdown can never emit
     scripts, event handlers, or iframes.
  4. Re-inject the rendered component HTML over the placeholders. Component
     templates are ours (trusted), and their attr values pass through Django
     template autoescaping, so this HTML may safely contain iframes etc.
"""

import re
import secrets
from dataclasses import dataclass

import markdown as md
import nh3
from django.db.models import Count
from django.template.loader import render_to_string
from django.utils.safestring import SafeString, mark_safe

SHORTCODE_LINE_RE = re.compile(r'^\[\[(\w+)((?:\s+\w+="[^"]*")*)\s*\]\]$')
ATTR_RE = re.compile(r'(\w+)="([^"]*)"')

ALLOWED_TAGS = {
    "p", "h1", "h2", "h3", "h4", "blockquote", "pre", "code", "em", "strong",
    "del", "ul", "ol", "li", "a", "hr", "br", "table", "thead",
    "tbody", "tr", "th", "td",
}
ALLOWED_ATTRIBUTES = {"a": {"href", "title"}}
ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


@dataclass(frozen=True)
class Shortcode:
    template: str
    allowed_attrs: frozenset[str]


# Adding a component = one cotton-using template file + one entry here.
SHORTCODES = {
    "youtube": Shortcode(
        template="explore/shortcodes/youtube.html",
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


def render_markdown(body: str) -> SafeString:
    text, shortcodes = _extract_shortcodes(body)
    html = md.markdown(text, extensions=["fenced_code", "tables"])
    html = nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_URL_SCHEMES,
        link_rel="noopener noreferrer",
    )
    for token, component_html in shortcodes.items():
        paragraph_re = re.compile(rf"<p>\s*{re.escape(token)}\s*</p>")
        if paragraph_re.search(html):
            html = paragraph_re.sub(lambda m: component_html, html, count=1)
        else:
            html = html.replace(token, component_html, 1)
    return mark_safe(html)


def render_post(post) -> SafeString:
    return render_markdown(post.article)


def get_previous_posts_context(current_post):
    """The five newest other posts, plus one locked archive preview."""
    if current_post is None:
        return {"previous_posts": [], "locked_previous_post": None}

    posts = list(
        get_published_posts()
        .exclude(pk=current_post.pk)
        .annotate(comment_count=Count("comments"))[:6]
    )
    return {
        "previous_posts": posts[:5],
        "locked_previous_post": posts[5] if len(posts) > 5 else None,
    }


def get_published_posts():
    """Published posts, newest first.

    `cosound__isnull=False` is a second lock on the same rule the model holds:
    a post is only publishable with a mix behind it. The model cannot see a
    cosound row being deleted (SET_NULL is a bulk UPDATE), so a post can lose
    its card between saves — this is what keeps such a post off the site even
    in the window before the pre_delete receiver has unpublished it.
    """
    from explore.models import Post

    return (
        Post.objects.filter(
            publication_date__isnull=False,
            cosound__isnull=False,
        )
        .select_related("cosound")
        .order_by("-publication_date", "-created_at", "-pk")
    )


def get_explore_context(user=None):
    """Build the latest published post and its discussion context.

    `user` only reaches the card's layers, deciding which sounds open with a
    filled heart, plus the one-comment state. The writing itself is the same
    for everyone. Older posts remain in context for the reserved tab but are
    intentionally not rendered yet.
    """
    posts = get_published_posts()
    lead_post = posts.first()
    if lead_post:
        posts = posts.exclude(pk=lead_post.pk)
    context = {
        "explore_post": lead_post,
        "explore_body_html": render_post(lead_post) if lead_post else "",
        "explore_sounds": lead_post.cosound_sounds(user) if lead_post else [],
        "explore_posts": posts,
        **get_previous_posts_context(lead_post),
    }
    if lead_post:
        from explore.discussion import build_discussion_context

        context.update(build_discussion_context(lead_post, user))
    return context
