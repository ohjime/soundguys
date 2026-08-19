import test from "node:test";
import assert from "node:assert/strict";

import {
    installSwapHeightTransitions,
    swapDuration,
} from "./swap-height.js";

function harness({ reducedMotion = false, promiseAnimations = false } = {}) {
    const listeners = new Map();
    const root = {
        defaultView: {
            matchMedia: () => ({ matches: reducedMotion }),
        },
        addEventListener(type, listener) {
            const registered = listeners.get(type) || [];
            registered.push(listener);
            listeners.set(type, registered);
        },
    };
    const animations = [];
    let naturalHeight = 100;
    let animatedHeight = null;
    const element = {
        nodeType: 1,
        style: { overflow: "visible" },
        hasAttribute(name) {
            return name === "data-swap-height";
        },
        getBoundingClientRect() {
            return { height: animatedHeight ?? naturalHeight };
        },
        animate(keyframes, options) {
            animatedHeight = Number.parseFloat(keyframes[0].height);
            let resolveFinished;
            let rejectFinished;
            const finished = promiseAnimations
                ? new Promise((resolve, reject) => {
                    resolveFinished = resolve;
                    rejectFinished = reject;
                })
                : null;
            const animation = {
                keyframes,
                options,
                finished,
                onfinish: null,
                oncancel: null,
                cancel() {
                    animatedHeight = null;
                    if (promiseAnimations) {
                        queueMicrotask(() => rejectFinished(new Error("cancelled")));
                    } else {
                        queueMicrotask(() => this.oncancel?.());
                    }
                },
                finish() {
                    animatedHeight = null;
                    if (promiseAnimations) resolveFinished();
                    else this.onfinish?.();
                },
            };
            animations.push(animation);
            return animation;
        },
    };
    return {
        root,
        element,
        animations,
        setHeight(value) {
            naturalHeight = value;
        },
        setAnimatedHeight(value) {
            animatedHeight = value;
        },
        emit(type, detail = {}) {
            for (const listener of listeners.get(type) || []) {
                listener({ target: element, detail: { target: element, ...detail } });
            }
        },
    };
}

test("swapDuration scales with distance and stays within its bounds", () => {
    assert.equal(swapDuration(1), 200);
    assert.equal(swapDuration(600), 270);
    assert.equal(swapDuration(2000), 450);
});

test("a marked htmx target animates between its old and new heights", () => {
    const scene = harness();
    installSwapHeightTransitions({ root: scene.root, scroller: null });

    scene.emit("htmx:beforeSwap");
    scene.setHeight(300);
    scene.emit("htmx:afterSwap");

    assert.deepEqual(scene.animations[0].keyframes, [
        { height: "100px" },
        { height: "300px" },
    ]);
    assert.equal(scene.element.style.overflow, "hidden");

    scene.animations[0].finish();
    assert.equal(scene.element.style.overflow, "visible");
});

test("a rapid replacement cannot leave the target overflow clipped", async () => {
    const scene = harness();
    installSwapHeightTransitions({ root: scene.root, scroller: null });

    scene.emit("htmx:beforeSwap");
    scene.setHeight(220);
    scene.emit("htmx:afterSwap");

    scene.setHeight(160);
    scene.emit("htmx:beforeSwap");
    scene.setHeight(320);
    scene.emit("htmx:afterSwap");

    await Promise.resolve();
    scene.animations[1].finish();

    assert.equal(scene.element.style.overflow, "visible");
});

test("overlapping htmx responses keep their own outgoing height snapshots", async () => {
    const scene = harness();
    const firstRequest = {};
    const secondRequest = {};
    installSwapHeightTransitions({ root: scene.root, scroller: null });

    scene.emit("htmx:beforeSwap", { xhr: firstRequest });
    scene.emit("htmx:beforeSwap", { xhr: secondRequest });

    scene.setHeight(220);
    scene.emit("htmx:afterSwap", { xhr: firstRequest });
    scene.setAnimatedHeight(170);
    scene.setHeight(340);
    scene.emit("htmx:afterSwap", { xhr: secondRequest });

    assert.equal(scene.animations.length, 2);
    assert.deepEqual(scene.animations[1].keyframes, [
        { height: "170px" },
        { height: "340px" },
    ]);

    await Promise.resolve();
    scene.animations[1].finish();
    assert.equal(scene.element.style.overflow, "visible");
});

test("late WAAPI cleanup cannot release a replacement animation's clipping", async () => {
    const scene = harness({ promiseAnimations: true });
    installSwapHeightTransitions({ root: scene.root, scroller: null });

    scene.emit("htmx:beforeSwap");
    scene.setHeight(220);
    scene.emit("htmx:afterSwap");

    scene.setAnimatedHeight(150);
    scene.emit("htmx:beforeSwap");
    scene.setHeight(360);
    scene.emit("htmx:afterSwap");

    await Promise.resolve();
    await Promise.resolve();
    assert.equal(scene.element.style.overflow, "hidden");

    scene.animations[1].finish();
    await Promise.resolve();
    assert.equal(scene.element.style.overflow, "visible");
});

test("reduced motion skips the height animation", () => {
    const scene = harness({ reducedMotion: true });
    installSwapHeightTransitions({ root: scene.root, scroller: null });

    scene.emit("htmx:beforeSwap");
    scene.setHeight(300);
    scene.emit("htmx:afterSwap");

    assert.equal(scene.animations.length, 0);
    assert.equal(scene.element.style.overflow, "visible");
});
