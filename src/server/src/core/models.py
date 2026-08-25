import datetime
import hashlib
import secrets
from datetime import datetime, timezone
from decimal import ROUND_UP, Decimal
from typing import List

from django.contrib.auth.models import AbstractUser
from django.db import models as DjangoDB
from django.db import transaction
from django_pydantic_field import SchemaField
from pgvector.django import VectorField
from pydantic import BaseModel, Field
from taggit.managers import TaggableManager

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

    def save(self, *args, **kwargs):
        if self.embeddings is None:
            classifier = _get_sound_classifier()
            self.embeddings = classifier(self.pk)
        super().save(*args, **kwargs)

    def asLayer(self, with_gain=1.0):
        return {
            "sound_id": self.pk,
            "sound_file": self.file.url,
            "sound_gain": with_gain,
            "sound_title": self.title,
            "sound_artist": self.artist_name,
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
    sounds = DjangoDB.ManyToManyField(Sound, blank=True)
    playing: Prediction = SchemaField(default=Prediction)
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
        if not self.token:
            self.token = secrets.token_hex(32)
        super().save(*args, **kwargs)

    def library(self) -> List[Sound]:
        return list(self.sounds.all())

    def update(self, prediction: Prediction) -> None:
        self.playing = prediction
        self.save()

    def announce(self, prediction: Prediction) -> None:
        print(f"New Prediction for \033[1m{self.name}\033[22m:")
        print(prediction.summary())
