import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models
from django.db.models.signals import pre_delete
from django.dispatch import receiver
from django.urls import reverse

from core.models import Cosound
from explore.fonts import article_font_css_stack


def validate_authors(authors):
    """Validate embedded author credits without creating an Author model."""
    if not isinstance(authors, list):
        raise ValidationError("Authors must be a list.")

    validate_url = URLValidator()
    for index, author in enumerate(authors, start=1):
        if not isinstance(author, dict):
            raise ValidationError(f"Author {index} must be an object.")
        unknown = set(author) - {"name", "role", "url"}
        if unknown:
            raise ValidationError(
                f"Author {index} has unsupported fields: {', '.join(sorted(unknown))}."
            )
        for field in ("name", "role"):
            value = author.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValidationError(f"Author {index} requires a {field}.")
        url = author.get("url", "")
        if url:
            if not isinstance(url, str):
                raise ValidationError(f"Author {index} URL must be text.")
            try:
                validate_url(url)
            except ValidationError as error:
                raise ValidationError(f"Author {index} has an invalid URL.") from error


class Post(models.Model):
    announcers_call = models.CharField(max_length=100, default="Presenting")
    title = models.CharField(max_length=200)
    # The mix the post is about. A post is a piece of writing *around* a
    # cosound, so a post without one has nothing to publish — see clean() and
    # save(), which between them ensure a publication date always means the
    # post has a playable cosound.
    cosound = models.ForeignKey(
        "core.Cosound",
        verbose_name="featured cosound",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="explore_posts",
    )
    greeting_style = models.CharField(max_length=100, default="Dear Listener")
    font_family = models.CharField(
        max_length=100,
        default="dancing-script",
        blank=True,
        help_text="Typography available to selected elements in the post template.",
    )
    article = models.TextField(blank=True)
    authors = models.JSONField(
        default=list,
        blank=True,
        validators=[validate_authors],
        help_text='A list of objects with "name", "role", and an optional "url".',
    )
    composer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="composed_explore_posts",
    )
    slug = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    publication_date = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-publication_date", "-created_at"]

    def __str__(self):
        return self.title

    @property
    def font_css_stack(self):
        return article_font_css_stack(self.font_family)

    def clean(self):
        """Refuse a publication date when there is no associated cosound."""
        super().clean()
        if self.publication_date and not self.cosound_id:
            raise ValidationError(
                {
                    "publication_date": "An Explore post cannot be published without an associated cosound.",
                    "cosound": "A cosound is required to publish an Explore post.",
                }
            )

    def save(self, *args, **kwargs):
        """Unpublish rather than store a published post with nothing to play.

        clean() catches this in the admin, where a human can be told why. This
        catches every other way the field can be cleared — a shell edit, a data
        migration, a script — because the front end has no rendering for a
        post whose card is missing.
        """
        if self.publication_date and not self.cosound_id:
            self.publication_date = None
            update_fields = kwargs.get("update_fields")
            if update_fields is not None and "publication_date" not in update_fields:
                kwargs["update_fields"] = [*update_fields, "publication_date"]
        super().save(*args, **kwargs)

    def cosound_sounds(self, user=None):
        """The associated mix as mountStore layers; empty when there is none.

        Only a draft can reach the empty case — get_published_posts() will not
        return a post without a cosound — but the admin preview and any future
        caller get a list either way rather than an AttributeError.
        """
        if not self.cosound_id:
            return []
        return self.cosound.as_layers(user)

    def get_absolute_url(self):
        # The admin/studio hosts run their own urlconfs, which don't mount
        # `explore` — always reverse against the main-site urlconf.
        return reverse(
            "explore:detail", kwargs={"slug": self.slug}, urlconf="config.urls"
        )


class Comment(models.Model):
    """A listener's single, permanent response to an Explore post."""

    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name="comments",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="explore_comments",
    )
    body = models.TextField(max_length=2000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["post", "user"],
                name="unique_explore_comment_per_user_post",
            )
        ]

    def __str__(self):
        return f"{self.user} on {self.post}"


@receiver(pre_delete, sender=Cosound)
def unpublish_posts_losing_their_cosound(sender, instance, **kwargs):
    """Deleting a cosound unpublishes the posts that were built on it.

    `on_delete=SET_NULL` clears the column with a bulk UPDATE, which never
    calls Post.save(), so the guard there cannot see this happen. Doing
    it here keeps the publication date honest — get_published_posts() would
    hide such a post anyway, but the admin list would still claim it was live.
    """
    instance.explore_posts.filter(publication_date__isnull=False).update(
        publication_date=None
    )
