from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class PostLinkMigrationTests(TransactionTestCase):
    before = [('core', '0005_localpost_player_post'), ('explore', '0008_publicpost_shared_content')]
    after = [('core', '0006_selectable_local_post'), ('explore', '0009_selectable_public_post')]

    def migrate(self, targets):
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        return executor.loader.project_state(targets).apps

    def setUp(self):
        super().setUp()
        apps = self.migrate(self.before)
        User = apps.get_model('core', 'User')
        Post = apps.get_model('core', 'Post')
        self.user = User.objects.create(username='link-migration', email='links@example.com')
        manager = apps.get_model('core', 'Manager').objects.create(user=self.user, name='Manager')
        sound = apps.get_model('core', 'Sound').objects.create(title='Sound', file='sounds/a.wav', embeddings=[0] * 5)
        # Gaps and independent writing ensure wrapper IDs are not renumbered.
        Post.objects.create(title='Unattached', composer=self.user)
        local = apps.get_model('core', 'LocalPost').objects.create(title='Local writing', article='Markdown', composer=self.user, publication_date=timezone.now())
        local.collection.add(sound)
        player = apps.get_model('core', 'Player').objects.create(name='Player', manager=manager, post=local, token='migration-player-token', playing={'layers': []})
        public = apps.get_model('explore', 'PublicPost').objects.create(title='Public writing', composer=self.user, publication_date=timezone.now())
        for post in (local, public):
            apps.get_model('core', 'Comment').objects.create(post_id=post.pk, user=self.user, body=f'Comment {post.pk}')
        self.local_id, self.public_id, self.player_id = local.pk, public.pk, player.pk
        self.public_slug = public.slug
        self.sound_id = sound.pk
        self.posts = list(Post.objects.order_by('pk').values())
        self.comments = list(apps.get_model('core', 'Comment').objects.order_by('pk').values())
        self.player = apps.get_model('core', 'Player').objects.values().get(pk=player.pk)
        Permission = apps.get_model('auth', 'Permission')
        ContentType = apps.get_model('contenttypes', 'ContentType')
        source_type, _ = ContentType.objects.get_or_create(app_label='explore', model='publicpost')
        source, _ = Permission.objects.get_or_create(content_type=source_type, codename='change_publicpost', defaults={'name': 'Can change public post'})
        self.user.user_permissions.add(source)
        group = apps.get_model('auth', 'Group').objects.create(name='Writing editors')
        group.permissions.add(source)
        self.group_id = group.pk

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def assert_preserved(self, apps):
        Post = apps.get_model('core', 'Post')
        self.assertEqual(list(Post.objects.order_by('pk').values()), self.posts)
        self.assertEqual(list(apps.get_model('core', 'Comment').objects.order_by('pk').values()), self.comments)
        self.assertEqual(apps.get_model('core', 'Player').objects.values().get(pk=self.player_id), self.player)
        local = apps.get_model('core', 'LocalPost').objects.get(pk=self.local_id)
        public = apps.get_model('explore', 'PublicPost').objects.get(pk=self.public_id)
        self.assertEqual(local.post_id, self.local_id)
        self.assertEqual(public.post_id, self.public_id)
        self.assertEqual(public.slug, self.public_slug)
        self.assertEqual(list(local.collection.values_list('pk', flat=True)), [self.sound_id])

    def test_data_and_permissions_survive_conversion_and_unchanged_round_trip(self):
        apps = self.migrate(self.after)
        self.assert_preserved(apps)
        user = apps.get_model('core', 'User').objects.get(pk=self.user.pk)
        group = apps.get_model('auth', 'Group').objects.get(pk=self.group_id)
        self.assertTrue(user.user_permissions.filter(codename='change_post', content_type__app_label='core').exists())
        self.assertTrue(group.permissions.filter(codename='change_post', content_type__app_label='core').exists())
        self.migrate(self.before)
        self.assert_preserved(self.migrate(self.after))
        # Independent sequence values must continue above preserved IDs.
        shared = apps.get_model('core', 'Post').objects.first()
        local = apps.get_model('core', 'LocalPost').objects.create(post=shared)
        public = apps.get_model('explore', 'PublicPost').objects.create(post=shared)
        self.assertGreater(local.pk, self.local_id)
        self.assertGreater(public.pk, self.public_id)
