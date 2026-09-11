from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Comment, LocalPost, Manager, Player, Post, Prediction, Sound, User


class LocalPostModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user(username="local-manager", email="local@example.com")
        cls.manager = Manager.objects.create(user=cls.user, name="Local manager")

    def make_player(self, **fields):
        return Player.objects.create(manager=self.manager, name="Local player", **fields)

    def test_new_player_creates_its_own_published_writing(self):
        player = self.make_player(bio="The existing player description")
        post = player.post
        self.assertEqual(post.post.title, player.name)
        self.assertEqual(post.post.article, player.bio)
        self.assertEqual(post.post.composer, self.user)
        self.assertIsNotNone(post.post.publication_date)
        self.assertEqual(post.post.local_posts.get(), post)
        self.assertEqual(post.player, player)
        self.assertFalse(post.collection.exists())
        self.assertTrue(player.sleeping)
        self.assertIsNone(player.activated_at)
        self.assertEqual(Post.objects.count(), 1)
        self.assertEqual(len(player.token), 64)

    def test_long_player_names_fit_the_shared_post_title(self):
        player = Player.objects.create(manager=self.manager, name="A" * 255)
        self.assertEqual(player.post.post.title, player.name)
        player.post.full_clean()

    def test_explicit_local_post_keeps_its_writing_and_draft_state(self):
        post = LocalPost.objects.create(post=Post.objects.create(
            composer=self.user, title="A deliberate title", article="A longer article"
        ))
        player = self.make_player(post=post, bio="Player description")
        self.assertEqual(player.post_id, post.pk)
        self.assertIsNone(player.post.post.publication_date)
        self.assertEqual(player.post.post.article, "A longer article")
        self.assertEqual(LocalPost.objects.count(), 1)

    def test_player_updates_do_not_overwrite_the_post(self):
        player = self.make_player(bio="Original description")
        post = player.post
        timestamp = post.post.updated_at
        player.name = "Changed player name"
        player.bio = "Changed player description"
        player.save(update_fields=["name", "bio"])
        player.update(Prediction.new())
        post.refresh_from_db()
        self.assertEqual(post.post.title, "Local player")
        self.assertEqual(post.post.article, "Original description")
        self.assertEqual(post.post.updated_at, timestamp)
        self.assertEqual(LocalPost.objects.count(), 1)

    def test_playback_updates_keep_sleeping_state_in_sync(self):
        sound = Sound.objects.create(
            file="sounds/awake.mp3",
            title="Awake",
            embeddings=[0] * 5,
        )
        player = self.make_player()
        prediction = Prediction.new()
        prediction.add_layer(sound.pk)

        player.update(prediction)
        player.refresh_from_db()
        self.assertFalse(player.sleeping)

        player.activated_at = timezone.now()
        player.save(update_fields=["activated_at"])
        player.update(Prediction.new())
        player.refresh_from_db()
        self.assertTrue(player.sleeping)
        self.assertIsNone(player.activated_at)

    def test_failed_player_creation_rolls_back_its_post_and_can_retry(self):
        existing = self.make_player()
        player = Player(manager=self.manager, name="Duplicate", token=existing.token)
        with self.assertRaises(IntegrityError):
            player.save(using="default")
        self.assertEqual(LocalPost.objects.count(), 1)
        self.assertEqual(Post.objects.count(), 1)
        self.assertIsNone(player.post_id)
        player.token = "replacement-token"
        player.save(using="default")
        self.assertEqual(LocalPost.objects.count(), 2)
        self.assertEqual(Player.objects.get(pk=player.pk).post_id, player.post_id)

    def test_empty_update_fields_does_not_create_a_post(self):
        player = Player(manager=self.manager, name="Not saved")
        player.save(update_fields=[])
        self.assertIsNone(player.pk)
        self.assertFalse(LocalPost.objects.exists())

    def test_explicit_unsaved_post_is_not_replaced_with_default_writing(self):
        post = LocalPost(post=Post.objects.create(composer=self.user, title="Unsaved writing"))
        player = Player(manager=self.manager, name="Player", post=post)
        with self.assertRaisesMessage(ValueError, "unsaved related object 'post'"):
            player.save()
        self.assertEqual(player.post, post)
        self.assertFalse(LocalPost.objects.exists())

    def test_missing_post_is_saved_with_partial_player_update(self):
        player = self.make_player()
        previous_post = player.post
        player.post = None
        player.name = "A replacement post"
        player.save(update_fields=["name"])
        player.refresh_from_db()
        self.assertNotEqual(player.post_id, previous_post.pk)
        self.assertEqual(player.post.post.title, "A replacement post")

    def test_post_and_player_relationship_is_required_unique_and_protected(self):
        player = self.make_player()
        with self.assertRaises(ProtectedError):
            player.post.delete()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.make_player(post=player.post)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Player.objects.bulk_create([
                Player(manager=self.manager, name="No post", token="no-post-token")
            ])

    def test_comments_reference_the_shared_post(self):
        player = self.make_player()
        comment = Comment.objects.create(post=player.post.post, user=self.user, body="A local response")
        self.assertEqual(comment.post_id, player.post.post_id)
        self.assertEqual(player.post.post.comments.get(), comment)
        self.assertEqual(player.post.post.comments.get(), comment)

    def test_library_and_links_follow_the_current_local_post(self):
        player = self.make_player()
        first_post = player.post
        sound = Sound.objects.create(file="sounds/local.mp3", title="Local", embeddings=[0] * 5)
        post = LocalPost.objects.create(post=Post.objects.create(composer=self.user, title="Next collection"))
        post.collection.add(sound)
        self.assertEqual(post.get_absolute_url(), reverse("vote:vote"))
        player.post = post
        player.save(update_fields=["post"])
        self.assertEqual(player.library(), [sound])
        self.assertEqual(post.get_absolute_url(), reverse("vote:vote", query={"player": player.token}))
        first_post.refresh_from_db()
        self.assertEqual(first_post.get_absolute_url(), reverse("vote:vote"))

    def test_link_reverses_against_main_urls_from_the_admin_host(self):
        player = self.make_player()
        with self.settings(ROOT_URLCONF="config.urls_admin"):
            self.assertEqual(player.post.get_absolute_url(), f"/vote/?player={player.token}")
