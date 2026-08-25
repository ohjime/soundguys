from django.core.management.color import no_style
from django.db import migrations


LEGACY_TABLE = "featured_featuredpost"


def copy_featured_posts(apps, schema_editor):
    connection = schema_editor.connection
    if LEGACY_TABLE not in connection.introspection.table_names():
        return

    Post = apps.get_model("explore", "Post")
    quote_name = connection.ops.quote_name
    columns = [
        "id",
        "title",
        "slug",
        "excerpt",
        "body",
        "is_published",
        "published_at",
        "created_at",
        "updated_at",
        "cosound_id",
    ]
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT {', '.join(quote_name(column) for column in columns)} "
            f"FROM {quote_name(LEGACY_TABLE)}"
        )
        rows = cursor.fetchall()

    Post.objects.bulk_create(
        [Post(**dict(zip(columns, row, strict=True))) for row in rows],
        ignore_conflicts=True,
    )

    # Explicit primary keys do not advance PostgreSQL's sequence. Reset it so
    # the next Post created through Django cannot reuse a migrated ID.
    with connection.cursor() as cursor:
        for statement in connection.ops.sequence_reset_sql(no_style(), [Post]):
            cursor.execute(statement)


class Migration(migrations.Migration):
    dependencies = [
        ("explore", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(copy_featured_posts, migrations.RunPython.noop),
    ]
