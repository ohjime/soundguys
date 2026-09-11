"""Textual TUI for the cosound player.

Layout (matches design mock):
- Sticky header: COSOUND logo on the left; player name, manager, and
  last-updated/refreshing status stacked on the right.
- Scrollable middle: "COSOUND DETAILS" section with one row per layer
  (name, gain, real-time peak bar).
- Sticky footer: mute button, volume control (readout + slider), exit button.
"""

import os
from datetime import datetime
from threading import Lock

from rich.text import Text

from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Button, Static

from app.client import get_latest_manifest, get_player_info, get_sound
from app.conditioning import condition_manifest
from app.live import watch_player_changes
from app.utils import the_love_life_you_wish_you_had

ROOT_DIR = os.path.join(os.path.dirname(__file__), "..", "..")
CONDITIONED_DIR = os.path.join(ROOT_DIR, "conditioned")
REFRESH_INTERVAL = 30  # In Seconds
METER_INTERVAL = 1 / 15  # Peak bar refresh rate
PEAK_DECAY = 0.82  # Per-tick falloff so bars release smoothly
PEAK_CURVE = 0.3  # Display exponent (<1 lifts quiet peaks so bars visibly move)
MAX_HISTORY = 10  # Oldest cosound entries are dropped beyond this

LOGO = the_love_life_you_wish_you_had.strip("\n")


def _refresh_missing_manifest_entries(
    api_key: str,
    manifest: dict,
    layers: list,
    target_fs: int | None,
) -> None:
    """Download and condition newly referenced sounds into ``manifest``.

    The startup manifest contains only the collection assigned at that moment.
    A later cosound can legitimately reference a sound added to the collection,
    so refresh the remote manifest only when a polled layer is not already in the
    local one.  Conditioning just the missing subset avoids reprocessing known
    sounds during the polling loop.
    """
    missing_ids = tuple(
        dict.fromkeys(
            str(layer["sound_id"])
            for layer in layers
            if str(layer["sound_id"]) not in manifest
        )
    )
    if not missing_ids:
        return
    if target_fs is None:
        raise RuntimeError("Player output sample rate is unavailable")

    remote_manifest = get_latest_manifest(api_key)
    unavailable_ids = [
        sound_id for sound_id in missing_ids if not remote_manifest.get(sound_id)
    ]
    if unavailable_ids:
        joined_ids = ", ".join(unavailable_ids)
        raise RuntimeError(
            f"Server manifest does not include requested sound ID(s): {joined_ids}"
        )

    downloaded = {}
    for sound_id in missing_ids:
        remote_path = remote_manifest.get(sound_id)
        try:
            downloaded[sound_id] = get_sound(sound_id, remote_path)
        except Exception as error:
            raise RuntimeError(
                f"Could not download sound {sound_id}: {error}"
            ) from error

    try:
        conditioned = condition_manifest(downloaded, CONDITIONED_DIR, target_fs)
    except Exception as error:
        joined_ids = ", ".join(missing_ids)
        raise RuntimeError(
            f"Could not condition requested sound ID(s) {joined_ids}: {error}"
        ) from error

    unavailable_ids = [
        sound_id for sound_id in missing_ids if not conditioned.get(sound_id)
    ]
    if unavailable_ids:
        joined_ids = ", ".join(unavailable_ids)
        raise RuntimeError(
            f"Conditioning did not produce requested sound ID(s): {joined_ids}"
        )

    manifest.update(conditioned)


def _queue_manifest_layers(manifest: dict, layers: list, player) -> None:
    """Queue every locally available layer and start its transition."""
    for layer in layers:
        local_path = manifest.get(str(layer["sound_id"]))
        if local_path:
            player.queue_sound(local_path, layer["gain"])
    player.dequeue_cosound()


def form_row(label: str, value: str) -> Text:
    """A 'LABEL: value' line — bold label, plain value, all caps."""
    return Text.assemble((f"{label}: ", "bold"), str(value).upper())


class PeakBar(Static):
    """A horizontal real-time peak meter with fast attack and slow decay."""

    level = reactive(0.0)

    def update_level(self, level: float) -> None:
        boosted = max(0.0, min(1.0, float(level))) ** PEAK_CURVE
        self.level = max(boosted, self.level * PEAK_DECAY)

    def render(self) -> Text:
        width = self.content_size.width
        if width <= 0:
            return Text("")
        filled = round(self.level * width)
        # The whole bar shifts color as its reach crosses the thresholds.
        if self.level > 0.85:
            color = "#e05f4e"  # red
        elif self.level > 0.6:
            color = "#ff9f43"  # orange
        else:
            color = "#5fd68b"  # green
        return Text("█" * filled + " " * (width - filled), style=color)


class VolumeSlider(Widget):
    """A click/drag/arrow-key horizontal slider for master volume."""

    # Dragging the handle must move the slider, not start a text selection.
    ALLOW_SELECT = False

    can_focus = True
    value = reactive(0.7)

    BINDINGS = [
        ("left", "nudge(-0.05)", "Volume down"),
        ("right", "nudge(0.05)", "Volume up"),
    ]

    class Changed(Message):
        def __init__(self, value: float) -> None:
            self.value = value
            super().__init__()

    def __init__(self, value: float = 0.7, **kwargs) -> None:
        super().__init__(**kwargs)
        self.value = max(0.0, min(1.0, float(value)))

    def action_nudge(self, amount: float) -> None:
        self._set_value(self.value + amount)

    def _set_value(self, value: float) -> None:
        value = max(0.0, min(1.0, float(value)))
        if value != self.value:
            self.value = value
            self.post_message(self.Changed(value))

    def _set_from_x(self, x: int) -> None:
        width = max(1, self.content_size.width - 1)
        self._set_value(x / width)

    def on_mouse_down(self, event) -> None:
        self.focus()
        self.capture_mouse()
        self._set_from_x(event.x)

    def on_mouse_move(self, event) -> None:
        if self.app.mouse_captured is self:
            self._set_from_x(event.x)

    def on_mouse_up(self, event) -> None:
        self.release_mouse()

    def render(self) -> Text:
        width = max(1, self.content_size.width)
        height = max(1, self.content_size.height)
        handle = round(self.value * (width - 1))
        row = Text()
        if handle > 0:
            row.append("█" * handle, style="#62d6b9")
        row.append("█", style="#e8f4f1")
        if width - handle - 1 > 0:
            row.append("─" * (width - handle - 1), style="#5c6a72")
        return Text("\n").join([row] * height)


class VolumeControl(Horizontal):
    """Master volume as one unit: percentage readout on the left, slider right."""

    def __init__(self, value: float, **kwargs) -> None:
        super().__init__(**kwargs)
        self._value = max(0.0, min(1.0, float(value)))

    def compose(self) -> ComposeResult:
        yield Static(f"VOL {round(self._value * 100)}%", id="volume-label")
        yield VolumeSlider(value=self._value, id="volume")


CHANGE_SYMBOLS = {
    "new": "+",  # not in the previous cosound
    "up": "▲",  # gain increased
    "down": "▼",  # gain lowered
    "same": "○",  # gain unchanged
}

TRANSITION_LABELS = {
    "new": "NEW",
    "up": "UP",
    "down": "DOWN",
    "same": "SAME",
}


class LayerRow(Horizontal):
    """One playing layer: change icon, name + transition, gain, and a live peak bar."""

    def __init__(
        self, title: str, gain: float, sound_path: str | None, change: str = "same",
        *, sound_id: str | None = None, artist: str = "",
    ) -> None:
        super().__init__(classes="layer-row")
        self._title = title
        self._artist = artist
        self._gain = gain
        self._change = change
        self.sound_path = sound_path
        self.sound_id = sound_id

    def _label(self) -> Text:
        return Text(f"{self._title} — {self._artist}" if self._artist else self._title)

    def update_metadata(self, layer: dict) -> None:
        self._title = layer.get("title") or f"Sound {layer['sound_id']}"
        self._artist = layer.get("artist") or ""
        for label in self.query(".layer-title"):
            label.update(self._label())

    def compose(self) -> ComposeResult:
        yield Static(
            CHANGE_SYMBOLS[self._change], classes=f"layer-change -{self._change}"
        )
        with Horizontal(classes="layer-name"):
            yield Static(self._label(), classes="layer-title")
            yield Static(
                TRANSITION_LABELS[self._change],
                classes=f"layer-transition -{self._change}",
            )
        yield Static(f"GAIN {self._gain:.2f}", classes="layer-gain")
        yield PeakBar(classes="layer-peak")


class CosoundEntry(Vertical):
    """One cosound in the history list: a section bar plus its layer rows."""

    def __init__(
        self,
        layers: list,
        manifest: dict,
        started_at: datetime,
        previous_gains: dict[str, float],
    ) -> None:
        super().__init__(classes="cosound-entry")
        self._layers = layers
        self._manifest = manifest
        self._previous_gains = previous_gains
        self.started_at = started_at

    @staticmethod
    def _change_of(gain: float, previous: float | None) -> str:
        if previous is None:
            return "new"
        if gain < previous - 1e-6:
            return "down"
        if gain > previous + 1e-6:
            return "up"
        return "same"

    def compose(self) -> ComposeResult:
        with Horizontal(classes="section-bar"):
            yield Static("Cosound Details", classes="section-title")
            yield Static("● CURRENTLY PLAYING", classes="playing-badge")
        with Vertical(classes="layer-box"):
            for layer in self._layers:
                gain = float(layer.get("gain", 1.0))
                yield LayerRow(
                    title=layer.get("title") or f"Sound {layer['sound_id']}",
                    gain=gain,
                    sound_path=self._manifest.get(str(layer["sound_id"])),
                    change=self._change_of(
                        gain, self._previous_gains.get(str(layer["sound_id"]))
                    ),
                    sound_id=str(layer["sound_id"]),
                    artist=layer.get("artist") or "",
                )

    def update_metadata(self, layers: list) -> None:
        """Refresh labels without adding history or starting an audio transition."""
        self._layers = layers
        by_id = {str(layer["sound_id"]): layer for layer in layers}
        for row in self.query(LayerRow):
            if row.sound_id in by_id:
                row.update_metadata(by_id[row.sound_id])

    def mark_history(self) -> None:
        """Demote this entry once a newer cosound starts playing."""
        self.add_class("-history")
        elapsed = max(0, round((datetime.now() - self.started_at).total_seconds()))
        minutes, seconds = divmod(elapsed, 60)
        hours, minutes = divmod(minutes, 60)
        duration = f"{hours}h {minutes}m {seconds}s" if hours else f"{minutes}m {seconds}s"
        self.query_one(".playing-badge", Static).update(f"PLAYED FOR {duration}")
        for bar in self.query(PeakBar):
            bar.level = 0.0


class CosoundPlayerApp(App):
    """The cosound player TUI."""

    TITLE = "COSOUND Player"

    # Player is display-only: text selection just causes accidental highlights
    # when clicking buttons or dragging the slider.
    ALLOW_SELECT = False

    BINDINGS = [
        ("m", "toggle_mute", "Mute"),
        ("r", "refresh_now", "Refresh"),
        ("q", "quit", "Quit"),
    ]

    CSS = """
    Screen {
        background: ansi_default;
        color: ansi_default;
    }

    #header {
        dock: top;
        height: auto;
        background: ansi_bright_black;
        color: ansi_bright_white;
        padding: 1 2;
    }
    #logo {
        width: auto;
        height: auto;
        color: $text-accent;
        padding: 0 1;
    }
    #header-info {
        width: 1fr;
        height: auto;
        align: right top;
    }
    #header-info Static {
        width: auto;
        color: ansi_white;
        text-align: right;
    }
    #player-name {
        color: ansi_bright_white;
    }

    #body {
        padding: 1 2;
    }
    #history {
        height: auto;
    }
    /* The tile is a notched card: the title block (top-left) and the
       full-width layer box below share the card color, while the badge
       floats to the right with no background. Fixed mid-tone colors stay
       visible on both dark and light terminal backgrounds. */
    .cosound-entry {
        height: auto;
        margin-bottom: 1;
        margin-right: 1;
        color: #ffffff;
    }
    .section-bar {
        height: 3;
    }
    .section-title {
        width: auto;
        background: #353b41;
        padding: 1 2;
        text-style: bold;
    }
    .playing-badge {
        width: 1fr;
        padding: 1 2;
        color: #5fd68b;
        text-style: bold;
        text-align: right;
    }
    .layer-box {
        height: auto;
        background: #353b41;
        padding: 1;
    }
    .cosound-entry.-history .section-title {
        color: #aeb6bd;
        text-style: none;
    }
    .cosound-entry.-history .playing-badge {
        color: #aeb6bd;
        text-style: none;
    }
    .cosound-entry.-history .layer-name,
    .cosound-entry.-history .layer-gain {
        color: #aeb6bd;
        text-style: none;
    }
    .cosound-entry.-history .layer-title,
    .cosound-entry.-history .layer-transition,
    .cosound-entry.-history .layer-change {
        color: #aeb6bd;
        text-style: none;
    }
    .layer-row {
        height: 3;
        margin-bottom: 1;
    }
    .layer-change {
        width: 5;
        height: 100%;
        background: #4d565e;
        content-align: center middle;
        text-style: bold;
    }
    .layer-change.-new {
        color: #5fd68b;
    }
    .layer-change.-up {
        color: #e05f4e;
    }
    .layer-change.-down {
        color: #4da3ff;
    }
    .layer-change.-same {
        color: #99a2aa;
        text-style: none;
    }
    .layer-name {
        width: 1fr;
        height: 100%;
        background: #4d565e;
        padding: 1 2;
        margin-left: 1;
    }
    .layer-title {
        width: 1fr;
        text-style: bold;
        text-wrap: nowrap;
        text-overflow: ellipsis;
    }
    .layer-transition {
        width: auto;
        margin-left: 2;
        text-style: bold;
    }
    .layer-transition.-new {
        color: #5fd68b;
    }
    .layer-transition.-up {
        color: #e05f4e;
    }
    .layer-transition.-down {
        color: #4da3ff;
    }
    .layer-transition.-same {
        color: #99a2aa;
        text-style: none;
    }
    .layer-gain {
        width: 14;
        height: 100%;
        padding: 1 2;
        margin-left: 1;
        background: #4d565e;
    }
    .layer-peak {
        width: 24;
        height: 100%;
        padding: 1 1;
        margin-left: 1;
    }
    .empty-state {
        padding: 1 2;
        color: ansi_default;
        text-style: dim;
    }

    #footer {
        dock: bottom;
        height: 5;
        background: ansi_bright_black;
        color: ansi_bright_white;
        padding: 1 2;
        align: center middle;
    }
    /* Default Button focus style is `reverse`, which reads as selected text. */
    Button:focus {
        text-style: bold;
    }
    #mute {
        margin-right: 2;
        min-width: 12;
        background: ansi_black;
        color: ansi_bright_white;
    }
    #mute.-muted {
        background: #6b4423;
        color: #ffffff;
        text-style: bold;
    }
    #volume-control {
        width: 1fr;
        height: 100%;
        background: ansi_black;
    }
    #volume-label {
        width: 10;
        height: 100%;
        padding: 0 1;
        content-align: left middle;
        text-style: bold;
    }
    #volume {
        width: 1fr;
        height: 100%;
        padding: 0 1;
    }
    #exit {
        margin-left: 2;
        min-width: 10;
        background: #b3382f;
        color: #ffffff;
        text-style: bold;
    }
    """

    def __init__(self, api_key: str, manifest: dict, player) -> None:
        # ansi_color keeps the ansi_* CSS colors mapped to the terminal's own
        # palette instead of Textual's built-in theme approximation.
        super().__init__(ansi_color=True)
        self.api_key = api_key
        self.manifest = manifest
        self.player = player
        self.player_info: dict = {}
        self._cosound_signature = None
        self._current_entry: CosoundEntry | None = None
        self._last_gains: dict[str, float] = {}
        self._refresh_generation = 0
        self._refresh_worker_running = False
        self._refresh_lock = Lock()

    def compose(self) -> ComposeResult:
        with Horizontal(id="header"):
            yield Static(LOGO, id="logo")
            with Vertical(id="header-info"):
                yield Static(form_row("PLAYER", "—"), id="player-name")
                yield Static(form_row("MANAGED BY", "—"), id="managed-by")
                yield Static(form_row("LAST UPDATED ON", "Connecting…"), id="last-updated")
                yield Static(form_row("LIVE UPDATES", "Connecting…"), id="live-status")
                yield Static(
                    form_row("SPEAKER SYSTEM", self._speaker_summary()),
                    id="speaker-system",
                )
        with VerticalScroll(id="body"):
            with Vertical(id="history"):
                yield Static(
                    "Nothing playing yet — waiting for a cosound…",
                    classes="empty-state",
                )
        with Horizontal(id="footer"):
            yield Button("MUTE", id="mute")
            yield VolumeControl(self.player.master_gain, id="volume-control")
            yield Button("EXIT", id="exit")

    def _speaker_summary(self) -> str:
        info = getattr(self.player, "device_info", None) or {}
        name = str(info.get("name") or "Unknown")
        channels = getattr(self.player, "channels", 0)
        if not channels:
            return name
        return f"{name} ({channels} CHANNEL{'S' if channels != 1 else ''})"

    def on_mount(self) -> None:
        self._show_volume(self.player.master_gain)
        self.set_interval(METER_INTERVAL, self._update_meters)
        self.set_interval(REFRESH_INTERVAL, self.refresh_cosound)
        self.refresh_cosound()
        self._watch_live_updates()

    @work(group="live-updates", exclusive=True, exit_on_error=False)
    async def _watch_live_updates(self) -> None:
        # Async Textual workers share the UI event loop and are cancelled when
        # the app exits. HTTP, downloads, and audio remain in the refresh thread.
        await watch_player_changes(
            self.api_key, self.refresh_cosound, self._show_live_status
        )

    def _show_live_status(self, status: str) -> None:
        self.query_one("#live-status", Static).update(form_row("LIVE UPDATES", status))

    # --- Periodic refresh (network + audio transition, off the UI thread) ---

    @staticmethod
    def _signature_of(info: dict) -> tuple:
        return tuple(
            sorted(
                (str(layer["sound_id"]), round(float(layer.get("gain", 1.0)), 4))
                for layer in info.get("layers", [])
            )
        )

    def refresh_cosound(self) -> None:
        """Request a refresh, coalescing requests that arrive during one."""
        with self._refresh_lock:
            self._refresh_generation += 1
            start_worker = not self._refresh_worker_running
            if start_worker:
                self._refresh_worker_running = True

        self._show_refreshing()
        if start_worker:
            self._refresh_cosound_worker()

    @work(thread=True, group="refresh")
    def _refresh_cosound_worker(self) -> None:
        self._run_refresh_loop()

    def _run_refresh_loop(self) -> None:
        """Run one refresh at a time and then process only the latest pending one."""
        stopped_normally = False
        try:
            while True:
                with self._refresh_lock:
                    generation = self._refresh_generation

                try:
                    self._run_refresh()
                except Exception as error:
                    self.call_from_thread(
                        self._show_refresh_error_if_latest, generation, error
                    )

                with self._refresh_lock:
                    if generation == self._refresh_generation:
                        self._refresh_worker_running = False
                        stopped_normally = True
                        return

                self.call_from_thread(self._show_refreshing)
        finally:
            # An unexpected worker failure must not leave future refreshes
            # permanently coalesced into a worker that no longer exists.
            if not stopped_normally:
                with self._refresh_lock:
                    self._refresh_worker_running = False

    def _run_refresh(self) -> None:
        """Fetch, prepare, and apply one serialized player refresh."""
        info = get_player_info(self.api_key)
        manifest = dict(self.manifest)

        # Same cosound as last time: leave audio and the history list alone.
        changed = self._signature_of(info) != self._cosound_signature
        if changed:
            layers = info.get("layers", [])
            _refresh_missing_manifest_entries(
                self.api_key,
                manifest,
                layers,
                getattr(self.player, "fs", None),
            )

            # Audio loading stays on this worker thread. Since this is the only
            # refresh worker, an older transition always finishes before the
            # latest coalesced request is fetched and applied.
            self.manifest.update(manifest)
            _queue_manifest_layers(self.manifest, layers, self.player)

        # Textual waits for this UI callback to finish, so the worker cannot
        # begin a newer refresh until this state is visible.
        self.call_from_thread(self._apply_state, info, changed)

    def _show_refresh_error_if_latest(
        self, generation: int, error: Exception
    ) -> None:
        with self._refresh_lock:
            if generation != self._refresh_generation:
                return
            self._show_refresh_error(error)

    @staticmethod
    def _timestamp(now: datetime) -> str:
        hour = ((now.hour - 1) % 12) + 1  # 12-hour clock without %-I (Windows-safe)
        return f"{now:%b} {now.day}, {hour}:{now:%M %p}"

    def _show_refreshing(self) -> None:
        self.query_one("#last-updated", Static).update(
            form_row("LAST UPDATED ON", "⟳ Refreshing…")
        )

    def _show_refresh_error(self, error: Exception) -> None:
        stamp = self._timestamp(datetime.now())
        self.query_one("#last-updated", Static).update(
            form_row("LAST UPDATED ON", f"⚠ Failed at {stamp}")
        )
        self.log(f"Refresh failed: {error}")

    def _apply_state(self, info: dict, changed: bool) -> None:
        self.player_info = info
        self.query_one("#player-name", Static).update(
            form_row("PLAYER", info.get("name") or "—")
        )
        self.query_one("#managed-by", Static).update(
            form_row("MANAGED BY", info.get("manager") or "—")
        )
        self.query_one("#last-updated", Static).update(
            form_row("LAST UPDATED ON", self._timestamp(datetime.now()))
        )

        if not changed:
            if self._current_entry is not None:
                self._current_entry.update_metadata(info.get("layers", []))
            return
        self._cosound_signature = self._signature_of(info)

        history = self.query_one("#history", Vertical)
        for placeholder in history.query(".empty-state"):
            placeholder.remove()

        if self._current_entry is not None:
            self._current_entry.mark_history()
            self._current_entry = None

        layers = info.get("layers", [])
        if not layers:
            self._last_gains = {}
            return

        entry = CosoundEntry(layers, self.manifest, datetime.now(), self._last_gains)
        self._last_gains = {
            str(layer["sound_id"]): float(layer.get("gain", 1.0)) for layer in layers
        }
        self._current_entry = entry
        history.mount(entry)

        entries = list(history.query(CosoundEntry))
        for stale in entries[:-MAX_HISTORY]:
            stale.remove()

        body = self.query_one("#body", VerticalScroll)
        self.call_after_refresh(lambda: body.scroll_end(animate=True))

    # --- Real-time peak meters ---

    def _update_meters(self) -> None:
        entry = self._current_entry
        if entry is None or not entry.is_mounted:
            return
        levels = self.player.get_levels()
        for row in entry.query(LayerRow):
            # A freshly mounted row may not have composed its PeakBar yet.
            for bar in row.query(PeakBar):
                bar.update_level(levels.get(row.sound_path, 0.0))

    # --- Volume / mute controls ---

    def _show_volume(self, gain: float) -> None:
        self.query_one("#volume-label", Static).update(f"VOL {round(gain * 100)}%")

    @on(VolumeSlider.Changed)
    def _on_volume_changed(self, event: VolumeSlider.Changed) -> None:
        self.player.set_master_gain(event.value)
        self._show_volume(event.value)

    @on(Button.Pressed, "#mute")
    def _on_mute_pressed(self) -> None:
        self.action_toggle_mute()

    @on(Button.Pressed, "#exit")
    def _on_exit_pressed(self) -> None:
        self.exit()

    def action_toggle_mute(self) -> None:
        muted = self.player.toggle_mute()
        button = self.query_one("#mute", Button)
        button.label = "MUTED" if muted else "MUTE"
        button.set_class(muted, "-muted")

    def action_refresh_now(self) -> None:
        self.refresh_cosound()
