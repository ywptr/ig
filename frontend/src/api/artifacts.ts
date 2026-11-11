import { requestJSON } from "./client";
import type { Artifact, ArtifactPage, ArtifactProvenance, MediaKind } from "../types/media";

export function artifactPreviewUrl(id: string) { return `/v2/artifacts/${encodeURIComponent(id)}/preview`; }
export function artifactContentUrl(id: string) { return `/v2/artifacts/${encodeURIComponent(id)}/content`; }
export function getArtifact(id: string, signal?: AbortSignal) {
    return requestJSON<Artifact>(`/v2/artifacts/${encodeURIComponent(id)}`, { signal });
}

export interface ArtifactQuery {
    page_size?: number;
    cursor?: string;
    media_type?: MediaKind;
    provenance?: ArtifactProvenance;
    q?: string;
    created_after?: string;
    created_before?: string;
    order?: "asc" | "desc";
}
export function listArtifactPage(query: ArtifactQuery = {}, signal?: AbortSignal) {
    const params = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
        if (value !== undefined) params.set(key, String(value));
    }
    return requestJSON<ArtifactPage>(`/v2/artifacts/page?${params}`, { signal });
}

export function uploadArtifact(file: File, signal?: AbortSignal) {
    const body = new FormData();
    body.append("file", file);
    body.append("origin", "reference");
    return requestJSON<Artifact>("/v2/artifacts/uploads", { method: "POST", body, signal });
}
