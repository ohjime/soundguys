import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def create_local_posts(apps, schema_editor):
    Player = apps.get_model("core", "Player")
    LocalPost = apps.get_model("core", "LocalPost")
    alias = schema_editor.connection.alias
    published_at = timezone.now()
    for player in Player.objects.using(alias).select_related("manager").iterator():
        post = LocalPost.objects.using(alias).create(
            title=player.name,
            article=player.bio,
            composer_id=player.manager.user_id,
            publication_date=published_at,
        )
        Player.objects.using(alias).filter(pk=player.pk).update(post_id=post.pk)

    quote = schema_editor.quote_name
    old_collection = Player._meta.get_field("sounds").remote_field.through
    new_collection = LocalPost._meta.get_field("collection").remote_field.through
    schema_editor.execute(
        f"INSERT INTO {quote(new_collection._meta.db_table)} (localpost_id, sound_id) "
        f"SELECT player.post_id, sounds.sound_id "
        f"FROM {quote(old_collection._meta.db_table)} sounds "
        f"JOIN {quote(Player._meta.db_table)} player ON player.id = sounds.player_id"
    )
    if schema_editor.connection.vendor == "postgresql":
        # Finish the FK checks from the backfill before setting post NOT NULL.
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def restore_player_collections(apps, schema_editor):
    Player = apps.get_model("core", "Player")
    LocalPost = apps.get_model("core", "LocalPost")
    old_collection = Player._meta.get_field("sounds").remote_field.through
    new_collection = LocalPost._meta.get_field("collection").remote_field.through
    quote = schema_editor.quote_name
    schema_editor.execute(
        f"INSERT INTO {quote(old_collection._meta.db_table)} (player_id, sound_id) "
        f"SELECT player.id, sounds.sound_id "
        f"FROM {quote(new_collection._meta.db_table)} sounds "
        f"JOIN {quote(Player._meta.db_table)} player ON player.post_id = sounds.localpost_id"
    )
    # Dropping the LocalPost child leaves its shared Post/Comment rows intact:
    # rolling playback back must not delete writing or listener responses.


def preserve_player_editor_permissions(apps, schema_editor):
    """Existing player editors must still be able to manage its collection."""
    alias = schema_editor.connection.alias
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    User = apps.get_model(settings.AUTH_USER_MODEL)
    Group = apps.get_model("auth", "Group")
    player_type = ContentType.objects.using(alias).filter(app_label="core", model="player").first()
    if player_type is None:
        # Fresh installs have no preexisting grants; post_migrate creates the
        # default permissions after all schema migrations have run.
        return
    local_type, _ = ContentType.objects.using(alias).get_or_create(app_label="core", model="localpost")
    for action in ("add", "change", "view", "delete"):
        source = Permission.objects.using(alias).filter(
            content_type=player_type, codename=f"{action}_player"
        ).first()
        if source is None:
            continue
        target_actions = ("change", "view") if action == "change" else (action,)
        for target_action in target_actions:
            target, _ = Permission.objects.using(alias).get_or_create(
                content_type=local_type,
                codename=f"{target_action}_localpost",
                defaults={"name": f"Can {target_action} local post"},
            )
            for owner_model, field_name in ((User, "user_permissions"), (Group, "permissions")):
                field = owner_model._meta.get_field(field_name)
                through = field.remote_field.through
                owner_column = f"{field.m2m_field_name()}_id"
                owner_ids = through.objects.using(alias).filter(permission_id=source.pk).values_list(
                    owner_column, flat=True
                )
                through.objects.using(alias).bulk_create(
                    [through(**{owner_column: owner_id, "permission_id": target.pk}) for owner_id in owner_ids],
                    ignore_conflicts=True,
                )


def remove_local_post_permissions(apps, schema_editor):
    # LocalPost is removed on rollback. Its new identity and grants can go,
    # while every original Player permission and assignment remains intact.
    ContentType = apps.get_model("contenttypes", "ContentType")
    ContentType.objects.using(schema_editor.connection.alias).filter(
        app_label="core", model="localpost"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [("core", "0004_post_title_length")]

    operations = [
        migrations.CreateModel(
            name="LocalPost",
            fields=[
                (
                    "post",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        parent_link=True,
                        primary_key=True,
                        related_name="local_post",
                        serialize=False,
                        to="core.post",
                    ),
                ),
                (
                    "collection",
                    models.ManyToManyField(blank=True, to="core.sound"),
                ),
            ],
            options={"ordering": ["-publication_date", "-created_at"]},
            bases=("core.post",),
        ),
        migrations.AddField(
            model_name="player",
            name="post",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="player",
                to="core.localpost",
            ),
        ),
        migrations.RunPython(create_local_posts, restore_player_collections),
        migrations.AlterField(
            model_name="player",
            name="post",
            field=models.OneToOneField(
                blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="player",
                to="core.localpost",
            ),
        ),
        migrations.RemoveField(model_name="player", name="sounds"),
        migrations.RunPython(preserve_player_editor_permissions, remove_local_post_permissions),
    ]
