from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("explore", "0006_post_font_family"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="post",
            name="farewell_style",
        ),
    ]
