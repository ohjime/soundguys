import hashlib
import secrets
import uuid
from decimal import ROUND_UP, Decimal
from typing import List

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import models as DjangoDB
from django.db import router, transaction
from django.urls import reverse
from django.utils import timezone
from django_pydantic_field import SchemaField
from pgvector.django import VectorField
from pydantic import BaseModel, Field
from taggit.managers import TaggableManager

from core.fonts import article_font_css_stack
from core.utils import (
    _get_sound_classifier,
    _get_sound_dimension,
    generate_layers_string,
    get_random_avatar_url,
)


class Sound(DjangoDB.Model):
    file = DjangoDB.FileField(upload_to="sounds/")
    title = DjangoDB.CharField(max_length=255)
    artist = DjangoDB.ForeignKey(
        "Artist",
        on_delete=DjangoDB.SET_NULL,
        null=True,
        blank=True,
        related_name="sounds",
    )
    # Optional grouping of an artist's sounds. A sound with no set is a "single".
    set = DjangoDB.ForeignKey(
        "Set",
        on_delete=DjangoDB.SET_NULL,
        null=True,
        blank=True,
        default=None,
        related_name="sounds",
    )
    # Legacy free-text artist, kept so the artist FK can be backfilled. Unused
    # by the app going forward; safe to drop once the migration is verified.
    artist_legacy = DjangoDB.CharField(max_length=255, blank=True, null=True)
    tags = TaggableManager(blank=True)
    art = DjangoDB.ImageField(
        upload_to="sound_arts/", blank=True, null=True, max_length=255
    )
    flavor = DjangoDB.TextField(blank=True, null=True, max_length=200)
    embeddings = VectorField(null=True, dimensions=_get_sound_dimension())
    created_at = DjangoDB.DateTimeField(auto_now_add=True)
    updated_at = DjangoDB.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

    @property
    def artist_name(self) -> str:
        """Display name of the artist, falling back to the legacy text."""
        if self.artist_id:
            return self.artist.name
        return self.artist_legacy or ""

    @property
    def artist_url(self) -> str:
        """The artist's own page, when they have given us one.

        Empty for a legacy credit, which is a name in a text column with no
        Artist row — and so nowhere to keep a URL — behind it. That empty
        string is what the carousel reads to decide whether pressing the name
        leaves for the artist's own site or opens our details modal instead.
        """
        if self.artist_id:
            return self.artist.url or ""
        return ""

    def save(self, *args, **kwargs):
        if self.embeddings is None:
            classifier = _get_sound_classifier()
            self.embeddings = classifier(self.pk)
        super().save(*args, **kwargs)

    def asLayer(self, with_gain=1.0):
        # Every surface that mounts a soundscape spreads this dict, so a field
        # added here reaches the library, the explore card, the swap picker and
        # the studio at once. `artist_url` rides along with the name it belongs
        # to: the two are one credit, and a layer carrying one without the
        # other is how a name comes to point at the wrong artist's site.
        return {
            "sound_id": self.pk,
            "sound_file": self.file.url,
            "sound_gain": with_gain,
            "sound_title": self.title,
            "sound_artist": self.artist_name,
            "artist_url": self.artist_url,
        }


class SoundLayer(DjangoDB.Model):
    sound = DjangoDB.ForeignKey(Sound, on_delete=DjangoDB.CASCADE)
    mix = DjangoDB.ForeignKey("Cosound", on_delete=DjangoDB.CASCADE)
    gain = DjangoDB.DecimalField(max_digits=3, decimal_places=2)

    def __str__(self):
        return f"{self.sound.pk}@{self.gain}"


class Cosound(DjangoDB.Model):
    layers = DjangoDB.ManyToManyField(Sound, through=SoundLayer)
    hashset = DjangoDB.CharField(max_length=64, editable=False, db_index=True)
    created_at = DjangoDB.DateTimeField(auto_now_add=True)
    hashid = DjangoDB.CharField(
        max_length=64, unique=True, editable=False, db_index=True
    )

    @classmethod
    def normalize_layers(cls, layers):
        normalized = []
        for sound_id, gain in layers:
            gain = Decimal(str(gain))
            rounded = (gain * 2).quantize(Decimal("0.1"), rounding=ROUND_UP) / 2
            normalized.append((sound_id, rounded))
        normalized.sort(key=lambda t: t[0])
        return normalized

    @staticmethod
    def compute_hashid(layers):
        normalized = Cosound.normalize_layers(layers)
        key = generate_layers_string(normalized)
        hashid = hashlib.sha256(key.encode()).hexdigest()
        return hashid

    @staticmethod
    def compute_hashset(layers):
        normalized = Cosound.normalize_layers(layers)
        key = generate_layers_string(normalized, with_gain=False)
        hashset = hashlib.sha256(key.encode()).hexdigest()
        return hashset

    @classmethod
    def get_or_create_from_layers(cls, layers):
        hashid = cls.compute_hashid(layers)
        hashset = cls.compute_hashset(layers)
        normalized = cls.normalize_layers(layers)
        with transaction.atomic():
            cosound, created = cls.objects.get_or_create(
                hashid=hashid, defaults={"hashset": hashset}
            )
            if created:
                SoundLayer.objects.bulk_create(
                    [
                        SoundLayer(sound_id=sid, mix=cosound, gain=g)
                        for sid, g in normalized
                    ]
                )
        return cosound

    @classmethod
    def with_sound_set(cls, sound_ids):
        """Cosounds whose layer sound set exactly matches `sound_ids` (gain-agnostic)."""
        ids = list({int(sid) for sid in sound_ids})
        if not ids:
            return cls.objects.none()
        return cls.objects.filter(hashset=cls.compute_hashset(ids))

    def layering(self) -> List["SoundLayer"]:
        return list(self.soundlayer_set.select_related("sound").all())

    def as_layers(self, user=None):
        """This mix in the shape c-core-sound-player hands to the browser.

        The same dict shape library.utils.get_random_sounds builds, so any
        surface that renders the shared soundscape card can mount a stored
        cosound the way the library mounts a random one.

        `user` decides only whether each layer opens with a filled heart —
        pass the request's user wherever the card is rendered for someone, or
        leave it off and every layer reads as uncollected.
        """
        saved_ids = set()
        if user is not None and user.is_authenticated:
            listener = Listener.objects.filter(user=user).first()
            if listener is not None:
                saved_ids = set(listener.collection.values_list("id", flat=True))

        layers = self.soundlayer_set.select_related("sound__artist").prefetch_related(
            "sound__tags"
        )
        return [
            {
                **layer.sound.asLayer(with_gain=float(layer.gain)),
                "artwork_url": layer.sound.art.url if layer.sound.art else "",
                # A stored mix already carries a muted layer as gain 0, so
                # nothing here starts muted — the fader shows where it sits.
                "mute": False,
                "saved": layer.sound.pk in saved_ids,
                "flavor": layer.sound.flavor or "",
                "tags": " / ".join(layer.sound.tags.names()) or "Unknown",
            }
            for layer in layers
        ]

    def __str__(self):
        layers = []
        for layer in self.soundlayer_set.all():  # type: ignore
            layers.append(tuple([layer.sound.pk, layer.gain]))
        return generate_layers_string(layers)


class PredictionLayer(BaseModel):
    sound_id: int
    sound_gain: float = Field(default=1.0, ge=0.0, le=1.0)


class Prediction(BaseModel):
    layers: List[PredictionLayer] = Field(default_factory=list)

    @classmethod
    def new(cls) -> "Prediction":
        return cls()

    def add_layer(self, sound_id: int, gain: float = 1.0) -> None:
        self.layers.append(PredictionLayer(sound_id=sound_id, sound_gain=gain))

    def __bool__(self) -> bool:
        return bool(self.layers)

    def summary(self):
        from core.models import Sound

        response = "Prediction Summary:\n"
        for layer in self.layers:
            try:
                sound = Sound.objects.get(pk=layer.sound_id)
                response += f"- {sound.title} by {sound.artist_name} at gain {layer.sound_gain}\n"
            except Sound.DoesNotExist:
                response += f"- Sound ID {layer.sound_id} not found at gain {layer.sound_gain}\n"
        return response


class User(AbstractUser):
    email = DjangoDB.EmailField(unique=True)
    username = DjangoDB.CharField(max_length=255, unique=True)
    avatar = DjangoDB.ImageField(
        upload_to="avatars/", blank=True, null=True, max_length=255
    )
    created_at = DjangoDB.DateTimeField(auto_now_add=True)
    updated_at = DjangoDB.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["username"]

    @property
    def avatar_url(self):
        if self.avatar:
            return self.avatar.url
        return get_random_avatar_url(self.pk)

    @property
    def is_anonymous_account(self):
        return bool(self.email) and self.email.endswith("@anon.cosound.ca")


class Listener(DjangoDB.Model):
    user = DjangoDB.OneToOneField(User, on_delete=DjangoDB.CASCADE)
    collection = DjangoDB.ManyToManyField(Sound, blank=True, related_name="saved_by")
    created_at = DjangoDB.DateTimeField(auto_now_add=True)
    updated_at = DjangoDB.DateTimeField(auto_now=True)

    def __str__(self):
        return str(self.user)

    def collected(self) -> list[Sound]:
        return list(self.collection.all())


class Manager(DjangoDB.Model):
    user = DjangoDB.ForeignKey(User, on_delete=DjangoDB.CASCADE)
    name = DjangoDB.CharField(max_length=255)
    logo = DjangoDB.ImageField(
        upload_to="logos/", blank=True, null=True, max_length=255
    )
    bio = DjangoDB.TextField(blank=True)
    created_at = DjangoDB.DateTimeField(auto_now_add=True)
    updated_at = DjangoDB.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Artist(DjangoDB.Model):
    user = DjangoDB.ForeignKey(User, on_delete=DjangoDB.SET_NULL, null=True, blank=True)
    name = DjangoDB.CharField(max_length=255)
    bio = DjangoDB.TextField(blank=True)
    url = DjangoDB.URLField(blank=True)
    avatar = DjangoDB.ImageField(
        upload_to="artist_avatars/", blank=True, null=True, max_length=255
    )
    cover = DjangoDB.ImageField(
        upload_to="artist_covers/", blank=True, null=True, max_length=255
    )
    created_at = DjangoDB.DateTimeField(auto_now_add=True)
    updated_at = DjangoDB.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name

    def singles(self) -> list["Sound"]:
        """Sounds by this artist that are not grouped into a set."""
        return list(self.sounds.filter(set__isnull=True))


class Set(DjangoDB.Model):
    artist = DjangoDB.ForeignKey(
        Artist, on_delete=DjangoDB.CASCADE, related_name="sets"
    )
    name = DjangoDB.CharField(max_length=255)
    bio = DjangoDB.TextField(blank=True)
    cover = DjangoDB.ImageField(
        upload_to="set_covers/", blank=True, null=True, max_length=255
    )
    created_at = DjangoDB.DateTimeField(auto_now_add=True)
    updated_at = DjangoDB.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Player(DjangoDB.Model):
    post = DjangoDB.OneToOneField(
        "LocalPost",
        on_delete=DjangoDB.PROTECT,
        related_name="player",
        blank=True,
    )
    playing: Prediction = SchemaField(default=Prediction)
    sleeping = DjangoDB.BooleanField(default=True)
    activated_at = DjangoDB.DateTimeField(blank=True, null=True)
    manager = DjangoDB.ForeignKey(Manager, on_delete=DjangoDB.CASCADE)
    token = DjangoDB.CharField(max_length=64, unique=True, editable=False)
    name = DjangoDB.CharField(max_length=255)
    photo = DjangoDB.ImageField(
        upload_to="photos/", blank=True, null=True, max_length=255
    )
    bio = DjangoDB.TextField(blank=True, max_length=200)
    location = DjangoDB.CharField(max_length=255, blank=True)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            if not update_fields:
                return
        sleeping = not bool(self.playing)
        if self.sleeping != sleeping:
            self.sleeping = sleeping
            if update_fields is not None:
                update_fields.add("sleeping")
        if sleeping and self.activated_at is not None:
            self.activated_at = None
            if update_fields is not None:
                update_fields.add("activated_at")
        if not self.token:
            self.token = secrets.token_hex(32)
            if update_fields is not None:
                update_fields.add("token")
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        kwargs["using"] = using
        if update_fields is not None:
            kwargs["update_fields"] = update_fields
        post_field = self._meta.get_field("post")
        supplied_post = post_field.get_cached_value(self, default=None)
        if self.post_id is not None or supplied_post is not None:
            # Keep Django's normal unsaved-related-object validation when a
            # caller explicitly supplied an unsaved LocalPost.
            return super().save(*args, **kwargs)

        # New players retain the public name/bio they previously displayed.
        # A separately authored LocalPost keeps the shared Post draft default.
        # Both records must commit together, including when the player fails a
        # uniqueness constraint or the caller is using another database.
        created_post = None
        try:
            with transaction.atomic(using=using):
                composer_id = Manager.objects.using(using).values_list(
                    "user_id", flat=True
                ).get(pk=self.manager_id)
                shared_post = Post.objects.using(using).create(
                    title=self.name,
                    article=self.bio,
                    composer_id=composer_id,
                    publication_date=timezone.now(),
                )
                created_post = LocalPost.objects.using(using).create(post=shared_post)
                self.post = created_post
                if update_fields is not None:
                    update_fields.add("post")
                return super().save(*args, **kwargs)
        except Exception:
            if created_post is not None:
                # Permit retrying this instance after its creation rolls back.
                self.post = None
            raise

    def library(self) -> List[Sound]:
        return list(self.post.collection.all())

    def update(self, prediction: Prediction) -> None:
        self.playing = prediction
        self.save(update_fields=["playing"])

    def announce(self, prediction: Prediction) -> None:
        print(f"New Prediction for \033[1m{self.name}\033[22m:")
        print(prediction.summary())


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


class Post(DjangoDB.Model):
    """Shared writing and discussion, independent of a playback source."""

    announcers_call = DjangoDB.CharField(max_length=100, default="Presenting")
    title = DjangoDB.CharField(max_length=255)
    greeting_style = DjangoDB.CharField(max_length=100, default="Dear Listener")
    font_family = DjangoDB.CharField(
        max_length=100,
        default="dancing-script",
        blank=True,
        help_text="Typography available to selected elements in the post template.",
    )
    article = DjangoDB.TextField(blank=True)
    authors = DjangoDB.JSONField(
        default=list,
        blank=True,
        validators=[validate_authors],
        help_text='A list of objects with "name", "role", and an optional "url".',
    )
    composer = DjangoDB.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=DjangoDB.PROTECT,
        related_name="composed_posts",
    )
    slug = DjangoDB.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    publication_date = DjangoDB.DateTimeField(blank=True, null=True)
    created_at = DjangoDB.DateTimeField(auto_now_add=True)
    updated_at = DjangoDB.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-publication_date", "-created_at"]

    def __str__(self):
        return self.title

    @property
    def font_css_stack(self):
        return article_font_css_stack(self.font_family)


class LocalPost(DjangoDB.Model):
    """A player's writing and selectable sounds, independent of its live mix."""

    post = DjangoDB.ForeignKey(
        Post,
        on_delete=DjangoDB.PROTECT,
        related_name="local_posts",
    )
    collection = DjangoDB.ManyToManyField(Sound, blank=True)

    class Meta:
        ordering = ["-post__publication_date", "-post__created_at"]

    def __str__(self):
        return str(self.post)

    def get_absolute_url(self):
        try:
            player = self.player
        except Player.DoesNotExist:
            return reverse("vote:vote", urlconf="config.urls")
        return reverse(
            "vote:vote", urlconf="config.urls", query={"player": player.token}
        )


class Comment(DjangoDB.Model):
    """A listener's single, permanent response to a post."""

    post = DjangoDB.ForeignKey(
        Post,
        on_delete=DjangoDB.CASCADE,
        related_name="comments",
    )
    user = DjangoDB.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=DjangoDB.CASCADE,
        related_name="comments",
    )
    body = DjangoDB.TextField(max_length=2000)
    created_at = DjangoDB.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [
            DjangoDB.UniqueConstraint(
                fields=["post", "user"],
                name="unique_comment_per_user_post",
            )
        ]

    def __str__(self):
        return f"{self.user} on {self.post}"
