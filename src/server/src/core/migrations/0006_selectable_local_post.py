import django.db.models.deletion
from django.core.management.color import no_style
from django.db import migrations, models


def copy_links(apps, schema_editor):
    alias = schema_editor.connection.alias
    Old = apps.get_model('core', 'LocalPost')
    New = apps.get_model('core', 'LocalPostLink')
    New.objects.using(alias).bulk_create([
        New(id=pk, post_id=pk) for pk in Old.objects.using(alias).values_list('pk', flat=True)
    ])
    old_collection = Old._meta.get_field('collection').remote_field.through
    new_collection = New._meta.get_field('collection').remote_field.through
    new_collection.objects.using(alias).bulk_create([
        new_collection(localpostlink_id=local_id, sound_id=sound_id)
        for local_id, sound_id in old_collection.objects.using(alias).values_list('localpost_id', 'sound_id')
    ])
    for sql in schema_editor.connection.ops.sequence_reset_sql(no_style(), [New]):
        schema_editor.execute(sql)
    schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE')


def check_reversible(apps, schema_editor):
    from django.db.migrations.exceptions import IrreversibleError
    LocalPost = apps.get_model('core', 'LocalPost')
    if LocalPost.objects.using(schema_editor.connection.alias).exclude(id=models.F('post_id')).exists():
        raise IrreversibleError('Posts have been reassigned; restore a backup to return to inheritance.')


def restore_links(apps, schema_editor):
    schema_editor.execute('INSERT INTO core_localpost (post_id) SELECT post_id FROM core_localpostlink')
    schema_editor.execute('INSERT INTO core_localpost_collection (localpost_id, sound_id) SELECT localpostlink_id, sound_id FROM core_localpostlink_collection')
    schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE')


class Migration(migrations.Migration):
    dependencies = [('core', '0005_localpost_player_post')]
    operations = [
        migrations.CreateModel(
            name='LocalPostLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('post', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='local_posts', to='core.post')),
                ('collection', models.ManyToManyField(blank=True, to='core.sound')),
            ],
            options={'ordering': ['-post__publication_date', '-post__created_at']},
        ),
        migrations.RunPython(copy_links, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='player', name='post',
            field=models.OneToOneField(blank=True, on_delete=django.db.models.deletion.PROTECT, related_name='player', to='core.localpostlink'),
        ),
        migrations.RunPython(migrations.RunPython.noop, restore_links),
        migrations.DeleteModel(name='LocalPost'),
        migrations.RenameModel(old_name='LocalPostLink', new_name='LocalPost'),
        # Swappable/shared links cannot be folded back into inheritance without
        # losing identity or duplicating writing. Restore a backup to roll back.
        migrations.RunPython(migrations.RunPython.noop, check_reversible),
    ]
