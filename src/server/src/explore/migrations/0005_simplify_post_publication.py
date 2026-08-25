import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("explore", "0004_post_article_metadata"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="post",
            name="excerpt",
        ),
        migrations.RemoveField(
            model_name="post",
            name="is_published",
        ),
        migrations.RenameField(
            model_name="post",
            old_name="published_at",
            new_name="publication_date",
        ),
        migrations.AlterField(
            model_name="post",
            name="slug",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AlterModelOptions(
            name="post",
            options={"ordering": ["-publication_date", "-created_at"]},
        ),
    ]
