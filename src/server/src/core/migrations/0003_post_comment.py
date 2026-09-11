import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import core.models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_player_location_sound_artist_legacy_alter_player_bio_and_more"),
        ("explore", "0007_remove_post_farewell_style"),
    ]

    operations = [
        migrations.CreateModel(
            name="Post",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("announcers_call", models.CharField(default="Presenting", max_length=100)),
                ("title", models.CharField(max_length=200)),
                ("greeting_style", models.CharField(default="Dear Listener", max_length=100)),
                (
                    "font_family",
                    models.CharField(
                        blank=True,
                        default="dancing-script",
                        help_text="Typography available to selected elements in the post template.",
                        max_length=100,
                    ),
                ),
                ("article", models.TextField(blank=True)),
                (
                    "authors",
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text='A list of objects with "name", "role", and an optional "url".',
                        validators=[core.models.validate_authors],
                    ),
                ),
                ("slug", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("publication_date", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "composer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="composed_posts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-publication_date", "-created_at"]},
        ),
        migrations.CreateModel(
            name="Comment",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("body", models.TextField(max_length=2000)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "post",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="comments",
                        to="core.post",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="comments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at", "-pk"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("post", "user"),
                        name="unique_comment_per_user_post",
                    )
                ],
            },
        ),
    ]
