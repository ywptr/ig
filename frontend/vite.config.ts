import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";

const tlsCert = process.env.IG_DEV_TLS_CERT;
const tlsKey = process.env.IG_DEV_TLS_KEY;
if (Boolean(tlsCert) !== Boolean(tlsKey)) {
    throw new Error("IG development HTTPS requires both IG_DEV_TLS_CERT and IG_DEV_TLS_KEY");
}

export default defineConfig({
    plugins: [react(), {
        name: "ig-editor-development-entry",
        configureServer(server) {
            server.middlewares.use((request, response, next) => {
                if (request.url === "/assets/embed.js") {
                    response.setHeader("Content-Type", "text/javascript");
                    response.end(`import RefreshRuntime from '/@react-refresh';
RefreshRuntime.injectIntoGlobalHook(window);
window.$RefreshReg$ = () => {}; window.$RefreshSig$ = () => type => type;
window.__vite_plugin_react_preamble_installed__ = true;
await import('/@vite/client'); await import('/src/embed.tsx');`);
                } else if (request.url === "/assets/style.css") {
                    response.setHeader("Content-Type", "text/css");
                    response.end('@import "/src/index.css"; @import "/src/App.css";');
                } else next();
            });
        },
    }],
    build: {
        cssCodeSplit: false,
        rollupOptions: {
            input: { main: "index.html", embed: "src/embed.tsx" },
            output: { entryFileNames: chunk => chunk.name === "embed" ? "assets/embed.js" : "assets/[name]-[hash].js",
                assetFileNames: asset => asset.names.some(name => name.endsWith(".css")) ? "assets/style.css" : "assets/[name]-[hash][extname]" },
        },
    },

    server: {
        host: "0.0.0.0",
        https: tlsCert && tlsKey ? {
            cert: readFileSync(tlsCert),
            key: readFileSync(tlsKey),
        } : undefined,
        // Docker development only; browser requests remain same-origin.
        // Preserve Host so IG resolves the browser's TenantDomain.
        ...(process.env.IG_DEV_API_TARGET ? {
            proxy: Object.fromEntries(["/v1", "/v2", "/health", "/embed/generation"].map(path => [path, {
                target: process.env.IG_DEV_API_TARGET,
                changeOrigin: false,
            }])),
        } : {}),
        allowedHosts: ["192.168.0.99", "5480-deb13", "5480-deb13.lan", "5480-deb13.lan.com"],
    },
});
