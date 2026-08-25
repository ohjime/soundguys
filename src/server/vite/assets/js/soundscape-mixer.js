/**
 * A Web Audio soundscape engine inspired by the scheduling pattern used by
 * myNoise. It loads finite files, creates two offset one-shot schedules per
 * layer, and lets the combined layer periods drift instead of looping HTML
 * audio elements in lockstep.
 *
 * This module has no Alpine or HTMX dependency. UI integrations can use the
 * public methods or the DOM event bridge in soundscape-store.js.
 */

export const DEFAULT_STRETCH = 1.75;
export const DEFAULT_LOOK_AHEAD = 0.5;
export const DEFAULT_SCHEDULER_INTERVAL = 100;

/**
 * Where a layer lands when loudness matching is switched on: EBU R128's
 * programme level. Eight of these stack under one master, so the broadcast
 * target leaves far more headroom than a streaming one (-14) would.
 */
export const DEFAULT_LOUDNESS_TARGET = -23;

/**
 * How far loudness matching may push a layer, either way. A field recording
 * made at -50 LUFS genuinely needs +27dB to reach -23, but that much make-up
 * gain is all hiss — the cap keeps a quiet source quiet rather than ruining it,
 * and the settings pane shows the applied dB so the shortfall is visible.
 */
export const LOUDNESS_GAIN_LIMIT_DB = 24;

/** The shortest stretch a crop may leave behind. */
export const MIN_REGION_SECONDS = 0.25;

export function clamp(value, min = 0, max = 1) {
    return Math.max(min, Math.min(max, Number(value) || 0));
}

export function roundToEighth(seconds) {
    return Math.round(Number(seconds) * 8) / 8;
}

export function gainFromSlider(value) {
    return clamp(value) ** 3;
}

function numberOrNull(value) {
    if (value == null || value === "") return null;
    const number = Number(value);
    return Number.isFinite(number) ? number : null;
}

/**
 * The slice of a buffer a layer actually plays, from the artist's two crop
 * handles. Both are measured from the head of the file in seconds, and
 * `trimEnd: null` means "to the end" — which is what a layer that has never
 * been cropped carries.
 *
 * This is the only place the handles are interpreted, so it is also the only
 * place they are corrected: the store reads the region back out and writes the
 * corrected numbers onto the layer, so a slider that was dragged past its
 * partner visibly snaps instead of quietly meaning something else.
 */
export function cropRegion(duration, { trimStart = 0, trimEnd = null } = {}) {
    const total = Math.max(0, Number(duration) || 0);
    if (total <= 0) return { offset: 0, duration: 0 };
    const shortest = Math.min(MIN_REGION_SECONDS, total);
    const end = trimEnd == null
        ? total
        : Math.min(total, Math.max(0, Number(trimEnd) || 0));
    const offset = Math.min(
        Math.max(0, Number(trimStart) || 0),
        Math.max(0, end - shortest),
    );
    return { offset, duration: Math.max(shortest, end - offset) };
}

/**
 * When a schedule repeats, and how much of itself each pass overlaps.
 *
 * `loopCrossfade` is the artist's crossfade in seconds: each pass is pulled
 * that much closer to the one before it, so the tail of one repeat sounds over
 * the head of the next instead of butting against it. The fade itself is an
 * envelope per pass — see `_envelope` — and this is where the schedule makes
 * room for it.
 *
 * The ceiling comes back out as `loopCrossfadeMax`, because only this function
 * can work it out and both the panel's slider and the engine need the same
 * number. Two things set it: a pass cannot fade for longer than half of itself
 * (the two ramps would cross), and the crossfade cannot pull the period in past
 * half of what the stretch asked for, which is what stops a short loop with a
 * long fade from scheduling itself into the ground.
 */
export function timingForBuffers(durationA, durationB, {
    stretch = DEFAULT_STRETCH,
    playbackRate = 1,
    loopCrossfade = 0,
} = {}) {
    const safeRate = Math.max(0.01, Number(playbackRate) || 1);
    const a = roundToEighth(durationA);
    const b = roundToEighth(durationB);
    const base = ((a + b) / 2) * stretch / safeRate;
    const loopCrossfadeMax = Math.max(0, Math.min(Math.min(a, b) / safeRate, base) / 2);
    const fade = Math.min(Math.max(0, Number(loopCrossfade) || 0), loopCrossfadeMax);
    return {
        loopCrossfade: fade,
        loopCrossfadeMax,
        // Floored, because the scheduler advances by one period per iteration:
        // a period of zero — which a hard crop of a very short file rounds down
        // to — would spin `_tick` forever instead of filling the look-ahead.
        period: Math.max(0.125, base - fade),
        // Where B sits inside the cycle is the stretch's business, so the
        // crossfade is not taken off it: pulling both in would slide the two
        // schedules together rather than tightening each one's own loop.
        offsetB: (a / 2) * stretch / safeRate,
    };
}

function normalizeLayer(layer, index) {
    const fallbackUrl = layer.sound_file ?? layer.url ?? "";
    return {
        id: layer.sound_id ?? layer.id ?? index,
        urlA: layer.urlA ?? layer.sound_file_a ?? fallbackUrl,
        urlB: layer.urlB ?? layer.sound_file_b ?? fallbackUrl,
        level: clamp(
            layer.level
            ?? (layer.gain != null ? Number(layer.gain) / 100 : undefined)
            ?? layer.sound_gain
            ?? 0.5,
        ),
        muted: Boolean(layer.muted ?? layer.mute),
        solo: Boolean(layer.solo ?? layer.isolated),
        playbackRate: Math.max(0.01, Number(layer.playbackRate ?? 1)),
        stretch: Math.max(0.01, Number(layer.stretch ?? DEFAULT_STRETCH)),
        // How long each repeat of this layer fades into the next, in seconds.
        // Zero — where every layer starts — is the hard join the passes had
        // before there was a control for it.
        loopCrossfade: Math.max(
            0,
            Number(layer.loopCrossfade ?? layer.loop_crossfade ?? 0) || 0,
        ),
        trimStart: Math.max(0, Number(layer.trimStart ?? layer.trim_start ?? 0) || 0),
        trimEnd: numberOrNull(layer.trimEnd ?? layer.trim_end),
        // null switches loudness matching off, which is how every layer starts.
        loudnessTarget: numberOrNull(layer.loudnessTarget ?? layer.loudness_target),
        metadata: layer,
    };
}

// ---------------------------------------------------------------------------
// Loudness, per ITU-R BS.1770-4 (the measurement EBU R128 is built on).
//
// A peak or an RMS reading says nothing useful about how loud a layer *seems*:
// a bright stream and a low rumble can share a peak and sit forty dB apart to
// the ear. BS.1770 fixes that by K-weighting the signal — a shelf that lifts
// the highs the way a head does, then a high-pass that discards the sub-bass no
// one hears as level — and then averaging energy over gated 400ms blocks, so
// the silence between events does not drag the number down.
//
// Everything below is plain arithmetic over the decoded buffer rather than
// Web Audio nodes: this has to run faster than real time on a buffer that is
// not playing, which an OfflineAudioContext render could not promise.
// ---------------------------------------------------------------------------

const ABSOLUTE_GATE_LUFS = -70;
const RELATIVE_GATE_LU = -10;
const BLOCK_STEP_SECONDS = 0.1;
/** BS.1770 channel weights: L, R, C flat; the surrounds lifted by ~1.5dB. */
const CHANNEL_WEIGHTS = [1, 1, 1, 1.41, 1.41];

/**
 * The two K-weighting biquads, derived for whatever rate the file decoded at.
 *
 * The standard prints its coefficients at 48kHz only, and our library is full
 * of 44.1kHz material; using the 48k numbers there would slide both corners
 * about 9% up the spectrum. These are the analog prototypes the 48k table comes
 * from, bilinear-transformed at the actual rate, so 48k reproduces the printed
 * table and every other rate gets the filter the standard meant.
 */
export function kWeightingStages(sampleRate) {
    const rate = Number(sampleRate) > 0 ? Number(sampleRate) : 48000;

    // Stage 1 — high shelf, +4dB above ~1.7kHz (the head's own response).
    const shelfK = Math.tan(Math.PI * 1681.974450955533 / rate);
    const shelfQ = 0.7071752369554196;
    const vh = 10 ** (3.999843853973347 / 20);
    const vb = vh ** 0.4996667741545416;
    const shelfDen = 1 + shelfK / shelfQ + shelfK * shelfK;

    // Stage 2 — RLB high pass, rolling off below ~38Hz.
    const passK = Math.tan(Math.PI * 38.13547087602444 / rate);
    const passQ = 0.5003270373238773;
    const passDen = 1 + passK / passQ + passK * passK;

    return [
        {
            b0: (vh + vb * shelfK / shelfQ + shelfK * shelfK) / shelfDen,
            b1: 2 * (shelfK * shelfK - vh) / shelfDen,
            b2: (vh - vb * shelfK / shelfQ + shelfK * shelfK) / shelfDen,
            a1: 2 * (shelfK * shelfK - 1) / shelfDen,
            a2: (1 - shelfK / shelfQ + shelfK * shelfK) / shelfDen,
        },
        {
            b0: 1,
            b1: -2,
            b2: 1,
            a1: 2 * (passK * passK - 1) / passDen,
            a2: (1 - passK / passQ + passK * passK) / passDen,
        },
    ];
}

/** Direct-form-I biquad, run over the samples in place. */
function filterInPlace(samples, { b0, b1, b2, a1, a2 }) {
    let x1 = 0;
    let x2 = 0;
    let y1 = 0;
    let y2 = 0;
    for (let i = 0; i < samples.length; i += 1) {
        const x0 = samples[i];
        const y0 = b0 * x0 + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2;
        samples[i] = y0;
        x2 = x1;
        x1 = x0;
        y2 = y1;
        y1 = y0;
    }
}

function blockLoudness(meanSquare) {
    return -0.691 + 10 * Math.log10(meanSquare);
}

/**
 * The integrated loudness of one region of a buffer, in LUFS.
 *
 * Returns -Infinity when there is nothing to measure — silence, or a region
 * shorter than the standard's 400ms window. Callers read that as "no reading",
 * not as "very quiet", and leave the layer's gain alone.
 *
 * Synchronous, and around 60ms for a minute of stereo audio. That is a hitch
 * the artist pays once, on the click that switches loudness matching on for a
 * layer: the caller caches the reading against the file and the region it was
 * taken over, so moving the target afterwards costs a subtraction.
 *
 * @param {AudioBuffer} buffer
 * @param {{offset?: number, duration?: number|null}} [region]
 */
export function measureLoudness(buffer, { offset = 0, duration = null } = {}) {
    if (typeof buffer?.getChannelData !== "function") return -Infinity;
    const sampleRate = Number(buffer.sampleRate) || 48000;
    const channels = Math.max(1, buffer.numberOfChannels || 1);
    const total = buffer.length ?? Math.round((buffer.duration || 0) * sampleRate);
    const start = Math.min(total, Math.max(0, Math.round(offset * sampleRate)));
    const span = duration == null ? total - start : Math.round(duration * sampleRate);
    const length = Math.max(0, Math.min(total - start, span));

    // 400ms windows overlapping by 75%. Sizing the window as exactly four steps
    // (rather than rounding 400ms separately) is what lets a block be the sum of
    // four consecutive step sums, so the whole region is squared once instead of
    // four times over.
    const step = Math.max(1, Math.round(BLOCK_STEP_SECONDS * sampleRate));
    const blockSize = step * 4;
    if (length < blockSize) return -Infinity;

    const stages = kWeightingStages(sampleRate);
    const steps = Math.floor(length / step);
    const blocks = steps - 3;
    // Each entry is the block's weighted mean square, summed across channels.
    const meanSquares = new Float64Array(blocks);

    for (let channel = 0; channel < channels; channel += 1) {
        const weight = CHANNEL_WEIGHTS[channel] ?? 1;
        const samples = Float64Array.from(
            buffer.getChannelData(channel).subarray(start, start + length),
        );
        for (const stage of stages) filterInPlace(samples, stage);

        const stepEnergy = new Float64Array(steps);
        for (let s = 0; s < steps; s += 1) {
            let sum = 0;
            const from = s * step;
            for (let i = from; i < from + step; i += 1) sum += samples[i] * samples[i];
            stepEnergy[s] = sum;
        }
        for (let b = 0; b < blocks; b += 1) {
            const energy = stepEnergy[b] + stepEnergy[b + 1]
                + stepEnergy[b + 2] + stepEnergy[b + 3];
            meanSquares[b] += weight * (energy / blockSize);
        }
    }

    // Two-stage gate: drop everything below -70 LUFS outright, then drop
    // everything more than 10 LU under what is left. Without it, the pauses in
    // a sparse recording would count as programme material.
    const audible = [];
    for (const meanSquare of meanSquares) {
        if (blockLoudness(meanSquare) > ABSOLUTE_GATE_LUFS) audible.push(meanSquare);
    }
    if (!audible.length) return -Infinity;

    const mean = (values) => values.reduce((a, b) => a + b, 0) / values.length;
    const relativeGate = blockLoudness(mean(audible)) + RELATIVE_GATE_LU;
    const gated = audible.filter((ms) => blockLoudness(ms) > relativeGate);
    if (!gated.length) return -Infinity;
    return blockLoudness(mean(gated));
}

/**
 * How much to lift or drop a layer to land it on its target, in dB, capped
 * both ways by LOUDNESS_GAIN_LIMIT_DB. Zero whenever there is nothing to go on.
 */
export function loudnessGainDb(measured, target, limit = LOUDNESS_GAIN_LIMIT_DB) {
    if (target == null || !Number.isFinite(measured)) return 0;
    return Math.max(-limit, Math.min(limit, Number(target) - measured));
}

// ---------------------------------------------------------------------------
// Waveform envelope.
//
// A drawable summary of a file: the lowest and highest sample in each of
// `buckets` equal slices of it, which is what a waveform actually is. It is
// taken here rather than in the drawing code because the decoded buffers live
// here — peaksFor goes through the same cache the voices load from, so drawing
// a layer the mix already plays costs no fetch and no second decode.
//
// Resolution is deliberately higher than any panel is wide. The envelope is
// computed once per file and the renderer folds it down to however many bars it
// has room for, so a resize redraws without coming back here.
// ---------------------------------------------------------------------------

export const DEFAULT_PEAK_BUCKETS = 2048;

// Longest run of samples one bucket will look at, per channel. Past this the
// bucket is subsampled: an envelope is min/max over tens of thousands of
// samples of an oscillating signal, and every stride hits the same extremes
// within a pixel. Without the cap a ten-minute file would walk ~30M samples on
// the main thread to draw ~800 bars.
const PEAK_SAMPLE_LIMIT = 4096;

/**
 * @param   {AudioBuffer} buffer
 * @param   {number}      [buckets] columns to summarise the file into
 * @returns {{min: Float32Array, max: Float32Array, peak: number, length: number}}
 */
export function peaksFromBuffer(buffer, buckets = DEFAULT_PEAK_BUCKETS) {
    const length = Math.max(1, Math.floor(buckets));
    const min = new Float32Array(length);
    const max = new Float32Array(length);
    const empty = { min, max, peak: 0, length };
    if (typeof buffer?.getChannelData !== "function") return empty;
    const samples = buffer.length
        ?? Math.round((buffer.duration || 0) * (buffer.sampleRate || 0));
    if (!samples) return empty;

    const channels = Math.max(1, buffer.numberOfChannels || 1);
    let peak = 0;
    for (let channel = 0; channel < channels; channel += 1) {
        const data = buffer.getChannelData(channel);
        for (let bucket = 0; bucket < length; bucket += 1) {
            const from = Math.floor((bucket * samples) / length);
            const to = Math.min(
                samples,
                Math.max(from + 1, Math.floor(((bucket + 1) * samples) / length)),
            );
            const stride = Math.max(1, Math.ceil((to - from) / PEAK_SAMPLE_LIMIT));
            let low = min[bucket];
            let high = max[bucket];
            for (let i = from; i < to; i += stride) {
                const value = data[i];
                if (value < low) low = value;
                if (value > high) high = value;
            }
            min[bucket] = low;
            max[bucket] = high;
            if (-low > peak) peak = -low;
            if (high > peak) peak = high;
        }
    }
    return { min, max, peak, length };
}

// ---------------------------------------------------------------------------
// Loop crossfade curves.
//
// Equal power rather than linear, because the two sides of a loop's seam are
// different audio — the tail of the region against its own head — so they sum
// incoherently and a pair of straight lines would dip about 3dB through the
// join, which is the hole a crossfade is there to avoid.
//
// Sampled once and shared: setValueCurveAtTime stretches whatever curve it is
// given over the duration it is given, so one pair of arrays serves every pass
// of every layer at every fade length.
// ---------------------------------------------------------------------------

function equalPowerCurve(rising, points = 128) {
    const curve = new Float32Array(points);
    for (let i = 0; i < points; i += 1) {
        const phase = (i / (points - 1)) * (Math.PI / 2);
        curve[i] = rising ? Math.sin(phase) : Math.cos(phase);
    }
    return curve;
}

const FADE_IN = equalPowerCurve(true);
const FADE_OUT = equalPowerCurve(false);

function stopSources(voice, when = 0) {
    for (const source of voice.activeSources) {
        try {
            source.stop(when);
        } catch {
            // The source may already have ended.
        }
    }
    voice.activeSources.clear();
}

export class SoundscapeMixer extends EventTarget {
    constructor({
        audioContext,
        lookAhead = DEFAULT_LOOK_AHEAD,
        schedulerInterval = DEFAULT_SCHEDULER_INTERVAL,
        masterGain = 0.5,
        stereoWidth = 1,
        crossfadeSeconds = 1.8,
    } = {}) {
        super();
        const AudioContextClass = globalThis.AudioContext ?? globalThis.webkitAudioContext;
        if (!audioContext && !AudioContextClass) {
            throw new Error("This browser does not support the Web Audio API.");
        }

        this.context = audioContext ?? new AudioContextClass();
        this.lookAhead = lookAhead;
        this.schedulerInterval = schedulerInterval;
        this.crossfadeSeconds = crossfadeSeconds;
        this.voices = [];
        this.started = false;
        this.destroyed = false;
        this._timer = null;
        // When the first pass was scheduled for. layerPlayheads needs it to tell
        // the pre-roll before playback from a pass that is genuinely sounding.
        this._startedAt = Infinity;
        this._bufferCache = new Map();
        // Keyed by url and crop region, because a reading depends on both and
        // on nothing else. Re-preparing a voice is common — every rate, stretch
        // or crop change rebuilds one — and only a crop invalidates the reading,
        // so the target slider and the rate slider both come back for free.
        this._loudnessCache = new Map();
        // Keyed by url and bucket count. A crop does not invalidate a waveform —
        // the trim UI draws the whole file and shades what is cut — so unlike
        // the loudness cache this survives every rebuild of the voice.
        this._peaksCache = new Map();
        this._buildMasterGraph(masterGain, stereoWidth);
    }

    _buildMasterGraph(masterGain, stereoWidth) {
        const context = this.context;
        this.input = context.createGain();
        this.splitter = context.createChannelSplitter(2);
        this.merger = context.createChannelMerger(2);
        this.master = context.createGain();
        this.limiter = context.createDynamicsCompressor();

        // Matrix stereo-width processor:
        // L' = ((1+w)/2)L + ((1-w)/2)R
        // R' = ((1-w)/2)L + ((1+w)/2)R
        this.matrix = {
            leftToLeft: context.createGain(),
            rightToLeft: context.createGain(),
            leftToRight: context.createGain(),
            rightToRight: context.createGain(),
        };

        this.input.connect(this.splitter);
        this.splitter.connect(this.matrix.leftToLeft, 0);
        this.splitter.connect(this.matrix.leftToRight, 0);
        this.splitter.connect(this.matrix.rightToLeft, 1);
        this.splitter.connect(this.matrix.rightToRight, 1);
        this.matrix.leftToLeft.connect(this.merger, 0, 0);
        this.matrix.rightToLeft.connect(this.merger, 0, 0);
        this.matrix.leftToRight.connect(this.merger, 0, 1);
        this.matrix.rightToRight.connect(this.merger, 0, 1);
        this.merger.connect(this.master).connect(this.limiter).connect(context.destination);

        this.master.gain.value = clamp(masterGain);
        this.limiter.threshold.value = -12;
        this.limiter.knee.value = 6;
        this.limiter.ratio.value = 10;
        this.limiter.attack.value = 0.05;
        // Some browsers cap this AudioParam at 1 second.
        const maximumRelease = Number.isFinite(this.limiter.release.maxValue)
            ? this.limiter.release.maxValue
            : 1;
        this.limiter.release.value = Math.min(2, maximumRelease);
        this.setStereoWidth(stereoWidth);
    }

    setStereoWidth(width) {
        const w = clamp(width, 0, 1.8);
        const direct = (1 + w) / 2;
        const cross = (1 - w) / 2;
        const now = this.context.currentTime;
        this.matrix.leftToLeft.gain.setTargetAtTime(direct, now, 0.03);
        this.matrix.rightToRight.gain.setTargetAtTime(direct, now, 0.03);
        this.matrix.rightToLeft.gain.setTargetAtTime(cross, now, 0.03);
        this.matrix.leftToRight.gain.setTargetAtTime(cross, now, 0.03);
    }

    setMasterGain(value) {
        this.master.gain.setTargetAtTime(clamp(value), this.context.currentTime, 0.05);
    }

    async _decode(url) {
        if (!url) throw new Error("A soundscape layer is missing an audio URL.");
        if (!this._bufferCache.has(url)) {
            const promise = fetch(url, { credentials: "same-origin" })
                .then((response) => {
                    if (!response.ok) {
                        throw new Error(`Could not load ${url} (${response.status}).`);
                    }
                    return response.arrayBuffer();
                })
                .then((data) => this.context.decodeAudioData(data));
            this._bufferCache.set(url, promise);
        }
        try {
            return await this._bufferCache.get(url);
        } catch (error) {
            this._bufferCache.delete(url);
            throw error;
        }
    }

    /**
     * A second of silence, used for a layer that has no audio yet.
     *
     * The studio lets an artist create a blank layer and fill it in later. That
     * layer still has to own a voice, because the store addresses layers by
     * index — skipping it here would shift every voice after it out from under
     * its fader. A silent voice keeps the mapping honest and costs nothing.
     */
    _silence() {
        const rate = this.context.sampleRate || 44100;
        return this.context.createBuffer(2, rate, rate);
    }

    async _prepareVoice(layer, index, onFileLoaded) {
        const config = normalizeLayer(layer, index);
        const bufferA = config.urlA ? await this._decode(config.urlA) : this._silence();
        onFileLoaded?.();
        const bufferB = config.urlB === config.urlA
            ? bufferA
            : await this._decode(config.urlB);
        if (config.urlB !== config.urlA) onFileLoaded?.();

        const gain = this.context.createGain();
        gain.gain.value = 0;
        gain.connect(this.input);
        // The crop applies to both passes — it belongs to the track, not to one
        // schedule — and the period follows the cropped length, so trimming a
        // long tail tightens the drift instead of leaving a silent gap where
        // the tail used to be.
        const region = cropRegion(bufferA.duration, config);
        const regionB = cropRegion(bufferB.duration, config);
        const timing = timingForBuffers(region.duration, regionB.duration, config);
        const voice = {
            config,
            bufferA,
            bufferB,
            gain,
            region,
            regionB,
            loudness: null,
            loudnessGainDb: 0,
            loudnessGain: 1,
            // The crossfade as the schedule could actually take it, and the
            // most it would have taken. The panel's slider reads both back
            // through layerAnalysis, so a fade asked for past the ceiling snaps
            // to it the same way a crop handle does.
            loopCrossfade: timing.loopCrossfade,
            loopCrossfadeMax: timing.loopCrossfadeMax,
            period: timing.period,
            offsetB: timing.offsetB,
            nextA: 0,
            nextB: 0,
            activeSources: new Set(),
        };
        this._measureVoice(voice);
        return voice;
    }

    /**
     * Read a voice's loudness and work out its make-up gain. Measures the
     * cropped region rather than the file, because that is what will be heard.
     */
    _measureVoice(voice) {
        if (voice.config.loudnessTarget == null) {
            voice.loudness = null;
            voice.loudnessGainDb = 0;
            voice.loudnessGain = 1;
            return voice;
        }
        const key = `${voice.config.urlA}@${voice.region.offset.toFixed(3)}`
            + `+${voice.region.duration.toFixed(3)}`;
        if (!this._loudnessCache.has(key)) {
            this._loudnessCache.set(key, measureLoudness(voice.bufferA, voice.region));
        }
        const measured = this._loudnessCache.get(key);
        voice.loudness = Number.isFinite(measured) ? measured : null;
        voice.loudnessGainDb = loudnessGainDb(measured, voice.config.loudnessTarget);
        voice.loudnessGain = 10 ** (voice.loudnessGainDb / 20);
        return voice;
    }

    /**
     * Switch loudness matching on (a target in LUFS) or off (null).
     *
     * Unlike a crop this needs no rebuild: the make-up gain is a factor on the
     * voice's own gain node, so it rides the same ramp the fader does and the
     * schedule never moves.
     */
    setLayerLoudness(index, target) {
        const voice = this.voices[index];
        if (!voice) return null;
        voice.config.loudnessTarget = target == null ? null : Number(target);
        this._measureVoice(voice);
        this._applyMixState();
        return this.layerAnalysis(index);
    }

    /**
     * What the engine learned about a layer once its file was decoded: how long
     * it is, where its crop actually landed after correction, and its loudness.
     * The settings pane cannot size a crop slider or name a reading without
     * these, and none of them are knowable before the fetch.
     */
    layerAnalysis(index) {
        const voice = this.voices[index];
        if (!voice) return null;
        // A blank layer holds a second of silence so its slot keeps a voice.
        // Reporting that as a length would offer the artist a crop over
        // nothing, so it reports no file at all — which is what it has.
        const silent = !voice.config.urlA;
        return {
            duration: silent ? 0 : voice.bufferA.duration,
            trimStart: silent ? 0 : voice.region.offset,
            trimEnd: silent ? 0 : voice.region.offset + voice.region.duration,
            loopCrossfade: silent ? 0 : voice.loopCrossfade,
            loopCrossfadeMax: silent ? 0 : voice.loopCrossfadeMax,
            loudnessTarget: voice.config.loudnessTarget,
            loudness: voice.loudness,
            loudnessGainDb: voice.loudnessGainDb,
        };
    }

    /**
     * Where a layer is sounding right now, in seconds into its file — the axis
     * the crop handles are on, so a caller can draw these against the same
     * waveform without converting anything.
     *
     * There is no single playhead to return. A voice runs two schedules of the
     * same crop, and spacing them apart is exactly what `stretch` does, so at
     * any moment a layer has two heads, one, or none: between passes the drift
     * is a gap, not a loop, and nothing is sounding at all. This reports only
     * the passes actually in their region, which is why the array is the return
     * type rather than a number — a single head would have to lie for whichever
     * part of the cycle it was not describing.
     *
     * A schedule can also be sounding twice over: a loop crossfade pulls each
     * pass into the one before it, and so does a stretch under 1, so each
     * schedule offers its outgoing pass as well as its current one. Four heads
     * is therefore the most a layer can report, and mid-crossfade is exactly
     * when it does — one head running out the tail while the other comes in at
     * the head.
     *
     * Positions come off the schedule rather than from a timer, so they stay
     * true across a pause: suspending the context stops `currentTime`, and the
     * heads stop with it.
     */
    layerPlayheads(index) {
        const voice = this.voices[index];
        if (!voice || !this.started || !voice.config.urlA) return [];
        const now = this.context.currentTime;
        const rate = Math.max(0.01, voice.config.playbackRate);
        const heads = [];
        for (const [next, region] of [[voice.nextA, voice.region], [voice.nextB, voice.regionB]]) {
            if (!(region.duration > 0)) continue;
            // nextA/nextB are the next pass still to be *scheduled*, so the one
            // sounding now began a whole number of periods before it.
            const began = next - Math.ceil((next - now) / voice.period) * voice.period;
            // The pass before it too: while a crossfade is running, the one on
            // its way out is still in its own region. Oldest first, so the head
            // leaving the tail is reported before the one arriving.
            for (const at of [began - voice.period, began]) {
                const into = now - at;
                // Slack, because `at` is reached by subtracting whole periods
                // off a scheduled time and lands a float hair either side of
                // the one it is being compared to. A millisecond is far past
                // that and still far under anything audible.
                if (at < this._startedAt - 0.001) continue;
                if (into < 0 || into >= region.duration / rate) continue;
                heads.push(region.offset + into * rate);
            }
        }
        return heads;
    }

    /**
     * The drawable envelope of a file, for the studio's trim panel.
     *
     * Addressed by url rather than by layer index on purpose: it goes through
     * `_decode`, so a file the mix already holds is summarised straight off the
     * cached buffer, and one it does not — a track being auditioned before it
     * joins the mix — is fetched once and then shared with the voice that
     * follows. Both answers are memoised, so a redraw never repeats the walk.
     */
    async peaksFor(url, buckets = DEFAULT_PEAK_BUCKETS) {
        const count = Math.max(1, Math.floor(buckets));
        const key = `${url}@${count}`;
        if (!this._peaksCache.has(key)) {
            this._peaksCache.set(
                key,
                this._decode(url).then((buffer) => peaksFromBuffer(buffer, count)),
            );
        }
        try {
            return await this._peaksCache.get(key);
        } catch (error) {
            this._peaksCache.delete(key);
            throw error;
        }
    }

    async setLayers(layers, {
        crossfadeSeconds = this.started ? this.crossfadeSeconds : 0,
        onProgress,
    } = {}) {
        if (this.destroyed) throw new Error("Cannot load a destroyed mixer.");
        const configs = Array.from(layers ?? []);
        const totalFiles = configs.reduce((total, layer) => {
            const normalized = normalizeLayer(layer, total);
            return total + (normalized.urlA === normalized.urlB ? 1 : 2);
        }, 0);
        let loadedFiles = 0;
        const report = () => {
            loadedFiles += 1;
            const detail = { loaded: loadedFiles, total: totalFiles };
            onProgress?.(detail);
            this.dispatchEvent(new CustomEvent("progress", { detail }));
        };

        const prepared = await Promise.all(
            configs.map((layer, index) => this._prepareVoice(layer, index, report)),
        );
        const previous = this.voices;
        const now = this.context.currentTime;
        const startAt = Math.max(now + 0.08, Math.ceil(now));

        this.voices = prepared;
        for (const voice of prepared) {
            voice.nextA = startAt;
            voice.nextB = startAt + voice.offsetB;
        }
        if (crossfadeSeconds > 0) {
            this._fadeInVoices(prepared, startAt, crossfadeSeconds);
        } else {
            this._applyMixState(0);
        }

        if (this.started) this._tick();
        this._retireVoices(previous, crossfadeSeconds);
        this.dispatchEvent(new CustomEvent("layerschange", {
            detail: { count: prepared.length },
        }));
        return prepared;
    }

    async replaceLayer(index, layer, {
        crossfadeSeconds = this.crossfadeSeconds,
    } = {}) {
        if (!this.voices[index]) throw new RangeError(`No layer exists at index ${index}.`);
        const replacement = await this._prepareVoice(layer, index);
        const previous = this.voices[index];
        const now = this.context.currentTime;
        replacement.nextA = now + 0.08;
        replacement.nextB = replacement.nextA + replacement.offsetB;
        this.voices[index] = replacement;
        this._applyMixState(crossfadeSeconds);
        if (this.started) this._tick();
        this._retireVoices([previous], crossfadeSeconds);
        this.dispatchEvent(new CustomEvent("layerreplace", {
            detail: { index, id: replacement.config.id },
        }));
        return replacement;
    }

    /**
     * Append one layer without disturbing the voices already playing.
     *
     * setLayers() could do this, but it rebuilds every voice and restarts each
     * schedule from a common `startAt`, so every existing layer audibly jumps
     * back into phase. The studio builder adds layers one at a time on top of a
     * running mix, so it needs the surgical version: prepare the new voice,
     * fade it in, leave the rest alone.
     */
    async addLayer(layer, { crossfadeSeconds = this.crossfadeSeconds } = {}) {
        if (this.destroyed) throw new Error("Cannot add a layer to a destroyed mixer.");
        const voice = await this._prepareVoice(layer, this.voices.length);
        const now = this.context.currentTime;
        voice.nextA = now + 0.08;
        voice.nextB = voice.nextA + voice.offsetB;
        this.voices.push(voice);
        this._applyMixState(crossfadeSeconds);
        if (this.started) this._tick();
        this.dispatchEvent(new CustomEvent("layerschange", {
            detail: { count: this.voices.length },
        }));
        return voice;
    }

    /**
     * Drop the layer at `index`, fading it out before its sources are stopped.
     * Remaining voices keep their schedules, so removing a layer never
     * re-phases the mix.
     */
    removeLayer(index, { crossfadeSeconds = this.crossfadeSeconds } = {}) {
        const voice = this.voices[index];
        if (!voice) throw new RangeError(`No layer exists at index ${index}.`);
        this.voices.splice(index, 1);
        this._applyMixState(crossfadeSeconds);
        this._retireVoices([voice], crossfadeSeconds);
        this.dispatchEvent(new CustomEvent("layerschange", {
            detail: { count: this.voices.length },
        }));
        return voice;
    }

    _retireVoices(voices, fadeSeconds) {
        const now = this.context.currentTime;
        for (const voice of voices) {
            if (!voice) continue;
            voice.gain.gain.cancelScheduledValues(now);
            voice.gain.gain.setValueAtTime(voice.gain.gain.value, now);
            voice.gain.gain.linearRampToValueAtTime(0, now + fadeSeconds);
        }
        const delay = Math.max(0, fadeSeconds * 1000 + 120);
        globalThis.setTimeout(() => {
            for (const voice of voices) {
                if (!voice) continue;
                stopSources(voice);
                try {
                    voice.gain.disconnect();
                } catch {
                    // Already disconnected.
                }
            }
        }, delay);
    }

    _targetGain(voice) {
        const anySolo = this.voices.some((candidate) => candidate.config.solo);
        const silenced = voice.config.muted || (anySolo && !voice.config.solo);
        // Loudness matching multiplies the fader rather than replacing it, so
        // the artist keeps a fader that still means what it did — the make-up
        // gain only decides where its 50% sits. The product may exceed 1; the
        // master limiter is what catches that, and eight layers summing at unity
        // would have needed it anyway.
        return silenced ? 0 : gainFromSlider(voice.config.level) * voice.loudnessGain;
    }

    /**
     * Bring newly scheduled voices up from silence when their audio begins.
     *
     * Decoding can leave `startAt` well ahead of `currentTime`. Starting the
     * ramp immediately would spend part (or all) of the fade before a source
     * is audible, which makes first playback and loaded mixes sound abrupt.
     */
    _fadeInVoices(voices, startAt, rampSeconds = this.crossfadeSeconds) {
        const now = this.context.currentTime;
        const begins = Math.max(now, startAt);
        const duration = Math.max(0, rampSeconds);
        for (const voice of voices) {
            const param = voice.gain.gain;
            const target = this._targetGain(voice);
            param.cancelScheduledValues(now);
            param.setValueAtTime(param.value, now);
            param.setValueAtTime(0, begins);
            if (duration > 0) {
                param.linearRampToValueAtTime(target, begins + duration);
            } else {
                param.setValueAtTime(target, begins);
            }
        }
    }

    _applyMixState(rampSeconds = 0.1) {
        const now = this.context.currentTime;
        for (const voice of this.voices) {
            const target = this._targetGain(voice);
            const param = voice.gain.gain;
            param.cancelScheduledValues(now);
            param.setValueAtTime(param.value, now);
            if (rampSeconds > 0) {
                param.linearRampToValueAtTime(target, now + rampSeconds);
            } else {
                param.setValueAtTime(target, now);
            }
        }
    }

    setLayerGain(index, value) {
        const voice = this.voices[index];
        if (!voice) return;
        voice.config.level = clamp(value);
        voice.config.muted = false;
        this._applyMixState();
    }

    setLayerMute(index, muted) {
        const voice = this.voices[index];
        if (!voice) return;
        voice.config.muted = Boolean(muted);
        this._applyMixState();
    }

    setLayerSolo(index, solo) {
        const voice = this.voices[index];
        if (!voice) return;
        voice.config.solo = Boolean(solo);
        this._applyMixState();
    }

    /**
     * The loop crossfade for one pass: a gain node that rises over the pass's
     * first `loopCrossfade` seconds and falls over its last.
     *
     * Per pass rather than per voice, because that is what a crossfade is —
     * two passes overlap, and each has to be somewhere different in its own
     * fade at the same moment. The voice's own gain node cannot do that: it
     * carries the fader, the mute and the loudness make-up gain, which belong
     * to the whole layer.
     *
     * Timed in context seconds, so a layer played at half speed fades for the
     * seconds the artist asked for rather than for half of them.
     *
     * Null when there is no fade to make — a pass too short to hold one, which
     * is the only case left once the caller has checked the layer asked for one
     * at all. A zero-length value curve is a RangeError, not a no-op.
     */
    _envelope(voice, when, region) {
        const pass = (region?.duration || 0) / Math.max(0.01, voice.config.playbackRate);
        // Half a pass is the ceiling, less a millisecond: at the ceiling the
        // head ramp would end on the exact moment the tail ramp begins, and an
        // automation event landing on the end of a value curve is the one case
        // implementations disagree about. A millisecond of hold between them
        // settles it and is far under anything audible.
        const fade = Math.min(voice.loopCrossfade, Math.max(0, pass - 0.001) / 2);
        if (!(fade > 0)) return null;
        const node = this.context.createGain();
        node.connect(voice.gain);
        node.gain.setValueCurveAtTime(FADE_IN, when, fade);
        node.gain.setValueCurveAtTime(FADE_OUT, when + pass - fade, fade);
        return node;
    }

    _schedule(voice, buffer, when, region) {
        const source = this.context.createBufferSource();
        source.buffer = buffer;
        source.loop = false;
        source.playbackRate.value = voice.config.playbackRate;
        const envelope = voice.loopCrossfade > 0
            ? this._envelope(voice, when, region)
            : null;
        source.connect(envelope ?? voice.gain);
        voice.activeSources.add(source);
        source.onended = () => {
            voice.activeSources.delete(source);
            for (const node of [source, envelope]) {
                try {
                    node?.disconnect();
                } catch {
                    // Already disconnected.
                }
            }
        };
        // The crop is applied per source rather than by rewriting the buffer:
        // both offset and duration are buffer-time, so a cropped voice shares
        // its cached buffer with every other layer pointing at the same file.
        if (region && region.duration > 0) {
            source.start(when, region.offset, region.duration);
        } else {
            source.start(when);
        }
    }

    _tick = () => {
        if (!this.started || this.destroyed) return;
        const horizon = this.context.currentTime + this.lookAhead;
        for (const voice of this.voices) {
            while (voice.nextA < horizon) {
                this._schedule(voice, voice.bufferA, voice.nextA, voice.region);
                voice.nextA += voice.period;
            }
            while (voice.nextB < horizon) {
                this._schedule(voice, voice.bufferB, voice.nextB, voice.regionB);
                voice.nextB += voice.period;
            }
        }
    };

    async play() {
        if (this.destroyed) return;
        await this.context.resume();
        if (!this.started) {
            const startAt = Math.max(this.context.currentTime + 0.08, Math.ceil(this.context.currentTime));
            for (const voice of this.voices) {
                voice.nextA = startAt;
                voice.nextB = startAt + voice.offsetB;
            }
            this._fadeInVoices(this.voices, startAt);
            this._startedAt = startAt;
            this.started = true;
            this._tick();
            this._timer = globalThis.setInterval(this._tick, this.schedulerInterval);
        }
        this.dispatchEvent(new CustomEvent("statechange", { detail: { state: "playing" } }));
    }

    async pause() {
        if (this.destroyed) return;
        await this.context.suspend();
        this.dispatchEvent(new CustomEvent("statechange", { detail: { state: "paused" } }));
    }

    async resume() {
        if (this.destroyed) return;
        await this.context.resume();
        this.dispatchEvent(new CustomEvent("statechange", { detail: { state: "playing" } }));
    }

    destroy() {
        if (this.destroyed) return;
        this.destroyed = true;
        this.started = false;
        if (this._timer) globalThis.clearInterval(this._timer);
        for (const voice of this.voices) {
            stopSources(voice);
            try {
                voice.gain.disconnect();
            } catch {
                // Already disconnected.
            }
        }
        this.voices = [];
        this._bufferCache.clear();
        this._loudnessCache.clear();
        this._peaksCache.clear();
        this.context.close().catch(() => {});
        this.dispatchEvent(new CustomEvent("statechange", { detail: { state: "destroyed" } }));
    }
}
