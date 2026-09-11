import '../css/main.css';
import Alpine from 'alpinejs';
import htmx from 'htmx.org/dist/htmx.esm.js';
import { Observer } from 'tailwindcss-intersect';
import { layerCarousel } from './layer-carousel.js';
import { installExploreLayerLinks } from './explore-layer-links.js';
import { installSoundscapeBridge } from './soundscape-store.js';
import { installSoundSelectorGuard } from './sound-selector-guard.js';
import { studioBuilder } from './studio-builder.js';
import { trimTrack } from './trim-track.js';
import { voteDisplay } from './vote-display.js';
import './explore-fonts.js';

window.htmx = htmx;
window.Alpine = Alpine;
installSoundscapeBridge();
installSoundSelectorGuard();
installExploreLayerLinks();
// Components have to be registered before start(), or the elements that use
// them are already past initialisation by the time the name exists.
Alpine.data('layerCarousel', layerCarousel);
Alpine.data('studioBuilder', studioBuilder);
Alpine.data('trimTrack', trimTrack);
Alpine.data('voteDisplay', voteDisplay);
Alpine.start();

// Start the intersection observer for scroll animations
Observer.start();
