from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Comment, Cosound, LocalPost, Manager, Player, Post, Sound, User
from explore.models import PublicPost


class SelectablePostTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_superuser(username='post-selector', email='selector@example.com', password='pw')
        cls.manager = Manager.objects.create(user=cls.user, name='Manager')
        cls.player = Player.objects.create(name='Room', manager=cls.manager)
        cls.local = cls.player.post
        cls.original = cls.local.post
        cls.replacement = Post.objects.create(title='Replacement writing', article='Replacement **article**', composer=cls.user, publication_date=timezone.now())
        cls.sound = Sound.objects.create(title='Room sound', file='sounds/room.wav', embeddings=[0] * 5)
        cls.local.collection.add(cls.sound)
        cls.mix = Cosound.get_or_create_from_layers([(cls.sound.pk, 0.4)])
        cls.public = PublicPost.objects.create(post=cls.original, cosound=cls.mix)
        cls.original_comment = Comment.objects.create(post=cls.original, user=cls.user, body='Original discussion')
        cls.replacement_comment = Comment.objects.create(post=cls.replacement, user=cls.user, body='Replacement discussion')

    def setUp(self):
        self.client.force_login(self.user)

    def test_local_admin_swaps_shared_post_and_keeps_player_collection_and_discussions(self):
        local_id = self.local.pk
        for shared in (self.replacement, self.original):
            response = self.client.post(reverse('admin:core_localpost_change', args=[local_id]), {'post': shared.pk, 'collection': [self.sound.pk]})
            self.assertEqual(response.status_code, 302)
            self.player.refresh_from_db()
            self.assertEqual(self.player.post_id, local_id)
            self.assertEqual(self.player.post.post, shared)
            self.assertEqual(self.player.library(), [self.sound])
            page = self.client.get(reverse('vote:vote'), {'player': self.player.token})
            self.assertContains(page, shared.title)
            self.assertContains(page, shared.comments.get().body)
        self.assertEqual(Comment.objects.count(), 2)

    def test_public_admin_swaps_shared_post_and_keeps_mix_url_and_discussions(self):
        public_id, url = self.public.pk, self.public.get_absolute_url()
        for shared in (self.replacement, self.original):
            response = self.client.post(reverse('admin:explore_publicpost_change', args=[public_id]), {'post': shared.pk, 'cosound': self.mix.pk})
            self.assertEqual(response.status_code, 302)
            self.public.refresh_from_db()
            self.assertEqual(self.public.post, shared)
            self.assertEqual(self.public.cosound_id, self.mix.pk)
            self.assertEqual(self.public.get_absolute_url(), url)
            response = self.client.get(url, HTTP_HX_REQUEST='true')
            self.assertContains(response, shared.title)
            self.assertContains(response, shared.comments.get().body)
        self.assertEqual(Comment.objects.count(), 2)

    def test_post_selectors_include_attached_and_unattached_posts(self):
        for app_label, model_name in [('core', 'localpost'), ('explore', 'publicpost')]:
            response = self.client.get(reverse('admin:autocomplete'), {'app_label': app_label, 'model_name': model_name, 'field_name': 'post'})
            self.assertEqual(response.status_code, 200)
            self.assertEqual({int(row['id']) for row in response.json()['results']}, {self.original.pk, self.replacement.pk})
            response = self.client.get(reverse(f'admin:{app_label}_{model_name}_change', args=[self.local.pk if app_label == 'core' else self.public.pk]))
            self.assertContains(response, 'id="id_post"')
            self.assertContains(response, reverse('admin:core_post_change', args=[self.original.pk]))
        self.assertContains(self.client.get(reverse('admin:core_post_changelist')), self.replacement.title)

    def test_deleting_wrapper_preserves_writing_and_attached_writing_is_protected(self):
        with self.assertRaises(ProtectedError):
            self.original.delete()
        self.public.delete()
        self.assertTrue(Post.objects.filter(pk=self.original.pk).exists())
        self.assertTrue(Comment.objects.filter(pk=self.original_comment.pk).exists())

    def test_stale_comment_forms_cannot_land_on_replacement_writing(self):
        local_url = reverse('vote:create_comment', kwargs={'slug': self.original.slug}) + f'?player={self.player.token}'
        public_url = reverse('explore:create_comment', kwargs={'slug': self.public.slug}) + f'?post={self.original.slug}'
        self.local.post = self.replacement
        self.local.save(update_fields=['post'])
        self.public.post = self.replacement
        self.public.save(update_fields=['post'])
        self.assertEqual(self.client.post(local_url, {'body': 'Stale local response'}).status_code, 404)
        self.assertEqual(self.client.post(public_url, {'body': 'Stale public response'}).status_code, 409)
        self.assertEqual(Comment.objects.count(), 2)
