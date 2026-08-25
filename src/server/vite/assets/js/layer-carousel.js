/**
 * How long a jump may stay unarrived before the carousel stops waiting for it,
 * in milliseconds. A smooth scroll across the widest mix takes well under this;
 * the timer is only here so that a scroll the browser never runs — cut short by
 * `prefers-reduced-motion`, or aimed at where we already are — cannot leave the
 * selection pinned for good.
 */
export const JUMP_TIMEOUT_MS = 1500;

/**
 * The artwork carousel: which layer is on screen, and every way of changing it.
 *
 * Registered as the `layerCarousel` Alpine component and handed the name of the
 * store it drives, since the library and the studio share this component but not
 * their state:
 *
 *   <div x-data="layerCarousel('soundLayers')"
 *        x-init="$nextTick(() => start())"
 *        @carousel-goto.window="select($event.detail)">
 *       <div x-ref="carousel" @scroll.debounce.5ms="updateActive()"> … </div>
 *   </div>
 *
 * There are two ways to change layer here, and they are not the same gesture:
 *
 *   - Swiping the artwork *is* the selection. `currentIndex` follows the scroll
 *     position live, so the layer pill and the studio's tab strip track the
 *     drag as it happens.
 *   - Pressing a number on the pill, a studio tab or an arrow is a jump. The
 *     selection lands at once and the artwork catches up to it.
 *
 * Telling the two apart is the whole job of this component. A smooth scroll
 * passes over every item between here and the target and the scroll handler
 * takes whichever is nearest, so with nothing to stop it a jump from layer 1 to
 * layer 8 walks the selection through 2…7 on the way: the pill flickers through
 * six numbers, the studio re-checks six tabs, and its settings panel re-renders
 * for six layers nobody asked to see. `jumpingTo` holds the index a jump is
 * flying to and keeps the scroll handler quiet until it gets there.
 *
 * That wait is given up the moment it stops being true — on arrival, on the
 * timeout above, and on the first sign of a hand on the carousel (see
 * `release`), because a swipe that interrupts a jump is a new selection and has
 * to be followed live like any other.
 */
export function layerCarousel(storeName) {
    return {
        /**
         * The index a jump is on its way to, or null when the carousel is
         * already where it thinks it is. While this is set, scroll positions
         * are the animation talking, not the artist.
         */
        jumpingTo: null,
        jumpTimer: null,

        get mix() {
            return this.$store[storeName];
        },

        items() {
            return this.$refs.carousel?.getElementsByClassName("carousel-item") ?? [];
        },

        /** The item sitting closest to the left edge — the one being shown. */
        nearest() {
            const el = this.$refs.carousel;
            if (!el) return -1;
            const items = this.items();
            let index = -1;
            let best = Infinity;
            for (let i = 0; i < items.length; i++) {
                const distance = Math.abs(items[i].offsetLeft - el.scrollLeft);
                if (distance < best) {
                    best = distance;
                    index = i;
                }
            }
            return index;
        },

        /** The scroll handler: follow the artwork, unless a jump is in flight. */
        updateActive() {
            const index = this.nearest();
            if (index < 0) return;
            if (this.jumpingTo !== null) {
                if (index === this.jumpingTo) this.release();
                return;
            }
            this.show(index);
        },

        /**
         * Point the store at a layer. Guarded against the store rather than
         * against the DOM: a layer that has just been added exists in `layers`
         * a tick before `x-for` has drawn a carousel item for it.
         */
        show(index) {
            const mix = this.mix;
            if (!mix?.layers[index]) return;
            mix.currentIndex = index;
        },

        /** Stop waiting on a jump, whether or not it ever arrived. */
        release() {
            this.jumpingTo = null;
            clearTimeout(this.jumpTimer);
            this.jumpTimer = null;
        },

        /**
         * Move to a layer: select it now, scroll to it after.
         *
         * The scroll waits a tick because the index may point at a layer added
         * in this same handler — the `+` on the pill selects what it just made —
         * and there is no item to measure until `x-for` has caught up.
         */
        select(index) {
            const count = this.mix?.layers.length ?? 0;
            if (!count) return;
            const target = Math.min(Math.max(0, Math.trunc(Number(index)) || 0), count - 1);
            this.show(target);
            this.$nextTick(() => this.slideTo(target));
        },

        move(delta) {
            this.select((this.mix?.currentIndex ?? 0) + delta);
        },

        /** Apply the server-confirmed collection state to the sound it names. */
        applySaved(detail) {
            if (!detail || detail.soundId == null) return;
            const layer = this.mix?.layers.find(
                (candidate) => String(candidate.sound_id) === String(detail.soundId),
            );
            if (layer) layer.saved = Boolean(detail.saved);
        },

        /**
         * Remove the selected layer from an editable mix and settle the
         * carousel on its new neighbour. An editable mix always keeps one
         * slot: deleting its final layer replaces it with a blank the user can
         * fill, rather than leaving a carousel with no route back to adding.
         */
        async removeCurrent() {
            const mix = this.mix;
            if (!mix?.allowAdd || !mix.layers.length) return;
            mix.removeLayer(mix.currentIndex);
            if (!mix.layers.length) await mix.addBlankLayer();
            if (mix.layers.length) this.select(mix.currentIndex);
        },

        slideTo(target) {
            const el = this.$refs.carousel;
            const item = this.items()[target];
            if (!el || !item) return;
            const left = item.offsetLeft;
            // Already there: nothing will scroll, so nothing would ever arrive
            // to release a wait taken out here.
            if (Math.abs(el.scrollLeft - left) < 1) {
                this.release();
                return;
            }
            this.jumpingTo = target;
            clearTimeout(this.jumpTimer);
            this.jumpTimer = setTimeout(() => this.release(), JUMP_TIMEOUT_MS);
            el.scrollTo({ left, behavior: "smooth" });
        },

        /** Open on whichever layer the store is already holding, without animating there. */
        start() {
            const el = this.$refs.carousel;
            const item = this.items()[this.mix?.currentIndex ?? 0];
            if (el && item) {
                el.scrollTo({ left: item.offsetLeft, behavior: "instant" });
            }
            this.release();
            this.updateActive();
        },
    };
}
