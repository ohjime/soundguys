"""Explore publication queries and fixed-mix article rendering."""

from django.db.models import Count
from django.utils.safestring import SafeString

from core.renderer import render_markdown


def render_post(post, layers=None) -> SafeString:
    """The post's writing, with its layer links wired to the card above it.

    Callers that already hold the reader's layers pass them in: the link
    carries a position in that list, so building a second one here would leave
    the two free to disagree about what layer 3 is.
    """
    if layers is None:
        layers = post.cosound_sounds()
    return render_markdown(post.post.article, layers, post.card_anchor_id)


def get_previous_posts_context(current_post):
    """The five newest other posts, plus one locked archive preview."""
    if current_post is None:
        return {"previous_posts": [], "locked_previous_post": None}

    posts = list(
        get_published_posts()
        .exclude(pk=current_post.pk)
        .annotate(comment_count=Count("post__comments"))[:6]
    )
    return {
        "previous_posts": posts[:5],
        "locked_previous_post": posts[5] if len(posts) > 5 else None,
    }


def get_published_posts():
    """Published writing with an available fixed mix, newest first."""
    from explore.models import PublicPost

    return (
        PublicPost.objects.filter(
            post__publication_date__isnull=False,
            cosound__isnull=False,
        )
        .select_related("post", "cosound")
        .order_by("-post__publication_date", "-post__created_at", "-pk")
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
    # Built once and handed to both the card and the writing: a layer link in
    # the writing is a position in this list.
    sounds = lead_post.cosound_sounds(user) if lead_post else []
    context = {
        "explore_post": lead_post,
        "explore_body_html": render_post(lead_post, sounds) if lead_post else "",
        "explore_sounds": sounds,
        "explore_posts": posts,
        **get_previous_posts_context(lead_post),
    }
    if lead_post:
        from explore.discussion import build_discussion_context

        context.update(build_discussion_context(lead_post, user))
    return context
