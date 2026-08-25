import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models

import explore.models


def delete_existing_posts(apps, schema_editor):
    apps.get_model("explore", "Post").objects.all().delete()


class Migration(migrations.Migration):
    # The cleanup must commit before PostgreSQL can add the required composer
    # foreign key; otherwise cascaded comment deletes leave pending triggers on
    # explore_post for the remainder of one atomic migration transaction.
    atomic = False

    dependencies = [
        ("explore", "0003_comment"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RenameField(
            model_name="post",
            old_name="body",
            new_name="article",
        ),
        migrations.AddField(
            model_name="post",
            name="announcers_call",
            field=models.CharField(default="Presenting", max_length=100),
        ),
        migrations.AddField(
            model_name="post",
            name="authors",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='A list of objects with "name", "role", and an optional "url".',
                validators=[explore.models.validate_authors],
            ),
        ),
        migrations.RunPython(delete_existing_posts, migrations.RunPython.noop),
        migrations.AddField(
            model_name="post",
            name="composer",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="composed_explore_posts",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="post",
            name="farewell_style",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AddField(
            model_name="post",
            name="greeting_style",
            field=models.CharField(default="Dear Listener", max_length=100),
        ),
        migrations.AlterField(
            model_name="post",
            name="cosound",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="explore_posts",
                to="core.cosound",
                verbose_name="featured cosound",
            ),
        ),
    ]
