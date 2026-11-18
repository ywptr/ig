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

export interface GovernedModel {
    id: string;
    display_name: string;
    known: boolean;
    supported: boolean;
    enabled: boolean;
    available: boolean;
    usable: boolean;
    lifecycle: { deprecated: boolean; sunset_at: string | null; replacement_model: string | null };
}
export interface GovernedMedia {
    media_type: string;
    restricted: boolean;
    default_model: string | null;
    default_override: string | null;
    configuration_error: string | null;
    models: GovernedModel[];
}
export interface GovernedProvider {
    id: string;
    display_name: string;
    enabled: boolean;
    has_credentials: boolean;
    media: GovernedMedia[];
}
export interface ModelGovernance { providers: GovernedProvider[] }
export function getModelGovernance(signal?: AbortSignal) {
    return requestJSON<ModelGovernance>("/v2/tenant/model-governance", { signal });
}
export function saveModelGovernance(provider: string, media: string, models: string[] | null, defaultModel: string | null) {
    return requestJSON<ModelGovernance>(`/v2/tenant/model-governance/${encodeURIComponent(provider)}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ media_type: media, enabled_models: models, default_model: defaultModel }),
    });
}
