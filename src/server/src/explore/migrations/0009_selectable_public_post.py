import uuid
import django.db.models.deletion
from django.conf import settings
from django.core.management.color import no_style
from django.db import migrations, models


def copy_links(apps, schema_editor):
    alias = schema_editor.connection.alias
    Old = apps.get_model('explore', 'PublicPost')
    New = apps.get_model('explore', 'PublicPostLink')
    New.objects.using(alias).bulk_create([
        New(id=row.pk, post_id=row.pk, cosound_id=row.cosound_id, slug=row.slug)
        for row in Old.objects.using(alias).all()
    ])
    for sql in schema_editor.connection.ops.sequence_reset_sql(no_style(), [New]):
        schema_editor.execute(sql)
    schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE')


def preserve_writing_permissions(apps, schema_editor):
    """Existing writing editors retain access to the newly separate editor."""
    alias = schema_editor.connection.alias
    ContentType = apps.get_model('contenttypes', 'ContentType')
    Permission = apps.get_model('auth', 'Permission')
    post_type, _ = ContentType.objects.using(alias).get_or_create(app_label='core', model='post')
    for action in ('add', 'change', 'view', 'delete'):
        target, _ = Permission.objects.using(alias).get_or_create(
            content_type=post_type, codename=f'{action}_post',
            defaults={'name': f'Can {action} post'},
        )
        sources = Permission.objects.using(alias).filter(
            models.Q(content_type__app_label='core', content_type__model='localpost') |
            models.Q(content_type__app_label='explore', content_type__model='publicpost'),
            codename__startswith=f'{action}_',
        )
        for Owner, field_name in ((apps.get_model(settings.AUTH_USER_MODEL), 'user_permissions'), (apps.get_model('auth', 'Group'), 'permissions')):
            field = Owner._meta.get_field(field_name)
            Through = field.remote_field.through
            owner_column = f'{field.m2m_field_name()}_id'
            owners = Through.objects.using(alias).filter(permission_id__in=sources).values_list(owner_column, flat=True).distinct()
            Through.objects.using(alias).bulk_create([
                Through(**{owner_column: owner_id, 'permission_id': target.pk}) for owner_id in owners
            ], ignore_conflicts=True)


def check_reversible(apps, schema_editor):
    from django.db.migrations.exceptions import IrreversibleError
    PublicPost = apps.get_model('explore', 'PublicPost')
    if PublicPost.objects.using(schema_editor.connection.alias).exclude(id=models.F('post_id'), slug=models.F('post__slug')).exists():
        raise IrreversibleError('Posts have been reassigned; restore a backup to return to inheritance.')


def restore_links(apps, schema_editor):
    schema_editor.execute('INSERT INTO explore_publicpost (post_id, cosound_id) SELECT post_id, cosound_id FROM explore_publicpostlink')
    schema_editor.execute('SET CONSTRAINTS ALL IMMEDIATE')


class Migration(migrations.Migration):
    dependencies = [('explore', '0008_publicpost_shared_content'), ('core', '0006_selectable_local_post')]
    operations = [
        migrations.CreateModel(
            name='PublicPostLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('post', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='public_posts', to='core.post')),
                ('slug', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('cosound', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='public_posts', to='core.cosound', verbose_name='featured cosound')),
            ],
            options={'ordering': ['-post__publication_date', '-post__created_at']},
        ),
        migrations.RunPython(copy_links, migrations.RunPython.noop),
        migrations.RunPython(migrations.RunPython.noop, restore_links),
        migrations.DeleteModel(name='PublicPost'),
        migrations.RenameModel(old_name='PublicPostLink', new_name='PublicPost'),
        migrations.RunPython(preserve_writing_permissions, migrations.RunPython.noop),
        # Reverting shared associations to inheritance would lose data.
        migrations.RunPython(migrations.RunPython.noop, check_reversible),
    ]
