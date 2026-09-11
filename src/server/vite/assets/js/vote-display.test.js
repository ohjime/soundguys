import test from "node:test";
import assert from "node:assert/strict";
import { voteDisplay } from "./vote-display.js";

const layers = [
    { sound_id: 11, sound_title: "Rain", sound_artist: "Artist", sound_gain: 0.35, saved: false },
    { sound_id: 22, sound_title: "Bells", sound_gain: 0, saved: false },
];

test("a sound-only carousel starts at layer one and preserves the venue's gains", () => {
    const display = voteDisplay(layers);
    const original = display.layers.map((layer) => layer.sound_gain);
    assert.equal(display.currentLayer.sound_id, 11);
    assert.equal(display.gainPercent, 35);
    display.move(-1);
    assert.equal(display.currentIndex, 0);
    display.move(1);
    assert.equal(display.currentLayer.sound_id, 22);
    assert.equal(display.gainPercent, 0);
    display.move(1);
    assert.equal(display.currentIndex, 1);
    assert.deepEqual(display.layers.map((layer) => layer.sound_gain), original);
});

test("swiping the artwork changes the selected layer", () => {
    const display = voteDisplay(layers);
    const carousel = {
        scrollLeft: 295,
        querySelectorAll: () => [{ offsetLeft: 0 }, { offsetLeft: 300 }],
        scrollTo: ({ left }) => { carousel.scrollLeft = left; },
    };
    display.$refs = { carousel };
    display.updateActive();
    assert.equal(display.currentIndex, 1);
    display.move(-1);
    assert.equal(carousel.scrollLeft, 0);
});

test("a delayed like response updates its sound even after moving to another layer", () => {
    const display = voteDisplay(layers);
    display.move(1);
    display.onLayerSaved({ soundId: "11", saved: true });
    assert.equal(display.layers[0].saved, true);
    assert.equal(display.currentLayer.saved, false);
    assert.equal(layers[0].saved, false);
    display.onLayerSaved({ soundId: "99", saved: true });
    assert.equal(display.layers.length, 2);
});

test("saving sends the exact snapshot IDs and gains with the post title", () => {
    const display = voteDisplay(layers);
    display.title = 'Rain & "bells"';
    const payload = JSON.parse(display.savePayload());
    assert.equal(payload.title, display.title);
    assert.deepEqual(JSON.parse(payload.layers), [
        { sound_id: 11, sound_gain: 0.35 },
        { sound_id: 22, sound_gain: 0 },
    ]);
});

test("initialization reads only the card's metadata payload", () => {
    const display = voteDisplay();
    display.$el = {
        dataset: { postTitle: "Venue post", hasVoteAction: "false" },
        querySelector: () => ({ textContent: JSON.stringify(layers) }),
    };
    display.init();
    assert.equal(display.title, "Venue post");
    assert.equal(display.currentLayer.sound_title, "Rain");
});

test("empty and malformed predictions remain safe to render", () => {
    const display = voteDisplay();
    display.$el = { dataset: {}, querySelector: () => ({ textContent: "invalid" }) };
    display.init();
    display.move(1);
    display.updateActive();
    assert.equal(display.currentIndex, 0);
    assert.equal(display.currentLayer, null);
    assert.equal(display.currentSlide, null);
    assert.deepEqual(display.carouselSlides, []);
    assert.equal(display.gainPercent, 0);
    assert.equal(display.isEmpty, true);
    assert.equal(display.activationMode, false);
    assert.equal(display.voteLabel, "VOTE");
    assert.equal(display.isFirst, true);
    assert.equal(display.isLast, true);
    assert.deepEqual(JSON.parse(JSON.parse(display.savePayload()).layers), []);
});

test("the vote prompt is the first panel of the same carousel", () => {
    const display = voteDisplay(layers);
    display.hasVoteAction = true;
    display.choice = "1";

    assert.deepEqual(
        display.carouselSlides.map((slide) => slide.kind),
        ["vote", "layer", "layer"],
    );
    assert.deepEqual(
        display.carouselSlides.map((slide) => slide.indicatorLabel),
        ["VOTE", "1", "2"],
    );
    assert.equal(display.isVoteSlide, true);
    assert.equal(display.currentLayer, null);

    display.move(1);
    assert.equal(display.currentLayer.sound_id, 11);
    assert.equal(display.gainPercent, 35);
});

test("an awake empty venue keeps the vote presentation instead of showing activation", () => {
    const display = voteDisplay();
    display.hasVoteAction = true;

    assert.deepEqual(display.carouselSlides.map((slide) => slide.kind), ["vote"]);
    assert.equal(display.currentSlide.kind, "vote");
    display.move(1);
    assert.equal(display.currentSlide.kind, "vote");
    assert.equal(display.currentLayer, null);
    assert.equal(display.isLast, true);
});

test("sleeping metadata enables activation even when stale layers are present", () => {
    const display = voteDisplay(layers);
    display.$el = {
        dataset: { playerSleeping: "true", hasVoteAction: "true" },
        querySelector: () => ({ textContent: JSON.stringify(layers) }),
    };
    display.init();

    assert.equal(display.playerSleeping, true);
    assert.equal(display.activationMode, true);
    assert.equal(display.voteLabel, "ACTIVATE");
    assert.deepEqual(display.carouselSlides, []);
});

test("activation success keeps the confirmation view and accepts fresh layers", () => {
    const display = voteDisplay([], true);
    display.handlePlayerActivated({ layers });

    assert.equal(display.playerSleeping, false);
    assert.equal(display.activationMode, true);
    assert.equal(display.activationComplete, true);
    assert.equal(display.activationError, "");
    assert.deepEqual(display.layers, layers);
    assert.notEqual(display.layers[0], layers[0]);
    assert.deepEqual(display.carouselSlides, []);
});

test("a room that sleeps after rendering an active card keeps its normal carousel when woken", () => {
    const display = voteDisplay(layers);
    display.hasVoteAction = true;
    const replacement = [{ ...layers[1], sound_id: 33, sound_title: "Wind" }];

    display.handlePlayerActivated({ layers: replacement });

    assert.equal(display.renderedActivationCard, false);
    assert.equal(display.activationMode, false);
    assert.equal(display.activationComplete, false);
    assert.deepEqual(
        display.carouselSlides.map((slide) => slide.kind),
        ["vote", "layer"],
    );
    assert.equal(display.layers[0].sound_id, 33);
});

test("an unavailable wake on an already-rendered active card reloads into activation UI", () => {
    const display = voteDisplay(layers);
    let reloads = 0;
    display.reloadForActivation = () => { reloads += 1; };

    display.handleActivationUnavailable();

    assert.equal(reloads, 1);
    assert.equal(display.activationMode, false);
});

test("unavailable activation stays actionable and shows a calm message", () => {
    const display = voteDisplay([], true);
    display.handleActivationUnavailable({ message: "Add sounds to this room first." });

    assert.equal(display.activationMode, true);
    assert.equal(display.activationComplete, false);
    assert.equal(display.activationError, "Add sounds to this room first.");
    display.handleActivationUnavailable();
    assert.equal(display.activationError, "This room has no sounds available yet.");
});

test("anonymous vote authentication errors stay on the vote presentation", () => {
    const display = voteDisplay(layers);
    display.handleVoteAuthUnavailable({ message: "Try that vote again." });

    assert.equal(display.activationMode, false);
    assert.equal(display.voteError, "Try that vote again.");
    display.handleVoteSuccess();
    assert.equal(display.voteError, "");
});

test("vote success and throttling drive the card labels", () => {
    const display = voteDisplay(layers);
    display.choice = "1";
    display.hasVoteAction = true;
    assert.equal(display.voteLabel, "VOTE");
    assert.equal(display.votePastLabel, "Upvoted");
    const kindsBeforeVote = display.carouselSlides.map((slide) => slide.kind);
    display.handleVoteSuccess();
    assert.equal(display.voted, true);
    assert.equal(display.secondsLeft, 0);
    assert.equal(display.currentIndex, 0);
    assert.deepEqual(display.carouselSlides.map((slide) => slide.kind), kindsBeforeVote);

    const downvote = voteDisplay(layers);
    downvote.choice = "0";
    assert.equal(downvote.voteLabel, "VOTE");
    assert.equal(downvote.votePastLabel, "Downvoted");
    downvote.handleThrottle(12);
    assert.equal(downvote.secondsLeft, 12);
    clearInterval(downvote.timer);
});
