from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from explore.discussion import build_discussion_context
from explore.forms import CommentForm
from explore.models import Comment, Post
from explore.renderer import (
    get_explore_context,
    get_previous_posts_context,
    render_post,
)


def get_published_post(slug):
    return get_object_or_404(
        Post.objects.select_related("cosound"),
        slug=slug,
        publication_date__isnull=False,
        cosound__isnull=False,
    )


def explore_index(request):
    # Public page: non-htmx GETs render the full shell (unlike the usual
    # "Request Denied." fragment guard). The htmx branch also serves the home
    # page's EXPLORE tab directly.
    if not request.htmx:
        return render(request, "explore/index.html")
    return render(
        request, "explore/index.html#index", get_explore_context(request.user)
    )


def explore_detail(request, slug):
    # A post that lost its cosound is not readable either, so the detail page
    # agrees with the list rather than serving an article with no card on it.
    post = get_published_post(slug)
    if not request.htmx:
        return render(request, "explore/detail.html", {"post": post})
    context = {
        "post": post,
        "body_html": render_post(post),
        "sounds": post.cosound_sounds(request.user),
        **build_discussion_context(post, request.user),
        **get_previous_posts_context(post),
    }
    return render(request, "explore/detail.html#detail", context)


@require_GET
def explore_discussion(request, slug):
    post = get_published_post(slug)
    context = build_discussion_context(
        post, request.user, request.GET.get("page", 1)
    )
    context["update_tab_count"] = request.htmx
    return render(
        request,
        "explore/discussion.html",
        context,
    )


@require_POST
def create_comment(request, slug):
    post = get_published_post(slug)
    if not request.user.is_authenticated:
        return HttpResponse("Sign in to comment.", status=403)

    form = CommentForm(request.POST)
    if not form.is_valid():
        context = build_discussion_context(post, request.user, form=form)
        context["update_tab_count"] = request.htmx
        return render(request, "explore/discussion.html", context)

    try:
        with transaction.atomic():
            Comment.objects.create(
                post=post,
                user=request.user,
                body=form.cleaned_data["body"],
            )
    except IntegrityError:
        # The unique constraint is the final guard when two submissions race.
        pass

    if not request.htmx:
        return redirect(f"{post.get_absolute_url()}?tab=comments#explore-discussion-{post.pk}")
    context = build_discussion_context(post, request.user)
    context["update_tab_count"] = True
    return render(request, "explore/discussion.html", context)
