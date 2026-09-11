import django.db.models.deletion
from django.conf import settings
from django.core.management.color import no_style
from django.db import migrations, models


def copy_rows(schema_editor, source, target, columns):
    """Copy in SQL so auto_now/auto_now_add cannot rewrite historical dates."""
    quote = schema_editor.quote_name
    names = ", ".join(quote(column) for column in columns)
    schema_editor.execute(
        f"INSERT INTO {quote(target._meta.db_table)} ({names}) "
        f"SELECT {names} FROM {quote(source._meta.db_table)}"
    )


def reset_sequences(schema_editor, model_list):
    for statement in schema_editor.connection.ops.sequence_reset_sql(no_style(), model_list):
        schema_editor.execute(statement)


def copy_permission_grants(apps, alias, source, target):
    """Keep both group and individual grants when permissions move or merge."""
    User = apps.get_model(settings.AUTH_USER_MODEL)
    Group = apps.get_model("auth", "Group")
    for model, field_name in ((User, "user_permissions"), (Group, "permissions")):
        field = model._meta.get_field(field_name)
        through = field.remote_field.through
        owner_id = f"{field.m2m_field_name()}_id"
        owners = through.objects.using(alias).filter(permission_id=source.pk).values_list(
            owner_id, flat=True
        )
        through.objects.using(alias).bulk_create(
            [through(**{owner_id: owner, "permission_id": target.pk}) for owner in owners],
            ignore_conflicts=True,
        )


def move_content_type(apps, alias, old_app, old_model, new_app, new_model):
    """Retain content-type/permission IDs, including admin history and grants.

    A target can already exist if core migrations were applied separately and
    post_migrate created permissions. Merge that empty/new identity into the
    established one before renaming it.
    """
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    content_type = ContentType.objects.using(alias).filter(
        app_label=old_app, model=old_model
    ).first()
    if content_type is None:
        return None
    target = ContentType.objects.using(alias).filter(
        app_label=new_app, model=new_model
    ).first()
    if target and target.pk != content_type.pk:
        for permission in Permission.objects.using(alias).filter(content_type=target):
            codename = permission.codename
            for action in ("add", "change", "delete", "view"):
                if codename == f"{action}_{new_model}":
                    codename = f"{action}_{old_model}"
                    break
            established = Permission.objects.using(alias).filter(
                content_type=content_type, codename=codename
            ).first()
            if established:
                copy_permission_grants(apps, alias, permission, established)
                permission.delete(using=alias)
            else:
                permission.content_type = content_type
                permission.codename = codename
                permission.save(using=alias, update_fields=["content_type", "codename"])
        # This also preserves references such as admin log entries and generic
        # relation content-type columns supplied by installed applications.
        for model in apps.get_models():
            for field in model._meta.local_fields:
                if field.is_relation and field.related_model == ContentType:
                    model.objects.using(alias).filter(**{field.attname: target.pk}).update(
                        **{field.attname: content_type.pk}
                    )
        target.delete(using=alias)
    content_type.app_label = new_app
    content_type.model = new_model
    content_type.save(using=alias, update_fields=["app_label", "model"])
    for action in ("add", "change", "delete", "view"):
        Permission.objects.using(alias).filter(
            content_type=content_type, codename=f"{action}_{old_model}"
        ).update(
            codename=f"{action}_{new_model}",
            name=f"Can {action} {'public post' if new_model == 'publicpost' else new_model}",
        )
    return content_type


def copy_post_permissions(apps, alias, source):
    if source is None:
        return
    ContentType = apps.get_model("contenttypes", "ContentType")
    Permission = apps.get_model("auth", "Permission")
    target, _ = ContentType.objects.using(alias).get_or_create(app_label="core", model="post")
    for action in ("add", "change", "delete", "view"):
        source_permission = Permission.objects.using(alias).filter(
            content_type=source, codename=f"{action}_publicpost"
        ).first()
        if source_permission:
            target_permission, _ = Permission.objects.using(alias).get_or_create(
                content_type=target,
                codename=f"{action}_post",
                defaults={"name": f"Can {action} post"},
            )
            copy_permission_grants(apps, alias, source_permission, target_permission)


def move_content_forward(apps, schema_editor):
    Post = apps.get_model("core", "Post")
    Comment = apps.get_model("core", "Comment")
    ExplorePost = apps.get_model("explore", "Post")
    ExploreComment = apps.get_model("explore", "Comment")
    PublicPost = apps.get_model("explore", "PublicPost")
    copy_rows(schema_editor, ExplorePost, Post, [field.column for field in Post._meta.local_fields])
    copy_rows(
        schema_editor, ExploreComment, Comment,
        [field.column for field in Comment._meta.local_fields],
    )
    quote = schema_editor.quote_name
    schema_editor.execute(
        f"INSERT INTO {quote(PublicPost._meta.db_table)} (post_id, cosound_id) "
        f"SELECT id, cosound_id FROM {quote(ExplorePost._meta.db_table)}"
    )
    reset_sequences(schema_editor, [Post, Comment])
    alias = schema_editor.connection.alias
    public_type = move_content_type(apps, alias, "explore", "post", "explore", "publicpost")
    move_content_type(apps, alias, "explore", "comment", "core", "comment")
    # Django's admin checks delete permissions for both multi-table models.
    copy_post_permissions(apps, alias, public_type)


def move_content_backward(apps, schema_editor):
    Post = apps.get_model("core", "Post")
    Comment = apps.get_model("core", "Comment")
    ExplorePost = apps.get_model("explore", "Post")
    ExploreComment = apps.get_model("explore", "Comment")
    PublicPost = apps.get_model("explore", "PublicPost")
    quote = schema_editor.quote_name
    columns = [field.column for field in Post._meta.local_fields]
    names = ", ".join(quote(column) for column in columns)
    selected = ", ".join(f"base.{quote(column)}" for column in columns)
    # Include base-only posts without a cosound, retaining all shared
    # content and comments even if some were added after the refactor.
    schema_editor.execute(
        f"INSERT INTO {quote(ExplorePost._meta.db_table)} ({names}, cosound_id) "
        f"SELECT {selected}, public.cosound_id FROM {quote(Post._meta.db_table)} base "
        f"LEFT JOIN {quote(PublicPost._meta.db_table)} public ON public.post_id = base.id"
    )
    copy_rows(
        schema_editor, Comment, ExploreComment,
        [field.column for field in Comment._meta.local_fields],
    )
    reset_sequences(schema_editor, [ExplorePost, ExploreComment])
    alias = schema_editor.connection.alias
    move_content_type(apps, alias, "explore", "publicpost", "explore", "post")
    move_content_type(apps, alias, "core", "comment", "explore", "comment")
    # Merge shared-post grants/history while retaining the original Explore
    # content-type and permission IDs, rather than replacing them with core's.
    move_content_type(apps, alias, "explore", "post", "core", "post")
    move_content_type(apps, alias, "core", "post", "explore", "post")
    # A rollback targeting only explore leaves core.0003 applied. Empty the
    # moved tables so applying this migration again copies the current legacy
    # content, without conflicting IDs or resurrecting subsequently deleted rows.
    for model in (PublicPost, Comment, Post):
        schema_editor.execute(f"DELETE FROM {quote(model._meta.db_table)}")
    if schema_editor.connection.vendor == "postgresql":
        # Finish deferred FK checks before DeleteModel drops the emptied child.
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_post_comment"),
        ("explore", "0007_remove_post_farewell_style"),
    ]

    operations = [
        migrations.CreateModel(
            name="PublicPost",
            fields=[
                (
                    "post",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        parent_link=True,
                        primary_key=True,
                        related_name="public_post",
                        serialize=False,
                        to="core.post",
                    ),
                ),
                (
                    "cosound",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="public_posts",
                        to="core.cosound",
                        verbose_name="featured cosound",
                    ),
                ),
            ],
            options={"ordering": ["-publication_date", "-created_at"]},
            bases=("core.post",),
        ),
        migrations.RunPython(move_content_forward, move_content_backward),
        migrations.DeleteModel(name="Comment"),
        migrations.DeleteModel(name="Post"),
    ]
