import json
import threading
import unittest
from collections import deque
from unittest.mock import Mock

import numpy as np

from app.chime import vote_chime
from app.live import _receive_changes
from app.player import SoundDevicePlayer


class VoteEventsTests(unittest.IsolatedAsyncioTestCase):
    async def test_votes_chime_once_across_connections_without_refreshing_the_mix(self):
        class Socket:
            async def recv(self):
                return json.dumps({"type": "player.ready", "schema_version": 1})

            async def __aiter__(self):
                for vote_id in (1, 1, True, None, -1, "2", 2):
                    yield json.dumps({"type": "player.vote_received", "schema_version": 1, "vote_id": vote_id})

        chime, refresh, status = Mock(), Mock(), Mock()
        seen = deque(maxlen=512)
        for _ in range(2):
            await _receive_changes(Socket(), refresh, status, chime, seen)
        self.assertEqual(chime.call_count, 2)
        self.assertEqual(refresh.call_count, 2)  # Ready only, never a vote.


class ChimeAudioTests(unittest.TestCase):
    def player(self):
        player = SoundDevicePlayer.__new__(SoundDevicePlayer)
        player.lock = threading.Lock()
        player.channels = 2
        player.master_gain = 0.7
        player.muted = False
        player.active_tracks = {}
        player.pending_queue = {"current-mix.wav": 0.5}
        player._vote_chime = vote_chime(48000)
        player._vote_chime_positions = []
        player.renderer = Mock()
        player.renderer.render.side_effect = lambda sources, frames: (np.zeros((frames, 2), np.float32), np.zeros((frames, 2), np.float32))
        player.reverb = Mock()
        player.reverb.process.side_effect = lambda send: np.zeros_like(send)
        return player

    def test_one_shot_finishes_without_altering_mix_or_looping(self):
        player = self.player()
        player.play_vote_chime()
        frames = len(player._vote_chime) + 32
        output = np.empty((frames, 2), np.float32)
        player._audio_callback(output, frames, None, None)
        self.assertGreater(np.max(np.abs(output)), 0.01)
        self.assertTrue(np.isfinite(output).all())
        self.assertEqual(player.pending_queue, {"current-mix.wav": 0.5})
        self.assertEqual(player.active_tracks, {})
        player._audio_callback(output, frames, None, None)
        np.testing.assert_array_equal(output, 0)

    def test_mute_and_zero_volume_apply_to_chime_and_consume_it(self):
        for muted, gain in ((True, 0.7), (False, 0.0)):
            player = self.player()
            player.muted, player.master_gain = muted, gain
            player.play_vote_chime()
            output = np.empty((24000, 2), np.float32)
            player._audio_callback(output, 24000, None, None)
            np.testing.assert_array_equal(output, 0)
            self.assertEqual(player._vote_chime_positions, [])

    def test_bursts_are_bounded_and_new_taps_start_immediately(self):
        player = self.player()
        for _ in range(100):
            player.play_vote_chime()
        self.assertEqual(player._vote_chime_positions, [0] * 8)
