export type MediaType = "image" | "video";
export type InterfaceLevel = "easy" | "medium" | "hard";
export type JobStatus = "queued" | "running" | "waiting" | "polling" | "cancel_requested" | "canceled" | "completed" | "failed";
export interface Range { min: number; max: number }
export interface GenerationCapabilities {
    text_to_media: boolean;
    reference_roles: string[];
    max_references: number;
    aspect_ratios: string[];
    resolutions: string[];
    duration_seconds: Range | null;
    audio: boolean;
    count: Range;
    layout_types: string[];
    quality_profiles: string[];
    styles: string[];
}
export interface MediaModel {
    id: string;
    display_name: string;
    available: boolean;
    supported_interface_levels: InterfaceLevel[];
    selectable_in: InterfaceLevel[];
    capabilities: GenerationCapabilities;
    use_cases: string[];
}
export interface MediaProvider {
    id: string;
    display_name: string;
    configured: boolean;
    enabled: boolean;
    available: boolean;
    default_model: string | null;
    default_model_available: boolean;
    models: MediaModel[];
}
export interface MediaCapability {
    id: string;
    available: boolean;
    default_provider: string | null;
    providers: MediaProvider[];
    purposes: string[];
}
export interface MediaCapabilities {
    interface_levels: Record<InterfaceLevel, {
        explicit_provider_selection: boolean;
        explicit_model_selection: boolean;
    }>;
    media_types: MediaCapability[];
}
export interface ImageOutput {
    purpose?: string;
    aspect_ratio?: string;
    quality?: string;
    style?: string;
    count?: number;
}
export interface VideoOutput {
    aspect_ratio?: string;
    resolution?: string;
    duration_seconds?: number;
    audio: boolean;
}
export type MediaGenerationRequest = {
    interface_level: InterfaceLevel;
    intent: { description: string };
    advanced?: { provider: string; model?: string };
} & ({ media_type: "image"; output: ImageOutput } | { media_type: "video"; output: VideoOutput });
export interface MediaSubmission {
    media_type: MediaType;
    request_id: string;
    job_id?: string;
    status: JobStatus | "generating";
}
export interface GenerationSubmission extends MediaSubmission {
    submitted_at: string;
    prompt: string;
    purpose?: string;
}
export interface Artifact {
    artifact_id: string;
    media_type: MediaType;
    mime_type: string | null;
    filename: string | null;
    size_bytes: number | null;
    created_at: string;
    metadata: Record<string, unknown> | null;
}
export interface GenerationResult {
    job_id: string;
    request_id?: string;
    media_type: MediaType;
    status: JobStatus;
    prompt: string;
    provider: string | null;
    model: string | null;
    purpose: string | null;
    created_at: string;
    completed_at: string | null;
    error: string | null;
    artifact: Artifact | null;
}
