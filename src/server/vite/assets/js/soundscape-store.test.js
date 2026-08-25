import test from "node:test";
import assert from "node:assert/strict";

// The store is browser code: it reaches for window, document and an
// AudioContext the moment it initialises. These stubs are the smallest surface
// that lets it run under node — nothing here makes sound, and the tests below
// only ever use blank layers, which the engine answers with a silent buffer
// rather than a fetch.
function fakeParam(value = 0) {
    return {
        value,
        maxValue: 1,
        setTargetAtTime() {},
        cancelScheduledValues() {},
        setValueAtTime(next) {
            this.value = next;
        },
        linearRampToValueAtTime(next) {
            this.value = next;
        },
    };
}

function fakeNode() {
    return {
        gain: fakeParam(),
        threshold: fakeParam(),
        knee: fakeParam(),
        ratio: fakeParam(),
        attack: fakeParam(),
        release: fakeParam(),
        connect: (destination) => destination,
        disconnect() {},
    };
}

/**
 * A twelve-second stereo tone standing in for a decoded file. A steady sine
 * referenced to full scale measures its own dBFS in LUFS, so `lufs` here is
 * both what the file holds and what the engine should read back out of it.
 */
function toneBuffer({ lufs = -33, seconds = 12, sampleRate = 48000 } = {}) {
    const length = Math.round(seconds * sampleRate);
    const amplitude = 10 ** (lufs / 20);
    const data = new Float32Array(length);
    for (let i = 0; i < length; i += 1) {
        data[i] = amplitude * Math.sin(2 * Math.PI * 1000 * i / sampleRate);
    }
    return {
        sampleRate,
        numberOfChannels: 2,
        length,
        duration: seconds,
        getChannelData: () => data,
    };
}

function fakeContext() {
    return {
        currentTime: 0,
        sampleRate: 44100,
        createBuffer: (channels, length, rate) => ({ duration: length / rate }),
        destination: fakeNode(),
        createGain: fakeNode,
        createChannelSplitter: fakeNode,
        createChannelMerger: fakeNode,
        createDynamicsCompressor: fakeNode,
        createBufferSource: fakeNode,
        decodeAudioData: async () => toneBuffer(),
        resume: async () => {},
        suspend: async () => {},
        close: async () => {},
    };
}

globalThis.window = globalThis;
globalThis.document = { dispatchEvent: () => true };
globalThis.AudioContext = function FakeAudioContext() {
    return fakeContext();
};
// Most tests here use blank layers, which the engine answers with a silent
// buffer rather than a fetch. The ones that need a length to crop or a level to
// measure need a layer with a file behind it, so both halves of the round trip
// are stubbed: nothing is transferred, and every URL decodes to the same tone.
globalThis.fetch = async () => ({
    ok: true,
    arrayBuffer: async () => new ArrayBuffer(8),
});

const { createSoundLayersStore, makeDraftLayer, MAX_LAYERS } = await import(
    "./soundscape-store.js"
);

// The blank layer studio.views._blank_layer seeds the builder with. Its id is
// server-made and fixed, which is exactly what the client's ids have to dodge.
function seededBlankLayer() {
    return {
        sound_id: "draft-1",
        sound_file: "",
        sound_title: "",
        sound_artist: "Some Artist",
        artwork_url: "",
        gain: 50,
        mute: false,
        saved: false,
        flavor: "In the begining there was darkness.",
        tags: "Void",
        is_local: true,
        is_draft: true,
    };
}

// A layer with a file behind it, the way a library sound or a dropped track
// arrives. Only these have a length to crop and a level to measure.
function soundLayer(overrides = {}) {
    return {
        sound_id: 9,
        sound_file: "/media/sounds/rain.ogg",
        sound_title: "Rain",
        gain: 50,
        mute: false,
        saved: true,
        flavor: "",
        tags: "rain",
        is_local: false,
        ...overrides,
    };
}

async function startedStore(rawLayers = [seededBlankLayer()], options = {}) {
    const store = createSoundLayersStore(rawLayers, options);
    await store.initialize();
    return store;
}

test("destroy clears playback state before a replacement store mounts", async () => {
    const store = await startedStore();
    store.started = true;
    store.paused = true;
    assert.equal(store.started, true);
    assert.equal(store.paused, true);

    store.destroy();

    assert.equal(store.started, false);
    assert.equal(store.paused, false);
});

test("the loading view waits for its progress ring to finish at 100%", async () => {
    let releaseProgress;
    let progressReached;
    const reachedProgress = new Promise((resolve) => {
        progressReached = resolve;
    });
    const store = createSoundLayersStore([soundLayer()], {
        settleProgress: () => {
            progressReached();
            return new Promise((resolve) => {
                releaseProgress = resolve;
            });
        },
    });

    const initializing = store.initialize();
    await reachedProgress;

    assert.equal(store.loadedCount, 1);
    assert.equal(store.loadingTotal, 1);
    assert.equal(store.tracksLoading, true, "the 100% ring is still visible");

    releaseProgress();
    await initializing;
    assert.equal(store.tracksLoading, false);
});

test("loading a saved mix starts its replacement layers", async () => {
    const store = await startedStore();
    let playCalls = 0;
    store._engine.play = async () => {
        playCalls += 1;
    };

    await store.loadMix({ layers: [soundLayer()] });

    assert.equal(playCalls, 1);
    assert.equal(store.started, true);
    assert.equal(store.paused, false);
    assert.equal(store.layers[0].sound_title, "Rain");
});

test("a blank layer added in the browser cannot collide with the seeded one", async () => {
    const store = await startedStore();
    await store.addLayer(makeDraftLayer({ artistName: "Some Artist" }));

    assert.equal(store.layers.length, 2);
    // Both x-for loops that render layers — the tab strip and the carousel —
    // key on sound_id, and Alpine renders a duplicate key once. Two layers
    // sharing an id is the whole bug: the store grows, the screen does not.
    assert.notEqual(store.layers[0].sound_id, store.layers[1].sound_id);
    assert.equal(store.currentIndex, 1);
    assert.equal(store.layers[0].sound_title, "");
    assert.equal(store.layers[1].sound_title, "");
    assert.equal(store.layers[0].tags, "Void");
    assert.equal(store.layers[1].tags, "Void");
    assert.equal(store.layers[0].flavor, "In the begining there was darkness.");
    assert.equal(store.layers[1].flavor, "In the begining there was darkness.");
});

test("every browser-made layer gets its own id", async () => {
    const store = await startedStore();
    for (let i = 0; i < 5; i += 1) {
        await store.addLayer(makeDraftLayer());
    }
    const ids = store.layers.map((layer) => layer.sound_id);
    assert.equal(new Set(ids).size, ids.length);
});

test("a mix stops at MAX_LAYERS", async () => {
    const store = await startedStore();
    for (let i = store.layers.length; i < MAX_LAYERS; i += 1) {
        assert.notEqual(await store.addLayer(makeDraftLayer()), null);
    }

    assert.equal(store.layers.length, MAX_LAYERS);
    assert.equal(store.isFull, true);
    assert.equal(await store.addLayer(makeDraftLayer()), null);
    assert.equal(store.layers.length, MAX_LAYERS);
});

test("the + button's blank layer arrives selected, silent and marked", async () => {
    const store = await startedStore([], { artistName: "Some Artist" });
    const layer = await store.addBlankLayer();

    assert.equal(store.layers.length, 1);
    assert.equal(store.currentIndex, 0);
    assert.equal(store.currentLayer, layer);
    assert.equal(layer.isDraft, true);
    assert.equal(layer.sound_file, "");
    assert.equal(layer.sound_artist, "Some Artist");
    // What every per-layer control reads to take itself out of service.
    assert.equal(store.currentIsDraft, true);
});

test("the + button goes away at the cap, and adding stops with it", async () => {
    const store = await startedStore();
    while (!store.isFull) {
        assert.equal(store.canAddLayer, true);
        assert.notEqual(await store.addBlankLayer(), null);
    }

    assert.equal(store.layers.length, MAX_LAYERS);
    assert.equal(store.canAddLayer, false);
    assert.equal(await store.addBlankLayer(), null);
    assert.equal(store.layers.length, MAX_LAYERS);
});

test("a mix mounted with adding switched off refuses every way in", async () => {
    const store = await startedStore([seededBlankLayer()], { allowAdd: false });

    assert.equal(store.canAddLayer, false);
    assert.equal(store.isFull, false);
    // Not just the blank-layer button: the library picker and a dropped file
    // both land on addLayer, and none of them may grow this mix.
    assert.equal(await store.addBlankLayer(), null);
    assert.equal(await store.addLayer(makeDraftLayer()), null);
    assert.equal(store.layers.length, 1);
});

test("filling a blank layer puts its controls back", async () => {
    const store = await startedStore();
    assert.equal(store.currentIsDraft, true);

    await store.setLayerSource(0, { sound_id: 7, sound_file: "", sound_title: "Rain" });

    assert.equal(store.currentIsDraft, false);
    assert.equal(store.layers[0].isDraft, false);
    assert.equal(store.layers[0].sound_title, "Rain");
    // The card stops greying a layer by its fader only while it is blank.
    assert.equal(store.grayscaleFor(store.layers[0]), 50);
});

test("the swapping screen stays up until audio and artwork are ready", async () => {
    let finishAudio;
    let finishArtwork;
    const PreviousImage = globalThis.Image;
    globalThis.Image = class PendingImage {
        set src(_value) {
            this.complete = false;
            finishArtwork = () => {
                this.complete = true;
                this.onload?.();
            };
        }
    };

    try {
        const store = await startedStore();
        store._engine.replaceLayer = () => new Promise((resolve) => {
            finishAudio = resolve;
        });
        const replacement = store.replaceLayer(0, soundLayer({
            artwork_url: "/media/art/rain.jpg",
        }));
        await Promise.resolve();

        assert.equal(store.swapLoading, true);
        assert.equal(store.swappingLayer, true);
        assert.equal(store.currentIsDraft, true);

        finishAudio();
        await Promise.resolve();
        assert.equal(store.swapLoading, true, "artwork is still pending");

        finishArtwork();
        await replacement;
        assert.equal(store.swapLoading, false);
        assert.equal(store.swappingLayer, false);
        assert.equal(store.currentLayer.sound_title, "Rain");
    } finally {
        globalThis.Image = PreviousImage;
    }
});

test("a decoded layer comes back knowing its own length", async () => {
    const store = await startedStore([soundLayer()]);

    // Nothing on the page can size a crop slider until this arrives, which is
    // why both new sections stay hidden while it is zero.
    assert.equal(store.layers[0].duration, 12);
    assert.equal(store.layers[0].trim_start, 0);
    assert.equal(store.layers[0].trim_end, 12);
    // A blank layer has no file, so it never offers a crop over one.
    const blank = await startedStore();
    assert.equal(blank.layers[0].duration, 0);
});

test("a crop is written back as the engine settled it, not as it was dragged", async () => {
    const store = await startedStore([soundLayer()]);

    await store.setTiming(0, { trim_start: 20, trim_end: 5 });

    // The start could not have 20 seconds; it takes the last quarter-second in
    // front of the end instead, and the slider snaps to say so.
    assert.equal(store.layers[0].trim_start, 4.75);
    assert.equal(store.layers[0].trim_end, 5);
});

test("a crossfade is written back as the engine settled it, not as it was asked for", async () => {
    const store = await startedStore([soundLayer()]);

    await store.setTiming(0, { loop_crossfade: 2 });

    assert.equal(store.layers[0].loop_crossfade, 2);
    // Half a pass is all a repeat can overlap of the one before it. The panel
    // reads that ceiling off the layer to size its slider.
    assert.equal(store.layers[0].loop_crossfade_max, 6);

    await store.setTiming(0, { loop_crossfade: 9 });

    assert.equal(store.layers[0].loop_crossfade, 6, "and the slider snaps to it");
});

test("a shorter crop cuts the crossfade down with it", async () => {
    const store = await startedStore([soundLayer()]);
    await store.setTiming(0, { loop_crossfade: 5 });

    await store.setTiming(0, { trim_start: 0, trim_end: 4 });

    assert.equal(store.layers[0].loop_crossfade_max, 2);
    assert.equal(store.layers[0].loop_crossfade, 2);
});

test("a new file clears the crop the old one was cut to", async () => {
    const store = await startedStore([soundLayer()]);
    await store.setTiming(0, { trim_start: 2, trim_end: 6 });

    await store.setLayerSource(0, {
        sound_id: 11,
        sound_file: "/media/sounds/wind.ogg",
        sound_title: "Wind",
    });

    assert.equal(store.layers[0].trim_start, 0);
    assert.equal(store.layers[0].trim_end, 12);
});

test("loudness matching reads the layer and reports its make-up gain", async () => {
    const store = await startedStore([soundLayer()]);
    assert.equal(store.layers[0].loudness_target, null, "off until asked for");
    assert.equal(store.layers[0].loudness_gain_db, 0);

    store.setLoudness(0, -23);

    assert.ok(Math.abs(store.layers[0].loudness + 33) < 0.1, "the tone is -33 LUFS");
    assert.ok(Math.abs(store.layers[0].loudness_gain_db - 10) < 0.1);

    store.setLoudness(0, null);

    assert.equal(store.layers[0].loudness_target, null);
    assert.equal(store.layers[0].loudness_gain_db, 0);
});

test("a loudness target survives being given a different file", async () => {
    const store = await startedStore([soundLayer()]);
    store.setLoudness(0, -20);

    await store.setLayerSource(0, {
        sound_id: 11,
        sound_file: "/media/sounds/wind.ogg",
        sound_title: "Wind",
    });

    // The target is a preference, so it carries; the reading is a measurement
    // of a file that is no longer there, so it is taken again.
    assert.equal(store.layers[0].loudness_target, -20);
    assert.ok(Math.abs(store.layers[0].loudness_gain_db - 13) < 0.1);
});

test("removing a layer from a full mix makes room again", async () => {
    const store = await startedStore();
    while (!store.isFull) {
        await store.addLayer(makeDraftLayer());
    }
    store.removeLayer(0);

    assert.equal(store.isFull, false);
    assert.notEqual(await store.addLayer(makeDraftLayer()), null);
    assert.equal(store.layers.length, MAX_LAYERS);
});
