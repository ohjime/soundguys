from abc import ABC, abstractmethod
import threading
import numpy as np
import sounddevice as sd
import soundfile as sf

from app.devices import detect_output, list_output_devices
from app.layout import infer_layout, default_source_azimuths
from app.spatial import make_renderer
from app.reverb import FDNReverb
from app.conditioning import _resample as resample_audio
from app.chime import vote_chime


class CommunalPlayer(ABC):
    @abstractmethod
    def queue_sound(self, sound_path, gain):
        raise NotImplementedError

    @abstractmethod
    def dequeue_cosound(self):
        raise NotImplementedError


class SoundDevicePlayer(CommunalPlayer):
    @staticmethod
    def available_output_devices():
        return list_output_devices()

    def __init__(
        self,
        channels=0,
        fs=None,
        fade_time_ms=8000,
        blocksize=1024,
        master_gain=0.7,
        device=None,
        layout_override=None,
        reverb_room=0.5,
        reverb_amount=0.35,
        rotation_deg_per_s=0.0,
    ):
        # --- Resolve and probe the output device (auto-detect, see devices.py) ---
        self.device_obj = detect_output(device)
        if channels and int(channels) > 0:
            self.device_obj.channels = min(int(channels), self.device_obj.channels)
        self.device = self.device_obj.index
        self.device_info = self.device_obj.raw or {"name": self.device_obj.name}
        self.channels = self.device_obj.channels
        # Default to the device's own sample rate unless overridden.
        self.fs = int(fs) if fs else int(self.device_obj.samplerate)
        self.master_gain = float(master_gain)

        # --- Infer speaker layout and build the spatial renderer + reverb ---
        self.layout = infer_layout(self.device_obj, layout_override)
        self.renderer = make_renderer(
            self.layout, self.fs, rotation_deg_per_s=rotation_deg_per_s
        )
        self.reverb = FDNReverb(
            self.channels, self.fs, room=reverb_room, amount=reverb_amount
        )

        fade_time_sec = fade_time_ms / 1000.0
        if fade_time_sec <= 0:
            self.fade_samples = 1
        else:
            self.fade_samples = max(1, int(self.fs * fade_time_sec))

        # State Management
        self.active_tracks = {}
        self.pending_queue = {}
        self.lock = threading.Lock()
        self.muted = False
        self.levels = {}
        self.last_status = None
        self._vote_chime = vote_chime(self.fs)
        self._vote_chime_positions = []

        # Source positions for layered tracks (AAS used an even 45° spread).
        self._positions = default_source_azimuths(8)
        self._pos_idx = 0

        # Initialize Stream
        self.stream = sd.OutputStream(
            samplerate=self.fs,
            channels=self.channels,
            device=self.device,
            callback=self._audio_callback,
            blocksize=blocksize,
            dtype="float32",
        )
        self.stream.start()

    # --- Controls -----------------------------------------------------------

    def set_master_gain(self, gain):
        with self.lock:
            self.master_gain = max(0.0, min(1.0, float(gain)))

    def play_vote_chime(self):
        """Start a one-shot on the existing output, independently of mix changes."""
        with self.lock:
            # Bound burst cost while allowing new taps to sound immediately.
            self._vote_chime_positions = self._vote_chime_positions[-7:] + [0]

    def set_muted(self, muted):
        with self.lock:
            self.muted = bool(muted)

    def toggle_mute(self):
        with self.lock:
            self.muted = not self.muted
            return self.muted

    def set_reverb(self, room=None, amount=None):
        if room is not None:
            self.reverb.set_room(room)
        if amount is not None:
            self.reverb.set_amount(amount)

    def get_levels(self):
        """Latest per-track output peaks as {sound_path: peak in [0, 1]}."""
        with self.lock:
            return dict(self.levels)

    # --- Queueing -----------------------------------------------------------

    def queue_sound(self, sound_path, gain):
        """Prepares a sound to be transitioned into the mix."""
        with self.lock:
            self.pending_queue[str(sound_path)] = float(gain)

    def _assign_azimuth(self):
        if not self._positions:
            return 0.0
        az = self._positions[self._pos_idx % len(self._positions)]
        self._pos_idx += 1
        return az

    def dequeue_cosound(self):
        """Triggers the transition: fades out old tracks and fades in new ones."""
        with self.lock:
            pending = dict(self.pending_queue)
            self.pending_queue = {}

            # Any track currently playing that ISN'T in the new queue fades to 0.
            for path in list(self.active_tracks.keys()):
                if path not in pending:
                    self.active_tracks[path]["target_gain"] = 0.0

        if not pending:
            return

        # Load audio outside the lock to avoid blocking the real-time callback.
        new_tracks = {}
        for path, target_gain in pending.items():
            data, sr = sf.read(path, dtype="float32", always_2d=True)
            if sr != self.fs:
                data = resample_audio(data, sr, self.fs)
            if data.size == 0:
                continue
            new_tracks[path] = {
                "data": data,
                "ptr": 0,
                "curr_gain": 0.0,
                "target_gain": float(target_gain),
            }

        with self.lock:
            for path, target_gain in pending.items():
                track = self.active_tracks.get(path)
                if track:
                    track["target_gain"] = float(target_gain)
                    continue
                new_track = new_tracks.get(path)
                if new_track:
                    new_track["azimuth"] = self._assign_azimuth()
                    self.active_tracks[path] = new_track

    # --- Real-time audio ----------------------------------------------------

    def _audio_callback(self, outdata, frames, time, status):
        """The real-time audio thread: gain-ramp tracks, then spatialise+reverb."""
        if status:
            self.last_status = status

        sources = []
        levels = {}

        with self.lock:
            chime = np.zeros(frames, dtype=np.float32)
            remaining = []
            for position in self._vote_chime_positions:
                count = min(frames, len(self._vote_chime) - position)
                chime[:count] += self._vote_chime[position:position + count]
                if position + count < len(self._vote_chime):
                    remaining.append(position + count)
            self._vote_chime_positions = remaining
            for path in list(self.active_tracks.keys()):
                track = self.active_tracks[path]
                data = track["data"]
                if data.size == 0:
                    del self.active_tracks[path]
                    continue

                indices = (np.arange(track["ptr"], track["ptr"] + frames)) % len(data)
                chunk = data[indices, :]

                target = track["target_gain"]
                current = track["curr_gain"]
                if current != target:
                    if self.fade_samples <= 1:
                        ramp = np.full(frames, target, dtype=np.float32)
                        track["curr_gain"] = float(target)
                    else:
                        step = (target - current) / self.fade_samples
                        ramp_end = current + (step * frames)
                        ramp = np.linspace(current, ramp_end, frames)
                        if target > current:
                            ramp = np.minimum(ramp, target)
                        else:
                            ramp = np.maximum(ramp, target)
                        track["curr_gain"] = float(ramp[-1]) if frames > 0 else current
                else:
                    ramp = np.full(frames, current, dtype=np.float32)

                gained = (chunk * ramp[:, np.newaxis]).astype(np.float32)
                sources.append({"signal": gained, "azimuth": track.get("azimuth")})
                if gained.size:
                    levels[path] = float(np.max(np.abs(gained)))

                track["ptr"] = (track["ptr"] + frames) % len(data)

                if track["curr_gain"] <= 0 and track["target_gain"] == 0:
                    del self.active_tracks[path]

            output_gain = 0.0 if self.muted else self.master_gain
            self.levels = {
                path: min(1.0, peak * output_gain) for path, peak in levels.items()
            }

        # Spatialise (positioned/decorrelated) + add the reverb return. These
        # objects are only ever touched here on the audio thread.
        dry, send = self.renderer.render(sources, frames)
        wet = self.reverb.process(send)
        mix = dry + wet
        mix += chime[:, np.newaxis] / np.sqrt(self.channels)
        mix *= output_gain
        np.clip(mix, -1.0, 1.0, out=mix)
        outdata[:] = mix
