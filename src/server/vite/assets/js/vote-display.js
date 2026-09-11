/** Metadata for the venue's current prediction, scoped to this card. */
export function voteDisplay(initialLayers = [], initialSleeping = false) {
    return {
        layers: initialLayers.map((layer) => ({ ...layer })),
        currentIndex: 0,
        title: "",
        choice: "",
        hasVoteAction: false,
        playerSleeping: Boolean(initialSleeping),
        activationMode: Boolean(initialSleeping),
        renderedActivationCard: Boolean(initialSleeping),
        activationComplete: false,
        activationError: "",
        voteError: "",
        voted: false,
        secondsLeft: 0,
        timer: null,

        init() {
            const payload = this.$el?.querySelector("[data-vote-layers] script");
            if (payload) {
                try {
                    const layers = JSON.parse(payload.textContent);
                    this.layers = Array.isArray(layers) ? layers : [];
                } catch {
                    this.layers = [];
                }
            }
            this.title = this.$el?.dataset.postTitle || "";
            this.choice = this.$el?.dataset.voteChoice || "";
            this.hasVoteAction = this.$el?.dataset.hasVoteAction === "true";
            this.playerSleeping = this.$el?.dataset.playerSleeping === "true";
            this.activationMode = this.playerSleeping;
            this.renderedActivationCard = this.activationMode;
            this.secondsLeft = Number(this.$el?.dataset.throttleSecondsLeft) || 0;
            if (this.secondsLeft > 0) this.startTimer();
        },

        destroy() {
            if (this.timer) clearInterval(this.timer);
        },

        get carouselSlides() {
            if (this.activationMode) return [];
            const soundSlides = this.layers.map((layer, index) => ({
                ...layer,
                kind: "layer",
                key: `layer:${layer.sound_id}`,
                indicatorLabel: String(index + 1),
                ariaLabel: `Show sound layer ${index + 1}`,
            }));
            if (!this.hasVoteAction) return soundSlides;
            return [{
                kind: "vote",
                key: "vote",
                indicatorLabel: "VOTE",
                ariaLabel: "Show voting",
            }, ...soundSlides];
        },
        get currentSlide() { return this.carouselSlides[this.currentIndex] ?? null; },
        get currentLayer() { return this.currentSlide?.kind === "layer" ? this.currentSlide : null; },
        get isVoteSlide() { return this.currentSlide?.kind === "vote"; },
        get isEmpty() { return this.layers.length === 0; },
        get isUpvote() { return this.choice === "1"; },
        get voteLabel() { return this.activationMode ? "ACTIVATE" : "VOTE"; },
        get votePastLabel() { return this.isUpvote ? "Upvoted" : "Downvoted"; },
        get isFirst() { return this.currentIndex === 0; },
        get isLast() { return this.currentIndex >= this.carouselSlides.length - 1; },
        get gainPercent() { return Math.round((this.currentLayer?.sound_gain ?? 0) * 100); },

        startTimer() {
            if (this.timer) clearInterval(this.timer);
            this.timer = setInterval(() => {
                this.secondsLeft = Math.max(0, this.secondsLeft - 1);
                if (this.secondsLeft === 0) {
                    clearInterval(this.timer);
                    this.timer = null;
                }
            }, 1000);
        },

        handleVoteSuccess() {
            this.voted = true;
            this.voteError = "";
            if (this.timer) clearInterval(this.timer);
            this.timer = null;
            this.secondsLeft = 0;
        },

        handlePlayerActivated(detail) {
            if (Array.isArray(detail?.layers)) {
                this.layers = detail.layers.map((layer) => ({ ...layer }));
            }
            this.playerSleeping = false;
            // A scheduler can put the room to sleep after an active card was
            // rendered but before its vote is submitted. The backend wakes it
            // instead of recording that stale vote. Keep that already-rendered
            // card in normal mode so its carousel can display the fresh layer;
            // only the dedicated activation card owns the confirmation view.
            this.activationMode = this.renderedActivationCard;
            this.activationComplete = this.renderedActivationCard;
            this.activationError = "";
            this.currentIndex = Math.min(
                this.currentIndex,
                Math.max(0, this.carouselSlides.length - 1),
            );
            if (this.timer) clearInterval(this.timer);
            this.timer = null;
            this.secondsLeft = 0;
        },

        handleActivationUnavailable(detail) {
            if (!this.renderedActivationCard) {
                this.reloadForActivation();
                return;
            }
            this.activationMode = true;
            this.activationComplete = false;
            this.activationError = detail?.message || "This room has no sounds available yet.";
        },

        handleVoteAuthUnavailable(detail) {
            this.voteError = detail?.message || "Could not vote anonymously. Please try again.";
        },

        reloadForActivation() {
            globalThis.location?.reload?.();
        },

        handleThrottle(seconds) {
            this.secondsLeft = Number(seconds) || 60;
            this.startTimer();
        },

        select(index) {
            this.currentIndex = Math.max(0, Math.min(this.carouselSlides.length - 1, index));
            const carousel = this.$refs?.carousel;
            const item = carousel?.querySelectorAll("[data-vote-layer]")[this.currentIndex];
            if (item) carousel.scrollTo({ left: item.offsetLeft, behavior: "instant" });
        },

        move(delta) { this.select(this.currentIndex + delta); },

        updateActive() {
            const carousel = this.$refs?.carousel;
            if (!carousel) return;
            const items = carousel.querySelectorAll("[data-vote-layer]");
            let distance = Infinity;
            for (let index = 0; index < items.length; index += 1) {
                const candidate = Math.abs(items[index].offsetLeft - carousel.scrollLeft);
                if (candidate < distance) {
                    distance = candidate;
                    this.currentIndex = index;
                }
            }
        },

        onLayerSaved(detail) {
            if (!detail || typeof detail.saved !== "boolean") return;
            for (const layer of this.layers) {
                if (String(layer.sound_id) === String(detail.soundId)) layer.saved = detail.saved;
            }
        },

        savePayload() {
            return JSON.stringify({
                title: this.title,
                layers: JSON.stringify(this.layers.map(({ sound_id, sound_gain }) => ({ sound_id, sound_gain }))),
            });
        },
    };
}
