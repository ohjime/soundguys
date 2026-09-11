from django.db import migrations, models


def sync_sleeping_with_playback(apps, schema_editor):
    Player = apps.get_model("core", "Player")
    alias = schema_editor.connection.alias

    for player in Player.objects.using(alias).only("pk", "playing").iterator():
        playing = player.playing or {}
        layers = (
            playing.get("layers", [])
            if isinstance(playing, dict)
            else getattr(playing, "layers", [])
        )
        Player.objects.using(alias).filter(pk=player.pk).update(
            sleeping=not bool(layers)
        )


class Migration(migrations.Migration):
    dependencies = [("core", "0006_selectable_local_post")]

    operations = [
        migrations.AddField(
            model_name="player",
            name="sleeping",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="player",
            name="activated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(
            sync_sleeping_with_playback,
            migrations.RunPython.noop,
        ),
    ]
