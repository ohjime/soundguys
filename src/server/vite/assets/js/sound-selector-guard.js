const CANCEL_BUTTON_ID = "cancel-swap-button-list";
const RESUME_EVENT = "sound-selector:cancelled";
const pendingActions = new WeakSet();

/**
 * Close the library's sound picker before an unrelated action changes the UI.
 *
 * Manage and Save are HTMX actions of their own. Letting either request race
 * the picker's OOB carousel restore can leave the card half picker and half
 * player, so the original action resumes only after Cancel has completed.
 */
export function cancelSoundSelectorBefore(action, doc = globalThis.document) {
    const cancel = doc?.getElementById(CANCEL_BUTTON_ID);
    if (!cancel) return false;
    if (pendingActions.has(action)) return true;

    pendingActions.add(action);
    const afterRequest = (event) => {
        if (event.detail?.elt !== cancel) return;
        doc.body.removeEventListener("htmx:afterRequest", afterRequest);
        pendingActions.delete(action);
        if (event.detail?.successful === false) return;
        action.dispatchEvent(new Event(RESUME_EVENT));
    };
    doc.body.addEventListener("htmx:afterRequest", afterRequest);
    cancel.click();
    return true;
}

export function installSoundSelectorGuard() {
    globalThis.window.cancelSoundSelectorBefore = cancelSoundSelectorBefore;
}

