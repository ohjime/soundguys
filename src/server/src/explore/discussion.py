from django.core.paginator import Paginator

from explore.forms import CommentForm


COMMENTS_PER_PAGE = 10


def build_discussion_context(post, user, page_number=1, form=None):
    form = form or CommentForm()
    comments = post.comments.select_related("user").all()
    paginator = Paginator(comments, COMMENTS_PER_PAGE)
    page = paginator.get_page(page_number)
    has_commented = bool(
        getattr(user, "is_authenticated", False)
        and post.comments.filter(user=user).exists()
    )
    pagination_items = []
    for value in paginator.get_elided_page_range(
        page.number, on_each_side=1, on_ends=1
    ):
        if value == paginator.ELLIPSIS:
            pagination_items.append({"ellipsis": True})
        else:
            pagination_items.append(
                {"number": value, "current": value == page.number}
            )

    return {
        "post": post,
        # Cotton components can be nested (home/index -> article -> discussion).
        # Pass plain values through those boundaries rather than a BoundField,
        # which loses its widget rendering at the second component hop.
        "comment_body": form["body"].value() or "",
        "comment_errors": [str(error) for error in form["body"].errors],
        "comments_page": page,
        "comment_count": paginator.count,
        "has_commented": has_commented,
        "pagination_items": pagination_items,
    }
