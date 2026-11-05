import { requestJSON } from "./client";
import type { Artifact } from "../types/media";

export function artifactContentUrl(id: string) { return `/v2/artifacts/${encodeURIComponent(id)}/content`; }
export function getArtifact(id: string, signal?: AbortSignal) {
    return requestJSON<Artifact>(`/v2/artifacts/${encodeURIComponent(id)}`, { signal });
}
