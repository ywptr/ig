import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { readFileSync } from "node:fs";

const tlsCert = process.env.IG_DEV_TLS_CERT;
const tlsKey = process.env.IG_DEV_TLS_KEY;
if (Boolean(tlsCert) !== Boolean(tlsKey)) {
    throw new Error("IG development HTTPS requires both IG_DEV_TLS_CERT and IG_DEV_TLS_KEY");
}

export default defineConfig({
    plugins: [react()],

    server: {
        host: "0.0.0.0",
        https: tlsCert && tlsKey ? {
            cert: readFileSync(tlsCert),
            key: readFileSync(tlsKey),
        } : undefined,
        // Docker development only; browser requests remain same-origin.
        // Preserve Host so IG resolves the browser's TenantDomain.
        ...(process.env.IG_DEV_API_TARGET ? {
            proxy: Object.fromEntries(["/v1", "/v2", "/health"].map(path => [path, {
                target: process.env.IG_DEV_API_TARGET,
                changeOrigin: false,
            }])),
        } : {}),
        allowedHosts: ["192.168.0.99", "5480-deb13", "5480-deb13.lan", "5480-deb13.lan.com"],
    },
});
