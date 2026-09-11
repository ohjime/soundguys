from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0003_post_comment"),
        ("explore", "0008_publicpost_shared_content"),
    ]

    operations = [
        # Player names already support 255 characters. Keep them intact when
        # they become post titles, and allow the LocalPost feature to be rolled
        # back independently of this shared field's expanded capacity.
        migrations.AlterField(
            model_name="post",
            name="title",
            field=models.CharField(max_length=255),
        ),
    ]
