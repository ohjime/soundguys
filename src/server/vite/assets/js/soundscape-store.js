import { DEFAULT_LOUDNESS_TARGET, SoundscapeMixer } from "./soundscape-mixer.js";

/**
 * How many layers one mix may hold.
 *
 * The studio's `+` goes inert at this count and addLayer refuses past it, so a
 * ninth voice never reaches the graph however it was asked for.
 */
export const MAX_LAYERS = 8;

/**
 * Stand-in cover art, so a layer with no artwork has something to show instead
 * of a broken <img>. A data: URI keeps it inline — there is no request to make
 * and nothing to revoke.
 */
function placeholderArtwork(title) {
    const initial = (String(title ?? "").trim()[0] || "?").toUpperCase()
        .replace(/[<>&"']/g, "?");
    const svg = "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 400 400'>"
        + "<defs><linearGradient id='g' x1='0' y1='0' x2='1' y2='1'>"
        + "<stop offset='0' stop-color='#1f3f23'/>"
        + "<stop offset='1' stop-color='#0b140d'/></linearGradient></defs>"
        + "<rect width='400' height='400' fill='url(#g)'/>"
        + "<text x='200' y='200' text-anchor='middle' dominant-baseline='central' "
        + "font-family='Georgia,serif' font-size='190' fill='#ffffff' "
        + `fill-opacity='0.2'>${initial}</text></svg>`;
    return `data:image/svg+xml,${encodeURIComponent(svg)}`;
}

function normalizeUiLayer(layer) {
    return {
        ...layer,
        // A blank layer arrives with no art — including the one the server
        // seeds the studio with, which cannot call this helper itself.
        artwork_url: layer.artwork_url || placeholderArtwork(layer.sound_title),
        gain: layer.sound_gain != null
            ? Number(layer.sound_gain) * 100
            : Number(layer.gain ?? 50),
        saved: layer.saved ?? false,
        isolated: false,
        mute: Boolean(layer.mute),
        // Playback shape, surfaced by the studio's settings pane. Both feed
        // layerConfig, so changing either re-prepares the voice.
        playback_rate: Number(layer.playback_rate ?? 1),
        stretch: Number(layer.stretch ?? 1.75),
        // How long each repeat of the layer fades into the next, in seconds.
        // Zero is the hard join; the ceiling below is written by _syncAnalysis,
        // because only the engine knows how long a pass turned out to be.
        loop_crossfade: Math.max(0, Number(layer.loop_crossfade ?? 0) || 0),
        loop_crossfade_max: Number(layer.loop_crossfade_max ?? 0),
        // The crop, in seconds from the head of the file. A null end means "to
        // the end", which is what an uncropped layer carries and what a layer
        // falls back to whenever it is given a different file.
        trim_start: Number(layer.trim_start ?? 0),
        trim_end: layer.trim_end == null ? null : Number(layer.trim_end),
        // The loudness the layer is matched to, in LUFS; null is off.
        loudness_target: layer.loudness_target == null
            ? null
            : Number(layer.loudness_target),
        // Written by _syncAnalysis once the engine has decoded the file — the
        // settings pane cannot size a crop slider or report a reading until it
        // knows how long the track is and how loud it measured.
        duration: Number(layer.duration ?? 0),
        loudness: layer.loudness ?? null,
        loudness_gain_db: Number(layer.loudness_gain_db ?? 0),
        // A layer the artist dropped in from their own machine. Its audio and
        // artwork are blob: URLs that exist only in this tab, so it has no
        // Sound row behind it and cannot take part in a saved mix.
        isLocal: Boolean(layer.is_local ?? layer.isLocal),
        // A blank layer the artist has not given audio to yet. It holds a
        // silent voice so the index mapping between store.layers and the
        // engine's voices stays one-to-one.
        isDraft: Boolean(layer.is_draft ?? layer.isDraft),
    };
}

/**
 * Revoke the blob: URLs an outgoing layer owned, except any the replacement
 * still points at. Called wherever a layer leaves this.layers.
 */
function revokeUnusedUrls(outgoing, next = null) {
    if (!outgoing?.isLocal) return;
    const keep = new Set([next?.sound_file, next?.artwork_url]);
    for (const url of [outgoing.sound_file, outgoing.artwork_url]) {
        if (typeof url === "string" && url.startsWith("blob:") && !keep.has(url)) {
            URL.revokeObjectURL(url);
        }
    }
}

function revokeLocalUrls(layer) {
    revokeUnusedUrls(layer, null);
}

function layerConfig(layer) {
    return {
        id: layer.sound_id,
        urlA: layer.sound_file_a ?? layer.sound_file,
        urlB: layer.sound_file_b ?? layer.sound_file,
        level: Number(layer.gain) / 100,
        muted: Boolean(layer.mute),
        solo: Boolean(layer.isolated),
        playbackRate: Number(layer.playback_rate ?? 1),
        stretch: Number(layer.stretch ?? 1.75),
        loopCrossfade: Number(layer.loop_crossfade ?? 0),
        trimStart: Number(layer.trim_start ?? 0),
        trimEnd: layer.trim_end == null ? null : Number(layer.trim_end),
        loudnessTarget: layer.loudness_target == null
            ? null
            : Number(layer.loudness_target),
    };
}

function indexOfLayer(store, layer) {
    return store.layers.indexOf(layer);
}

function emit(name, detail = {}) {
    document.dispatchEvent(new CustomEvent(`cosound:audio:${name}`, { detail }));
}

/**
 * @param {object[]} rawLayers  the mix this store opens on
 * @param {object}   [options]
 * @param {boolean}  [options.allowAdd]   may this mix grow at all
 * @param {string}   [options.artistName] stamped onto blank layers made here
 */
export function createSoundLayersStore(rawLayers, {
    allowAdd = true,
    artistName = "",
} = {}) {
    return {
        layers: rawLayers.map(normalizeUiLayer),
        maxLayers: MAX_LAYERS,
        // Whether this mix takes new layers at all, decided once by whoever
        // mounted the store (c-mixer-player). It sits here rather than on a
        // card or a deck because there is exactly one mix per page and several
        // components render it: the `+` on the layer indicator reads this, and
        // so does every path that appends, so switching it off closes all of
        // them at once instead of only hiding the obvious button.
        allowAdd,
        // The name a blank layer is stamped with, so one made from the `+`
        // carries the artist the way a dropped track does.
        artistName,
        // Where the settings pane's loudness toggle switches a layer on to, so
        // the panel does not have to hard-code a number the engine owns.
        loudnessDefault: DEFAULT_LOUDNESS_TARGET,
        currentIndex: 0,
        tracksLoading: true,
        loadedCount: 0,
        swappingLayer: false,
        started: false,
        paused: false,
        loadError: "",
        _engine: null,

        get currentLayer() {
            return this.layers[this.currentIndex];
        },
        get isFirst() {
            return this.currentIndex === 0;
        },
        get isLast() {
            return this.currentIndex === this.layers.length - 1;
        },
        get isEmpty() {
            return this.layers.length === 0;
        },
        get isFull() {
            return this.layers.length >= this.maxLayers;
        },
        // The one thing the `+` button asks. It goes at the cap as well as when
        // adding is switched off, so a full mix simply stops offering.
        get canAddLayer() {
            return this.allowAdd && !this.isFull;
        },
        // A layer the artist has made room for but not yet given a sound to.
        // Every per-layer control keys off this: there is nothing to fade,
        // mute, solo or keep until the layer has audio in it.
        get currentIsDraft() {
            return Boolean(this.currentLayer?.isDraft);
        },
        get hasLocalLayers() {
            return this.layers.some((layer) => layer.isLocal);
        },
        // Saving a mix posts sound_ids the server resolves to Sound rows, so a
        // mix holding a browser-local track has nothing to point at. The studio
        // uses this to explain why the save button is off rather than failing
        // the POST.
        get canSave() {
            return this.layers.length > 0 && !this.hasLocalLayers;
        },
        get saveBlockedReason() {
            if (this.layers.length === 0) return "Add a layer first.";
            if (this.layers.some((layer) => layer.isDraft)) {
                return "Give every blank layer a sound before saving.";
            }
            if (this.hasLocalLayers) {
                return "Mixes with your own tracks stay on this device.";
            }
            return "";
        },

        anyIsolated() {
            return this.layers.some((layer) => layer.isolated);
        },
        isSilenced(layer) {
            return layer.mute || (this.anyIsolated() && !layer.isolated);
        },
        /**
         * How grey a layer's artwork reads on the card: fully grey once it is
         * silenced, easing back to colour as its fader comes up.
         *
         * A blank layer is exempt. It has no artwork — the card draws its own
         * face for it — and greying that by a fader nobody can move yet would
         * only make an empty slot look broken.
         */
        grayscaleFor(layer) {
            if (!layer || layer.isDraft) return 0;
            if (this.isSilenced(layer)) return 100;
            return Math.max(0, Math.min(100, 100 - (layer.gain || 0)));
        },
        clearIsolation() {
            this.layers.forEach((layer) => {
                layer.isolated = false;
            });
            this._syncAudibility();
        },
        setGain(layer, value) {
            const index = indexOfLayer(this, layer);
            if (index < 0) return;
            layer.gain = Number(value);
            layer.mute = false;
            if (!layer.isolated && this.anyIsolated()) {
                this.layers.forEach((candidate) => {
                    candidate.isolated = false;
                });
            }
            this._syncAudibility();
        },
        toggleMute(layer) {
            const wasMuted = layer.mute;
            layer.mute = !layer.mute;
            if (wasMuted && !layer.isolated && this.anyIsolated()) {
                this.layers.forEach((candidate) => {
                    candidate.isolated = false;
                });
            }
            this._syncAudibility();
        },
        toggleIsolate(layer) {
            if (layer.isolated) {
                layer.isolated = false;
                this._syncAudibility();
                return;
            }
            this.layers.forEach((candidate) => {
                if (candidate !== layer && candidate.isolated) {
                    candidate.isolated = false;
                    candidate.mute = true;
                }
            });
            layer.mute = false;
            layer.isolated = true;
            this._syncAudibility();
        },
        _syncAudibility() {
            if (!this._engine) return;
            this.layers.forEach((layer, index) => {
                this._engine.setLayerGain(index, Number(layer.gain) / 100);
                this._engine.setLayerMute(index, Boolean(layer.mute));
                this._engine.setLayerSolo(index, Boolean(layer.isolated));
            });
        },

        /**
         * Copy back what only the engine can know about a layer: the decoded
         * file's length, where its crop actually landed, its loop crossfade and
         * its loudness.
         *
         * The crop and the crossfade are written back rather than merely read,
         * because the engine corrects them — a start dragged past the end, a
         * crop left over from a longer file, a fade longer than half the pass
         * it has to fit in. Writing the corrected numbers onto the layer is what
         * makes the slider snap to what is really being played instead of
         * sitting at a value that no longer means anything.
         */
        _syncAnalysis(index) {
            const layer = this.layers[index];
            const analysis = this._engine?.layerAnalysis(index);
            if (!layer || !analysis) return;
            layer.duration = analysis.duration;
            layer.trim_start = analysis.trimStart;
            layer.trim_end = analysis.trimEnd;
            layer.loop_crossfade = analysis.loopCrossfade;
            layer.loop_crossfade_max = analysis.loopCrossfadeMax;
            layer.loudness = analysis.loudness;
            layer.loudness_gain_db = analysis.loudnessGainDb;
        },
        _syncAllAnalysis() {
            this.layers.forEach((_, index) => this._syncAnalysis(index));
        },

        /**
         * Match a layer to a loudness in LUFS, or pass null to leave it alone.
         *
         * No rebuild: the engine applies the make-up gain on the voice's own
         * gain node, so dragging the target is as live as dragging the fader.
         * The first switch-on pays for one measurement of the cropped region;
         * every later move of the slider reads it from the engine's cache.
         */
        setLoudness(index, target) {
            const layer = this.layers[index];
            if (!layer || !this._engine) return;
            layer.loudness_target = target == null ? null : Number(target);
            this._engine.setLayerLoudness(index, layer.loudness_target);
            this._syncAnalysis(index);
            emit("loudness", { index, layer });
        },

        async initialize() {
            // Tear down the audio graph only. destroy() would also revoke the
            // blob: URLs of this store's own layers, which are still wanted.
            this._teardownEngine();
            this.tracksLoading = true;
            this.loadedCount = 0;
            this.loadError = "";
            this.started = false;
            this.paused = false;
            const engine = new SoundscapeMixer();
            this._engine = engine;
            window.cosoundMixer = engine;
            engine.addEventListener("statechange", (event) => {
                emit("state", event.detail);
            });
            try {
                await engine.setLayers(this.layers.map(layerConfig), {
                    onProgress: ({ loaded, total }) => {
                        this.loadedCount = Math.round((loaded / total) * this.layers.length);
                        emit("progress", { loaded, total });
                    },
                });
                this.tracksLoading = false;
                this.loadedCount = this.layers.length;
                this._syncAllAnalysis();
                emit("ready", { layers: this.layers.length });
            } catch (error) {
                this.tracksLoading = false;
                this.loadError = error instanceof Error ? error.message : String(error);
                emit("error", { message: this.loadError });
                throw error;
            }
        },

        async playAll() {
            if (!this._engine || this.tracksLoading) return;
            await this._engine.play();
            this.started = true;
            this.paused = false;
        },
        async pause() {
            if (!this._engine) return;
            await this._engine.pause();
            this.paused = true;
        },
        async resume() {
            if (!this._engine) return;
            await this._engine.resume();
            this.paused = false;
        },
        masterVolumeUp() {
            this.layers.forEach((layer) => {
                layer.gain = Math.min(100, Number(layer.gain) + 10);
            });
            this._syncAudibility();
        },
        masterVolumeDown() {
            this.layers.forEach((layer) => {
                layer.gain = Math.max(0, Number(layer.gain) - 10);
            });
            this._syncAudibility();
        },

        /**
         * Append a layer to a running mix. `rawLayer` is either a serialized
         * library sound or a locally-built layer (see makeLocalLayer); the
         * engine treats a blob: URL exactly like an S3 one.
         */
        async addLayer(rawLayer) {
            if (!this._engine || !this.canAddLayer) return null;
            const layer = normalizeUiLayer(rawLayer);
            this.swappingLayer = false;
            this.loadError = "";
            try {
                await this._engine.addLayer(layerConfig(layer));
                this.layers.push(layer);
                this.currentIndex = this.layers.length - 1;
                this._syncAnalysis(this.currentIndex);
                emit("add", { index: this.currentIndex, layer });
                return layer;
            } catch (error) {
                this.loadError = error instanceof Error ? error.message : String(error);
                emit("error", { message: this.loadError });
                throw error;
            }
        },

        /**
         * Make room for a sound without choosing one yet.
         *
         * Both `+` buttons come through here — the studio's tab and the one on
         * the layer indicator — so a blank layer means the same thing on either
         * surface. It holds a silent voice, which is what keeps the index
         * mapping between `layers` and the engine's voices one-to-one, and it
         * stops being blank the moment setLayerSource or replaceLayer gives it
         * audio.
         *
         * Resolves to the new layer, or to null when the mix cannot take one.
         */
        addBlankLayer() {
            return this.addLayer(makeDraftLayer({ artistName: this.artistName }));
        },

        /**
         * Drop a layer and keep `currentIndex` pointing at something real. A
         * local layer's blob: URLs are revoked here — this is the only place
         * that knows the layer is gone for good.
         */
        removeLayer(index) {
            const layer = this.layers[index];
            if (!layer || !this._engine) return;
            this._engine.removeLayer(index);
            this.layers.splice(index, 1);
            revokeLocalUrls(layer);
            if (this.currentIndex >= index) {
                this.currentIndex = Math.max(0, this.currentIndex - 1);
            }
            this._syncAudibility();
            emit("remove", { index, layer });
        },

        /**
         * Edit a layer's words — title, flavor, tags. Pure metadata, so the
         * audio graph is left alone.
         */
        updateLayer(index, patch) {
            const layer = this.layers[index];
            if (!layer) return;
            Object.assign(layer, patch);
            emit("update", { index, layer });
        },

        /**
         * Change how a layer is played back: its rate, the stretch factor that
         * spaces its two offset schedules, the crossfade each repeat overlaps
         * the next by, and the crop that decides which stretch of the file
         * either schedule plays.
         *
         * The engine bakes all of them into a voice's timing when it is
         * prepared, so there is no live setter — the voice has to be rebuilt.
         * replaceLayer does that against the same URL, and the engine's buffer
         * cache means nothing is refetched or re-decoded.
         */
        async setTiming(index, changes) {
            const layer = this.layers[index];
            if (!layer || !this._engine) return;
            const next = { ...layer, ...changes };
            await this._engine.replaceLayer(index, layerConfig(next));
            Object.assign(layer, changes);
            this._syncAudibility();
            // After the crop, not before: a new region is a new loudness, and
            // the engine has just re-measured it.
            this._syncAnalysis(index);
            emit("timing", { index, layer });
        },

        /**
         * Give a layer its audio — the way a blank layer stops being blank, and
         * the way a local track's file is swapped. The artist's own words and
         * fader position survive; only the source changes.
         */
        async setLayerSource(index, source) {
            const current = this.layers[index];
            if (!current || !this._engine) return;
            const next = normalizeUiLayer({
                ...current,
                ...source,
                is_draft: false,
                isDraft: false,
                // A different file is a different length, so the crop the
                // artist set on the old one points into nothing. The loudness
                // target survives — it is a preference, not a measurement, and
                // the engine re-reads the new file against it.
                trim_start: 0,
                trim_end: null,
                duration: 0,
                loudness: null,
                loudness_gain_db: 0,
            });
            this.loadError = "";
            try {
                await this._engine.replaceLayer(index, layerConfig(next));
                const [outgoing] = this.layers.splice(index, 1, next);
                revokeUnusedUrls(outgoing, next);
                this._syncAnalysis(index);
                emit("source", { index, layer: next });
                return next;
            } catch (error) {
                this.loadError = error instanceof Error ? error.message : String(error);
                emit("error", { message: this.loadError });
                throw error;
            }
        },

        async replaceLayer(index, rawLayer) {
            if (!this._engine) return;
            const layer = normalizeUiLayer(rawLayer);
            // Preserve the outgoing fader position so a search result does not
            // suddenly jump in level.
            layer.gain = Number(this.layers[index]?.gain ?? layer.gain);
            layer.mute = Boolean(this.layers[index]?.mute);
            layer.isolated = Boolean(this.layers[index]?.isolated);
            this.swappingLayer = true;
            this.loadError = "";
            try {
                await this._engine.replaceLayer(index, layerConfig(layer));
                const [outgoing] = this.layers.splice(index, 1, layer);
                revokeUnusedUrls(outgoing, layer);
                this._syncAnalysis(index);
                emit("replace", { index, layer });
            } catch (error) {
                this.loadError = error instanceof Error ? error.message : String(error);
                emit("error", { message: this.loadError });
                throw error;
            } finally {
                this.swappingLayer = false;
            }
        },

        async loadMix(mix) {
            if (!this._engine) return;
            const nextLayers = mix.layers.map(normalizeUiLayer);
            this.tracksLoading = true;
            this.loadedCount = 0;
            this.currentIndex = 0;
            this.loadError = "";
            try {
                await this._engine.setLayers(nextLayers.map(layerConfig), {
                    onProgress: ({ loaded, total }) => {
                        this.loadedCount = Math.round((loaded / total) * nextLayers.length);
                    },
                });
                this.layers.forEach(revokeLocalUrls);
                this.layers = nextLayers;
                this.loadedCount = nextLayers.length;
                this.tracksLoading = false;
                this._syncAllAnalysis();
                emit("mixload", { mix });
            } catch (error) {
                this.tracksLoading = false;
                this.loadError = error instanceof Error ? error.message : String(error);
                emit("error", { message: this.loadError });
                throw error;
            }
        },

        _teardownEngine() {
            this._engine?.destroy();
            if (window.cosoundMixer === this._engine) {
                window.cosoundMixer = null;
            }
            this._engine = null;
        },

        destroy() {
            // A replacement tab can initialise before this store object is
            // overwritten. Clear playback state first so reactive consumers
            // never mistake the outgoing mix for a newly started one.
            this.started = false;
            this.paused = false;
            this._teardownEngine();
            this.layers.forEach(revokeLocalUrls);
        },
    };
}

let localLayerSequence = 0;

/**
 * An id for a browser-made layer that no server-made one can collide with.
 *
 * The studio opens on a blank layer the *server* seeded, and it arrives already
 * carrying `draft-1` (studio.views._blank_layer). A bare counter here would hand
 * that exact id to the first blank the artist adds, and both the tab strip and
 * the carousel run `x-for ... :key="l.sound_id"` — Alpine drops a duplicate key
 * rather than rendering it, so the layer would exist in the store with nothing
 * on screen and `+` would look dead. The timestamp segment keeps the two id
 * spaces apart; the counter keeps ids made within the same millisecond apart.
 */
function nextLocalId(prefix) {
    localLayerSequence += 1;
    return `${prefix}-${Date.now().toString(36)}-${localLayerSequence}`;
}

/**
 * Build a layer from files the artist picked on their own machine.
 *
 * The blob: URLs handed back are owned by the store from the moment the layer
 * is added — addLayer/removeLayer/destroy revoke them. Nothing here touches the
 * network: the file never leaves the tab, which is why the layer is marked
 * is_local and kept out of saved mixes.
 */
export function makeLocalLayer({ file, artworkFile = null, artistName = "" }) {
    if (!file) throw new Error("A local layer needs an audio file.");
    const title = file.name.replace(/\.[^.]+$/, "");
    return {
        sound_id: nextLocalId("local"),
        sound_file: URL.createObjectURL(file),
        sound_title: title,
        sound_artist: artistName,
        artwork_url: artworkFile
            ? URL.createObjectURL(artworkFile)
            : placeholderArtwork(title),
        gain: 50,
        mute: false,
        saved: false,
        flavor: "",
        tags: "your track",
        is_local: true,
    };
}

/**
 * An empty layer the artist fills in from the settings pane.
 *
 * It carries no audio, so the engine gives it a silent voice; the layer becomes
 * real once setLayerSource points it at a file or a library sound.
 */
export function makeDraftLayer({ artistName = "" } = {}) {
    const title = "Untitled layer";
    return {
        sound_id: nextLocalId("draft"),
        sound_file: "",
        sound_title: title,
        sound_artist: artistName,
        artwork_url: placeholderArtwork(title),
        gain: 50,
        mute: false,
        saved: false,
        flavor: "",
        tags: "blank",
        is_local: true,
        is_draft: true,
    };
}

export function mountSoundLayersStore(jsonElement, options = {}) {
    if (!jsonElement) throw new Error("The soundLayers JSON element is missing.");
    const layers = JSON.parse(jsonElement.textContent);
    const nextStore = createSoundLayersStore(layers, options);
    const currentStore = window.Alpine.store("soundLayers");
    currentStore?.destroy?.();
    window.Alpine.store("soundLayers", nextStore);
    const mountedStore = window.Alpine.store("soundLayers");
    mountedStore.initialize();
    return mountedStore;
}

export function installSoundscapeBridge() {
    const store = () => window.Alpine?.store("soundLayers");
    const handlers = {
        "cosound:audio:play": () => store()?.playAll(),
        "cosound:audio:pause": () => store()?.pause(),
        "cosound:audio:resume": () => store()?.resume(),
        "cosound:audio:replace-request": (event) => {
            const index = event.detail?.index ?? store()?.currentIndex;
            return store()?.replaceLayer(index, event.detail?.layer);
        },
        "cosound:audio:gain-request": (event) => {
            const activeStore = store();
            const layer = activeStore?.layers[event.detail?.index];
            if (layer) activeStore.setGain(layer, Number(event.detail.value) * 100);
        },
    };
    for (const [name, handler] of Object.entries(handlers)) {
        document.addEventListener(name, handler);
    }

    // When HTMX removes the mixer root, release its AudioContext and scheduled
    // BufferSourceNodes. A newly inserted mixer fragment mounts a fresh store.
    document.addEventListener("htmx:beforeCleanupElement", (event) => {
        const element = event.detail?.elt;
        if (element?.matches?.("[data-cosound-mixer-root]")
            || element?.querySelector?.("[data-cosound-mixer-root]")) {
            store()?.destroy();
        }
    });

    window.CosoundSoundscape = {
        SoundscapeMixer,
        createSoundLayersStore,
        mountStore: mountSoundLayersStore,
        makeLocalLayer,
        makeDraftLayer,
        dispatch(name, detail) {
            document.dispatchEvent(new CustomEvent(`cosound:audio:${name}`, { detail }));
        },
    };
}
