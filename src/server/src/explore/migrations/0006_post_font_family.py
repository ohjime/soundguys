from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("explore", "0005_simplify_post_publication"),
    ]

    operations = [
        migrations.AddField(
            model_name="post",
            name="font_family",
            field=models.CharField(
                blank=True,
                default="dancing-script",
                help_text="Typography available to selected elements in the post template.",
                max_length=100,
            ),
        ),
    ]
