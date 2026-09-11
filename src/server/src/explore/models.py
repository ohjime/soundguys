import uuid

from django.db import models
from django.urls import reverse

from core.models import Post
# Historical migrations import this validator from its original path.
from core.models import validate_authors


class PublicPost(models.Model):
    """A fixed Explore mix linked to independently editable writing."""

    post = models.ForeignKey(Post, on_delete=models.PROTECT, related_name="public_posts")
    slug = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    cosound = models.ForeignKey(
        "core.Cosound", verbose_name="featured cosound",
        on_delete=models.SET_NULL, null=True, blank=True, related_name="public_posts",
    )

    class Meta:
        ordering = ["-post__publication_date", "-post__created_at"]

    def __str__(self):
        return str(self.post)

    def cosound_sounds(self, user=None):
        """The fixed mix as playback layers, or empty when no mix is attached."""
        if not self.cosound_id:
            return []
        return self.cosound.as_layers(user)

    @property
    def card_anchor_id(self):
        """The id on the deck holding this post's card.

        A layer link in the writing is an ordinary fragment link to it, so a
        press lands on the card even before explore-layer-links.js has taken
        the click over — and that is where the script scrolls to as well.
        """
        return f"explore-cosound-{self.pk}"

    def get_absolute_url(self):
        # The admin/studio hosts run their own urlconfs, which don't mount
        # `explore` — always reverse against the main-site urlconf.
        return reverse(
            "explore:detail", kwargs={"slug": self.slug}, urlconf="config.urls"
        )
