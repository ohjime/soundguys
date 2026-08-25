// Vite discovers every local font folder containing font.css and bundles both
// its stylesheet and referenced font files for the public site.
import.meta.glob('../fonts/*/font.css', { eager: true });
