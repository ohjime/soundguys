/**
 * Layer links: words in an Explore post that name one layer of the mix the
 * post is written about.
 *
 * The writing sits under the card, so following one is three moves, and they
 * are three different things saying the same sentence:
 *
 *   1. bring the card back on screen — the link is an ordinary fragment link
 *      to the deck holding it (explore/layer_link.html), so this much still
 *      happens if the bundle never loads;
 *   2. ride the carousel over to the layer, by the same `carousel-goto` the
 *      numbers on the card's layer pill dispatch;
 *   3. isolate it, so that layer is the only one left playing.
 *
 * One delegated listener rather than a handler per link: the writing is HTML
 * rendered at request time from a writer's markdown (explore/renderer.py) and
 * arrives through an HTMX swap, so there is nothing to bind to at load and
 * nothing to rebind after a swap.
 */

export const LAYER_LINK_SELECTOR = "[data-cosound-layer]";

/**
 * Leave the mix playing only the layer at `index`.
 *
 * `toggleIsolate` is a toggle, and a link is not: pressing the same words
 * twice, or two links naming the same layer, must land on isolated both times
 * rather than switching the rest of the mix back on.
 */
export function isolateLayer(mix, index) {
    const layer = mix?.layers?.[index];
    if (!layer) return false;
    if (!layer.isolated) mix.toggleIsolate(layer);
    return true;
}

/**
 * Act on a press of one layer link.
 *
 * The scroll is unconditional. A link whose layer has gone — a post whose mix
 * was rebuilt out from under it, or one pressed before the store has mounted —
 * still owes the reader the card it promised to take them to.
 */
export function followLayerLink(link, mix, doc = globalThis.document) {
    const card = doc?.getElementById(link.getAttribute("href")?.slice(1) || "");
    card?.scrollIntoView({ behavior: "smooth", block: "center" });

    const index = Number.parseInt(link.dataset.cosoundLayer, 10);
    if (!Number.isInteger(index)) return false;
    // Dispatched from the link and left to bubble, the way the layer pill's
    // buttons reach the carousel: it listens on the window, not on the card.
    link.dispatchEvent(
        new CustomEvent("carousel-goto", { detail: index, bubbles: true }),
    );
    return isolateLayer(mix, index);
}

export function installExploreLayerLinks(doc = globalThis.document) {
    doc.addEventListener("click", (event) => {
        const link = event.target?.closest?.(LAYER_LINK_SELECTOR);
        if (!link) return;
        // The href exists for the no-script case and for the address bar; with
        // this listener running, the scroll is ours to do smoothly.
        event.preventDefault();
        followLayerLink(link, globalThis.Alpine?.store("soundLayers"), doc);
    });
}
