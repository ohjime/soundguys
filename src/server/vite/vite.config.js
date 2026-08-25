import { defineConfig } from 'vite';
import { resolve } from 'path';
import { networkInterfaces } from 'os';
import tailwindcss from '@tailwindcss/vite';

// Get the local IP address of the machine
function getLocalIp() {
    const nets = networkInterfaces();
    for (const name of Object.keys(nets)) {
        for (const net of nets[name]) {
            // Skip internal and non-IPv4 addresses
            if (net.family === 'IPv4' && !net.internal) {
                return net.address;
            }
        }
    }
    return 'localhost';
}

const localIp = getLocalIp();
console.log(`[vite] Using local IP: ${localIp}`);

export default defineConfig({
    base: "/static/",
    server: {
        // CSS asset URLs must point back to Vite in development. Without an
        // explicit origin, @font-face URLs become /static/assets/... and the
        // browser incorrectly asks Django on port 8000 for the source files.
        origin: `http://${localIp}:5173`,
        allowedHosts: [localIp, 'localhost', '127.0.0.1', '0.0.0.0'],
        host: '0.0.0.0', // Listen on all addresses
        port: 5173,
        cors: true,      // Allow the phone to request resources
        hmr: {
            host: localIp, // Dynamically set HMR to local IP
        },
    },
    build: {
        manifest: "manifest.json",
        outDir: resolve("./static"),
        assetsDir: "vite",
        rollupOptions: {
            input: {
                test: resolve("./assets/js/main.js"),
            },
            onwarn: (warning, warn) => (warning.code !== 'EVAL') ? warn(warning) : undefined // suppress eval warnings (@vue/devtools)

        }
    },
    plugins: [
        tailwindcss(),
    ]
})
