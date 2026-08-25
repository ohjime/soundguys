import { makeLocalLayer } from "./soundscape-store.js";

/**
 * The studio builder's component, as an Alpine.data factory.
 *
 * This lived in an `x-data` attribute, and an attribute is a bad place for it.
 * Alpine compiles an expression by splicing it into a single line, so anything
 * that needs a newline to end — a `//` comment above all — silently takes the
 * rest of the object with it, and the whole component evaluates to nothing:
 * every handler on the element becomes a no-op with no crash to point at. A
 * 3.6k-character one-liner is also exactly what an HTML formatter is most eager
 * to rewrap. In here it is ordinary JavaScript: comments are safe, the diff is
 * readable, and `alpine-expressions.test.js` has almost nothing left to guard.
 *
 * `this.$store` / `this.$refs` / `this.$nextTick` are Alpine's magics, which it
 * binds onto the component instance for Alpine.data components — the same
 * values the bare `$store` and `$refs` resolved to in the attribute.
 *
 * A layer gets into the mix three ways, all driven from the settings pane:
 *
 *   - a track from the artist's own machine (the builder's file inputs)
 *   - a sound from their library (the picker, opened over the card)
 *   - a blank layer, from the `+` tab
 *
 * Dropped tracks never leave the tab: they become blob: URLs the audio engine
 * decodes exactly like an S3 file, marked `is_local` so the store keeps them
 * out of a saved mix.
 */

const MAX_AUDIO_MB = 50;
const MAX_IMAGE_MB = 10;

export function studioBuilder({ artistName = "" } = {}) {
    return {
        artistName,
        error: "",
        dragging: false,
        pendingArt: null,
        // Which layer a file picked from the file input should fill, rather
        // than being appended as a new one. null means append.
        attachTo: null,
        // The same intent for the library picker, read by studio_library_list_items.
        libraryTarget: null,
        _errTimer: null,

        /** The one mix both panes drive. */
        get mix() {
            return this.$store.soundLayers;
        },

        fail(message) {
            this.error = message;
            clearTimeout(this._errTimer);
            this._errTimer = setTimeout(() => {
                this.error = "";
            }, 5000);
        },

        /**
         * Browsers will not start an AudioContext until the user has touched
         * the page, so every entry point nudges it first.
         */
        wake() {
            this.mix?.playAll();
        },

        /**
         * Guards every path that *appends* a layer. Replacing or filling one is
         * never blocked: those swap a source and leave the count alone.
         */
        room() {
            if (!this.mix.isFull) return true;
            this.fail(`A mix holds ${this.mix.maxLayers} layers at most.`);
            return false;
        },

        accept(file) {
            if (!file) return false;
            if (!file.type.startsWith("audio/")) {
                this.fail("That file is not audio.");
                return false;
            }
            if (file.size > MAX_AUDIO_MB * 1024 * 1024) {
                this.fail(`Keep audio under ${MAX_AUDIO_MB}MB.`);
                return false;
            }
            return true;
        },

        async addFile(file) {
            if (!this.room()) return;
            if (!this.accept(file)) return;
            this.error = "";
            const layer = makeLocalLayer({
                file,
                artworkFile: this.pendingArt,
                artistName: this.artistName,
            });
            this.pendingArt = null;
            try {
                await this.mix.addLayer(layer);
                this.focusCurrent();
            } catch {
                this.fail("That track could not be decoded.");
            }
        },

        /** Give an existing layer its audio, keeping its words and its fader. */
        async attachFile(index, file) {
            if (!this.accept(file)) return;
            this.error = "";
            const layer = makeLocalLayer({
                file,
                artworkFile: this.pendingArt,
                artistName: this.artistName,
            });
            this.pendingArt = null;
            try {
                await this.mix.setLayerSource(index, {
                    sound_file: layer.sound_file,
                    sound_title: layer.sound_title,
                    artwork_url: layer.artwork_url,
                    is_local: true,
                });
            } catch {
                this.fail("That track could not be decoded.");
            }
        },

        /**
         * The `+` tab. The store owns what a blank layer *is* — the `+` on the
         * card's layer indicator calls the same method — so what is left here
         * is the two things only this pane can do: nudge the AudioContext, and
         * say why nothing happened when the mix is full.
         *
         * The card's `+` skips the nudge on purpose. It is not reachable until
         * the mix is playing in the library (the loading overlay covers it), and
         * a blank layer is silent either way, so there is nothing to hear.
         */
        addBlank() {
            this.wake();
            if (!this.room()) return;
            this.mix.addBlankLayer().then(() => this.focusCurrent());
        },

        focusCurrent() {
            this.$nextTick(() => {
                window.dispatchEvent(new CustomEvent("carousel-goto", {
                    detail: this.mix.currentIndex,
                }));
            });
        },

        /**
         * The studio is never layerless: taking the last layer out immediately
         * seeds a fresh blank, so the artist always has something to fill in
         * rather than an empty canvas to stare at.
         */
        removeCurrent() {
            const store = this.mix;
            if (store.isEmpty) return;
            store.removeLayer(store.currentIndex);
            if (store.isEmpty) {
                this.addBlank();
                return;
            }
            this.focusCurrent();
        },

        // Entry points for the `studio:*` events the panes dispatch. They bubble
        // to the window, so either pane can reach the inputs this component owns
        // without carrying its own copy of them.

        pickTrack(index = null) {
            this.wake();
            this.attachTo = index;
            this.$refs.trackInput.click();
        },

        pickArtwork() {
            this.$refs.artInput.click();
        },

        openLibrary(index = null) {
            this.libraryTarget = index ?? null;
            this.mix.swappingLayer = true;
        },

        onTrackInput(event) {
            const files = Array.from(event.target.files || []);
            const target = this.attachTo;
            this.attachTo = null;
            event.target.value = "";
            if (target !== null && files.length) {
                this.attachFile(target, files[0]);
                return;
            }
            for (const file of files) this.addFile(file);
        },

        onArtInput(event) {
            const file = event.target.files[0];
            event.target.value = "";
            if (!file) return;
            if (file.size > MAX_IMAGE_MB * 1024 * 1024) {
                this.fail(`Keep artwork under ${MAX_IMAGE_MB}MB.`);
                return;
            }
            this.error = "";
            const store = this.mix;
            if (!store.isEmpty) {
                store.updateLayer(store.currentIndex, {
                    artwork_url: URL.createObjectURL(file),
                    isLocal: true,
                });
                return;
            }
            // Nothing to attach it to yet — hold it for the track that follows.
            this.pendingArt = file;
            this.$refs.trackInput.click();
        },

        onDrop(event) {
            this.wake();
            const files = Array.from(event.dataTransfer?.files || []);
            const art = files.find((f) => f.type.startsWith("image/")) || null;
            const tracks = files.filter((f) => f.type.startsWith("audio/"));
            if (!tracks.length) {
                this.fail("Drop an audio file to add a layer.");
                return;
            }
            for (const file of tracks) {
                this.pendingArt = art;
                this.addFile(file);
            }
        },
    };
}
