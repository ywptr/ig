import { requestJSON } from "./client";

export interface TenantInfo {
    tenant_id: string;
    slug: string;
    name: string;
    status: string;
    membership: { role: string; status: string };
}
export interface RetentionPolicy {
    generated_artifact_retention_enabled: boolean;
    generated_artifact_retention_days: number;
    retention_policy_source: "tenant" | "storage_default";
}
export interface DriveCapability { google_drive_enabled: boolean }
interface ProviderConfig {
    provider: string;
    enabled: boolean;
    has_credentials: boolean;
    config: Record<string, unknown>;
}
export interface ProviderSummary {
    provider: string;
    enabled: boolean;
    has_credentials: boolean;
    defaults: { media: string; model: string }[];
}
export type RetentionScope = "managed" | "external";
const retentionPath = (scope: RetentionScope) => `/v2/tenant/settings/${scope === "managed" ? "retention" : "external-retention"}`;

export function getTenantInfo(signal?: AbortSignal) {
    return requestJSON<TenantInfo>("/v2/tenant", { signal });
}
export function getDriveCapability(signal?: AbortSignal) {
    return requestJSON<DriveCapability>("/v2/tenant/settings/storage", { signal });
}
export function saveDriveCapability(enabled: boolean) {
    return requestJSON<DriveCapability>("/v2/tenant/settings/storage", { method: "PATCH",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ google_drive_enabled: enabled }) });
}
export function getRetentionPolicy(scope: RetentionScope, signal?: AbortSignal) {
    return requestJSON<RetentionPolicy>(retentionPath(scope), { signal });
}
export function saveRetentionPolicy(scope: RetentionScope, enabled: boolean, days: string) {
    const duration = Number(days);
    if (!days.trim() || !Number.isInteger(duration) || duration < 1 || duration > 3650) {
        throw new Error("Enter a whole number of days from 1 to 3650.");
    }
    return requestJSON<RetentionPolicy>(retentionPath(scope), { method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ generated_artifact_retention_enabled: enabled, generated_artifact_retention_days: duration }) });
}
export async function getProviderSummaries(signal?: AbortSignal): Promise<ProviderSummary[]> {
    const providers = await requestJSON<ProviderConfig[]>("/v2/tenant/providers", { signal });
    // Display only supported defaults, never arbitrary config or credential fields.
    return providers.map(row => {
        const defaults: ProviderSummary["defaults"] = [];
        if (typeof row.config?.default_model === "string") defaults.push({ media: "Default", model: row.config.default_model });
        const models = row.config?.default_models;
        if (models && typeof models === "object") {
            for (const media of ["image", "video", "audio"]) {
                const model = (models as Record<string, unknown>)[media];
                if (typeof model === "string") defaults.push({ media, model });
            }
        }
        return { provider: row.provider, enabled: row.enabled, has_credentials: row.has_credentials, defaults };
    });
}
