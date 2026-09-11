from unittest.mock import patch

from django.db import transaction
from django.test import TestCase

from core.models import Cosound, Listener, Manager, Player, Sound, User
from vote.models import Vote


class VoteNotificationTests(TestCase):
    def setUp(self):
        user = User.objects.create_user(username="vote-chime", email="vote-chime@example.com")
        manager = Manager.objects.create(user=user, name="Manager")
        self.player = Player.objects.create(manager=manager, name="Room")
        self.listener = Listener.objects.create(user=user)
        sound = Sound.objects.create(title="Rain", file="sounds/rain.wav", embeddings=[0] * 5)
        self.cosound = Cosound.get_or_create_from_layers([(sound.pk, 1.0)])

    def vote(self):
        return Vote.objects.create(player=self.player, voter=self.listener, cosound=self.cosound, value=1)

    @patch("core.player_events.publish_player_vote")
    def test_new_vote_notifies_only_after_commit_and_edits_do_not_replay(self, publish):
        with self.captureOnCommitCallbacks(execute=True):
            vote = self.vote()
            publish.assert_not_called()
        publish.assert_called_once_with(self.player.pk, vote.pk)
        publish.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            vote.section = "Updated"
            vote.save()
        publish.assert_not_called()

    @patch("core.player_events.publish_player_vote")
    def test_rolled_back_vote_never_chimes(self, publish):
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    self.vote()
                    raise ValueError("rollback")
            except ValueError:
                pass
        publish.assert_not_called()
