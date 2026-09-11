from django.test import RequestFactory, TestCase

from app.api import get_manifest
from core.models import LocalPost, Manager, Player, Sound, User, Post


class PlayerManifestCollectionTests(TestCase):
    def test_manifest_follows_the_players_current_local_post_collection(self):
        user = User.objects.create_user(username="manifest-manager", email="manifest@example.com")
        manager = Manager.objects.create(user=user, name="Manager")
        player = Player.objects.create(manager=manager, name="Player")
        old_sound, new_sound = [
            Sound.objects.create(title=name, file=f"sounds/{name}.wav", embeddings=[0.0] * 5)
            for name in ("old", "new")
        ]
        player.post.collection.add(old_sound)
        replacement = LocalPost.objects.create(post=Post.objects.create(title="New collection", composer=user))
        replacement.collection.add(new_sound)
        player.post = replacement
        player.save(update_fields=["post"])
        request = RequestFactory().get("/api/manifest")
        request.auth = player
        manifest = get_manifest(request)
        self.assertEqual(set(manifest), {str(new_sound.pk)})
        self.assertTrue(urlsplit(manifest[str(new_sound.pk)]).path.endswith("/sounds/new.wav"))
from urllib.parse import urlsplit
