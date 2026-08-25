import test from "node:test";
import assert from "node:assert/strict";

import { cancelSoundSelectorBefore } from "./sound-selector-guard.js";

function htmxAfterRequest(elt, successful = true) {
    const event = new Event("htmx:afterRequest");
    Object.defineProperty(event, "detail", { value: { elt, successful } });
    return event;
}

function fixture({ selectorOpen = true } = {}) {
    const body = new EventTarget();
    const action = new EventTarget();
    let cancelClicks = 0;
    const cancel = {
        click() {
            cancelClicks += 1;
        },
    };
    const doc = {
        body,
        getElementById: () => selectorOpen ? cancel : null,
    };
    return { action, body, cancel, doc, cancelClicks: () => cancelClicks };
}

test("an action proceeds normally when no sound selector is open", () => {
    const { action, doc, cancelClicks } = fixture({ selectorOpen: false });

    assert.equal(cancelSoundSelectorBefore(action, doc), false);
    assert.equal(cancelClicks(), 0);
});

test("an action waits for the sound selector to finish cancelling", () => {
    const { action, body, cancel, doc, cancelClicks } = fixture();
    let resumed = 0;
    action.addEventListener("sound-selector:cancelled", () => {
        resumed += 1;
    });

    assert.equal(cancelSoundSelectorBefore(action, doc), true);
    assert.equal(cancelClicks(), 1);
    assert.equal(resumed, 0);

    body.dispatchEvent(htmxAfterRequest({ unrelated: true }));
    assert.equal(resumed, 0);
    body.dispatchEvent(htmxAfterRequest(cancel));
    assert.equal(resumed, 1);
});

test("a failed selector cancellation does not continue the action", () => {
    const { action, body, cancel, doc } = fixture();
    let resumed = 0;
    action.addEventListener("sound-selector:cancelled", () => {
        resumed += 1;
    });

    cancelSoundSelectorBefore(action, doc);
    body.dispatchEvent(htmxAfterRequest(cancel, false));

    assert.equal(resumed, 0);
});

