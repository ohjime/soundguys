import test from "node:test";
import assert from "node:assert/strict";

import {
    followLayerLink,
    installExploreLayerLinks,
    isolateLayer,
} from "./explore-layer-links.js";

/** The mix a layer link acts on, with just the isolation rules kept. */
function fakeMix(count = 3) {
    const layers = Array.from({ length: count }, (_, index) => ({
        sound_id: index + 1,
        isolated: false,
        mute: false,
    }));
    return {
        layers,
        toggleIsolate(layer) {
            if (layer.isolated) {
                layer.isolated = false;
                return;
            }
            for (const other of layers) {
                if (other !== layer && other.isolated) {
                    other.isolated = false;
                    other.mute = true;
                }
            }
            layer.mute = false;
            layer.isolated = true;
        },
    };
}

/** A link as the renderer emits it, plus the card it names. */
function fixture({ layer = "1", href = "#explore-cosound-7", cardOnPage = true } = {}) {
    const scrolls = [];
    const card = { scrollIntoView: (options) => scrolls.push(options) };
    const link = new EventTarget();
    link.dataset = { cosoundLayer: layer };
    link.getAttribute = (name) => (name === "href" ? href : null);
    link.closest = (selector) =>
        selector === "[data-cosound-layer]" && link.dataset.cosoundLayer !== undefined
            ? link
            : null;

    const gotos = [];
    link.addEventListener("carousel-goto", (event) => gotos.push(event.detail));

    const doc = {
        getElementById: (id) =>
            cardOnPage && id === href.slice(1) ? card : null,
    };
    return { link, doc, scrolls, gotos };
}

test("isolating a layer leaves it the only one playing", () => {
    const mix = fakeMix();

    assert.equal(isolateLayer(mix, 1), true);
    assert.deepEqual(
        mix.layers.map((layer) => layer.isolated),
        [false, true, false],
    );
});

test("a second press on the same link does not undo the isolation", () => {
    const mix = fakeMix();

    isolateLayer(mix, 1);
    isolateLayer(mix, 1);

    assert.equal(mix.layers[1].isolated, true);
});

test("isolating another layer moves the isolation rather than adding to it", () => {
    const mix = fakeMix();

    isolateLayer(mix, 0);
    isolateLayer(mix, 2);

    assert.deepEqual(
        mix.layers.map((layer) => layer.isolated),
        [false, false, true],
    );
});

test("a layer the mix does not have is left alone", () => {
    const mix = fakeMix();

    assert.equal(isolateLayer(mix, 7), false);
    assert.equal(isolateLayer(undefined, 0), false);
});

test("following a link scrolls to the card, rides to the layer and isolates it", () => {
    const { link, doc, scrolls, gotos } = fixture({ layer: "2" });
    const mix = fakeMix();

    assert.equal(followLayerLink(link, mix, doc), true);

    assert.deepEqual(scrolls, [{ behavior: "smooth", block: "center" }]);
    assert.deepEqual(gotos, [2]);
    assert.equal(mix.layers[2].isolated, true);
});

test("the carousel event bubbles, the way the layer pill's own goto does", () => {
    const { link, doc } = fixture();
    let bubbles = null;
    link.addEventListener("carousel-goto", (event) => {
        bubbles = event.bubbles;
    });

    followLayerLink(link, fakeMix(), doc);

    assert.equal(bubbles, true);
});

test("a link still scrolls to the card when the store has not mounted", () => {
    const { link, doc, scrolls, gotos } = fixture();

    assert.equal(followLayerLink(link, undefined, doc), false);

    assert.equal(scrolls.length, 1);
    assert.deepEqual(gotos, [1]);
});

test("a card missing from the page does not stop the rest of the press", () => {
    const { link, doc, scrolls, gotos } = fixture({ cardOnPage: false });
    const mix = fakeMix();

    assert.equal(followLayerLink(link, mix, doc), true);

    assert.deepEqual(scrolls, []);
    assert.deepEqual(gotos, [1]);
    assert.equal(mix.layers[1].isolated, true);
});

test("a link with no layer index on it only scrolls", () => {
    const { link, doc, scrolls, gotos } = fixture({ layer: "" });
    const mix = fakeMix();

    assert.equal(followLayerLink(link, mix, doc), false);

    assert.equal(scrolls.length, 1);
    assert.deepEqual(gotos, []);
    assert.deepEqual(
        mix.layers.map((layer) => layer.isolated),
        [false, false, false],
    );
});

test("the delegated listener takes over the press of a layer link", () => {
    const doc = new EventTarget();
    const mix = fakeMix();
    globalThis.Alpine = { store: (name) => (name === "soundLayers" ? mix : null) };
    doc.getElementById = () => null;
    installExploreLayerLinks(doc);

    const { link } = fixture({ layer: "2" });
    let defaultPrevented = false;
    const event = new Event("click");
    Object.defineProperty(event, "target", { value: link });
    event.preventDefault = () => {
        defaultPrevented = true;
    };
    doc.dispatchEvent(event);

    assert.equal(defaultPrevented, true);
    assert.equal(mix.layers[2].isolated, true);
    delete globalThis.Alpine;
});

test("a press anywhere else in the writing is left to the browser", () => {
    const doc = new EventTarget();
    doc.getElementById = () => null;
    installExploreLayerLinks(doc);

    let defaultPrevented = false;
    const event = new Event("click");
    Object.defineProperty(event, "target", { value: { closest: () => null } });
    event.preventDefault = () => {
        defaultPrevented = true;
    };
    doc.dispatchEvent(event);

    assert.equal(defaultPrevented, false);
});
