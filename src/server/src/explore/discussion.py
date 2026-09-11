"""Route shared comments through the public Explore post."""

from django.urls import reverse

from core.discussion import COMMENTS_PER_PAGE
from core.discussion import build_discussion_context as build_post_discussion_context


def build_discussion_context(post, user, page_number=1, form=None):
    context = build_post_discussion_context(
        post.post,
        user,
        page_number,
        form,
        discussion_url=reverse("explore:discussion", kwargs={"slug": post.slug}),
        comment_url=reverse("explore:create_comment", kwargs={"slug": post.slug}, query={"post": post.post.slug}),
        dom_id=f"explore-discussion-{post.pk}",
    )
    context["post"] = post
    return context
