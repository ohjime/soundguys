import unittest
from threading import Event, Thread, current_thread
from unittest.mock import call, patch

from app import tui


class FakePlayer:
    def __init__(self, fs=48_000):
        self.fs = fs
        self.queued = []
        self.dequeue_count = 0
        self.queue_threads = []
        self.dequeue_threads = []

    def queue_sound(self, path, gain):
        self.queued.append((path, gain))
        self.queue_threads.append(current_thread().name)

    def dequeue_cosound(self):
        self.dequeue_count += 1
        self.dequeue_threads.append(current_thread().name)


class ManifestRefreshTests(unittest.TestCase):
    def test_known_layers_keep_existing_manifest_and_skip_network_work(self):
        manifest = {"1": "/conditioned/1.wav"}
        layers = [{"sound_id": 1, "gain": 0.75}]
        player = FakePlayer()

        with (
            patch.object(tui, "get_latest_manifest") as get_manifest,
            patch.object(tui, "get_sound") as get_sound,
            patch.object(tui, "condition_manifest") as condition_manifest,
        ):
            tui._refresh_missing_manifest_entries(
                "player-key", manifest, layers, player.fs
            )

        get_manifest.assert_not_called()
        get_sound.assert_not_called()
        condition_manifest.assert_not_called()

        tui._queue_manifest_layers(manifest, layers, player)
        self.assertEqual(player.queued, [("/conditioned/1.wav", 0.75)])
        self.assertEqual(player.dequeue_count, 1)

    def test_missing_layers_are_downloaded_conditioned_merged_then_queued(self):
        manifest = {"1": "/conditioned/1.wav"}
        layers = [
            {"sound_id": 1, "gain": 0.25},
            {"sound_id": 2, "gain": 0.5},
            {"sound_id": 2, "gain": 0.5},
            {"sound_id": 3, "gain": 0.75},
        ]
        player = FakePlayer()
        remote_manifest = {
            "1": "https://sounds.example/1",
            "2": "https://sounds.example/2",
            "3": "https://sounds.example/3",
            "4": "https://sounds.example/4",
        }

        with (
            patch.object(
                tui, "get_latest_manifest", return_value=remote_manifest
            ) as get_manifest,
            patch.object(
                tui,
                "get_sound",
                side_effect=lambda sound_id, _url: f"/downloaded/{sound_id}",
            ) as get_sound,
            patch.object(
                tui,
                "condition_manifest",
                return_value={
                    "2": "/conditioned/2.wav",
                    "3": "/conditioned/3.wav",
                },
            ) as condition_manifest,
        ):
            tui._refresh_missing_manifest_entries(
                "player-key", manifest, layers, player.fs
            )

        get_manifest.assert_called_once_with("player-key")
        self.assertEqual(
            get_sound.call_args_list,
            [
                call("2", "https://sounds.example/2"),
                call("3", "https://sounds.example/3"),
            ],
        )
        condition_manifest.assert_called_once_with(
            {"2": "/downloaded/2", "3": "/downloaded/3"},
            tui.CONDITIONED_DIR,
            48_000,
        )
        self.assertEqual(
            manifest,
            {
                "1": "/conditioned/1.wav",
                "2": "/conditioned/2.wav",
                "3": "/conditioned/3.wav",
            },
        )

        tui._queue_manifest_layers(manifest, layers, player)
        self.assertEqual(
            player.queued,
            [
                ("/conditioned/1.wav", 0.25),
                ("/conditioned/2.wav", 0.5),
                ("/conditioned/2.wav", 0.5),
                ("/conditioned/3.wav", 0.75),
            ],
        )
        self.assertEqual(player.dequeue_count, 1)

    def test_manifest_refresh_errors_propagate_without_partial_update(self):
        manifest = {"1": "/conditioned/1.wav"}
        layers = [{"sound_id": 2, "gain": 1.0}]

        with (
            patch.object(
                tui, "get_latest_manifest", side_effect=OSError("offline")
            ),
            patch.object(tui, "get_sound") as get_sound,
            patch.object(tui, "condition_manifest") as condition_manifest,
        ):
            with self.assertRaisesRegex(OSError, "offline"):
                tui._refresh_missing_manifest_entries(
                    "player-key", manifest, layers, 48_000
                )

        get_sound.assert_not_called()
        condition_manifest.assert_not_called()
        self.assertEqual(manifest, {"1": "/conditioned/1.wav"})

    def test_unresolved_layer_does_not_trigger_playback_transition(self):
        manifest = {"1": "/conditioned/1.wav"}
        layers = [{"sound_id": 2, "gain": 1.0}]
        player = FakePlayer()

        with (
            patch.object(tui, "get_latest_manifest", return_value={"1": "known"}),
            patch.object(tui, "get_sound") as get_sound,
            patch.object(tui, "condition_manifest") as condition_manifest,
        ):
            with self.assertRaisesRegex(RuntimeError, r"sound ID\(s\): 2"):
                tui._refresh_missing_manifest_entries(
                    "player-key", manifest, layers, player.fs
                )
                tui._queue_manifest_layers(manifest, layers, player)

        get_sound.assert_not_called()
        condition_manifest.assert_not_called()
        self.assertEqual(manifest, {"1": "/conditioned/1.wav"})
        self.assertEqual(player.queued, [])
        self.assertEqual(player.dequeue_count, 0)

    def test_refresh_requests_are_coalesced_while_worker_is_running(self):
        app = tui.CosoundPlayerApp("player-key", {}, FakePlayer())

        with (
            patch.object(app, "_show_refreshing"),
            patch.object(app, "_refresh_cosound_worker") as start_worker,
        ):
            app.refresh_cosound()
            app.refresh_cosound()
            app.refresh_cosound()

        start_worker.assert_called_once_with()
        self.assertEqual(app._refresh_generation, 3)
        self.assertTrue(app._refresh_worker_running)

    def test_slow_refresh_finishes_before_latest_coalesced_state(self):
        manifest = {"1": "/conditioned/1.wav"}
        player = FakePlayer()
        app = tui.CosoundPlayerApp("player-key", manifest, player)
        old_info = {
            "name": "Old response",
            "layers": [{"sound_id": 2, "gain": 0.25}],
        }
        new_info = {
            "name": "New response",
            "layers": [{"sound_id": 3, "gain": 0.75}],
        }
        old_conditioning_started = Event()
        release_old_conditioning = Event()
        conditioning_order = []
        conditioning_threads = []
        thread_errors = []

        def condition(downloaded, _out_dir, _target_fs):
            sound_id = next(iter(downloaded))
            conditioning_order.append(sound_id)
            conditioning_threads.append(current_thread().name)
            if sound_id == "2":
                old_conditioning_started.set()
                if not release_old_conditioning.wait(timeout=2):
                    raise TimeoutError("old conditioning was not released")
            return {sound_id: f"/conditioned/{sound_id}.wav"}

        def run_refresh_loop():
            try:
                app._run_refresh_loop()
            except Exception as error:  # pragma: no cover - asserted below
                thread_errors.append(error)

        def apply_immediately(callback, *args):
            callback(*args)

        def track_applied_state(info, changed):
            if changed:
                app._cosound_signature = app._signature_of(info)

        with (
            patch.object(
                tui,
                "get_player_info",
                side_effect=[old_info, new_info],
            ),
            patch.object(
                tui,
                "get_latest_manifest",
                return_value={"2": "remote-2", "3": "remote-3"},
            ),
            patch.object(
                tui,
                "get_sound",
                side_effect=lambda sound_id, _path: f"/downloaded/{sound_id}",
            ),
            patch.object(tui, "condition_manifest", side_effect=condition),
            patch.object(app, "call_from_thread", side_effect=apply_immediately),
            patch.object(
                app, "_apply_state", side_effect=track_applied_state
            ) as apply_state,
            patch.object(app, "_show_refreshing") as show_refreshing,
        ):
            with app._refresh_lock:
                app._refresh_generation = 1
                app._refresh_worker_running = True
            worker_thread = Thread(
                target=run_refresh_loop, name="serial-refresh"
            )
            worker_thread.start()
            self.assertTrue(old_conditioning_started.wait(timeout=2))

            with app._refresh_lock:
                # Two requests arrive while generation 1 is still conditioning.
                app._refresh_generation = 2
                app._refresh_generation = 3

            self.assertEqual(conditioning_order, ["2"])
            self.assertEqual(player.queued, [])

            release_old_conditioning.set()
            worker_thread.join(timeout=2)
            self.assertFalse(worker_thread.is_alive())

        self.assertEqual(thread_errors, [])
        self.assertEqual(conditioning_order, ["2", "3"])
        self.assertEqual(conditioning_threads, ["serial-refresh", "serial-refresh"])
        self.assertEqual(
            manifest,
            {
                "1": "/conditioned/1.wav",
                "2": "/conditioned/2.wav",
                "3": "/conditioned/3.wav",
            },
        )
        self.assertEqual(
            player.queued,
            [
                ("/conditioned/2.wav", 0.25),
                ("/conditioned/3.wav", 0.75),
            ],
        )
        self.assertEqual(player.dequeue_count, 2)
        self.assertEqual(
            player.queue_threads, ["serial-refresh", "serial-refresh"]
        )
        self.assertEqual(
            player.dequeue_threads, ["serial-refresh", "serial-refresh"]
        )
        self.assertEqual(
            apply_state.call_args_list,
            [call(old_info, True), call(new_info, True)],
        )
        show_refreshing.assert_called_once_with()
        self.assertEqual(app._refresh_generation, 3)
        self.assertFalse(app._refresh_worker_running)


if __name__ == "__main__":
    unittest.main()
