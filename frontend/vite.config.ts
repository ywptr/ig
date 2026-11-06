import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
    plugins: [react()],

    server: {
        host: "0.0.0.0",
        // Docker development only; browser requests remain same-origin.
        // Preserve Host so IG resolves the browser's TenantDomain.
        ...(process.env.IG_DEV_API_TARGET ? {
            proxy: Object.fromEntries(["/v1", "/v2", "/health"].map(path => [path, {
                target: process.env.IG_DEV_API_TARGET,
                changeOrigin: false,
            }])),
        } : {}),
    },
});
