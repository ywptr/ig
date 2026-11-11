export type MediaKind = "text" | "image" | "video" | "audio";
export type GenerationOperation = "text_to_image" | "image_to_image" | "reference_image_conditioning"
    | "multi_reference_conditioning" | "text_to_video" | "image_to_video" | "image_editing"
    | "inpainting" | "variation" | "upscale";
export interface ReferenceInputSlot {
    name: string;
    media_types: MediaKind[];
    mime_types: string[];
    roles: string[];
    min_count: number;
    max_count: number;
    max_bytes: number;
    strengths: ("loose" | "normal" | "strong")[];
}
export interface TechnicalCapabilities {
    version: 1;
    input_media: MediaKind[];
    output_media: MediaKind[];
    operations: GenerationOperation[];
    execution: {
        mode: "synchronous" | "asynchronous";
        polling_required: boolean;
        webhook_supported: boolean;
        cancellation: "none" | "queue_only" | "cooperative";
        provider_cancellation_supported: boolean;
    };
    reference_inputs?: ReferenceInputSlot[];
    reference_mime_types: string[];
    output_mime_types: string[];
    max_prompt_characters: number | null;
}
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
    usable: boolean;
    technical: TechnicalCapabilities | null;
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
    schema_version: 1;
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
    references?: { artifact_id: string; role: string; strength?: "loose" | "normal" | "strong" }[];
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
export interface ArtifactPreview {
    status: "missing" | "processing" | "ready" | "failed" | "unsupported";
    kind: "thumbnail" | "contact_sheet" | null;
    version: number;
    url: string | null;
    mime_type: string | null;
    width: number | null;
    height: number | null;
    size_bytes: number | null;
    frame_count: number | null;
    positions: number[];
    error_code: string | null;
}
export type ArtifactProvenance = "unknown" | "generated" | "uploaded";
export interface ArtifactInput {
    artifact_id: string;
    position: number;
    relation: "reference";
    role: string;
    strength: string | null;
}
export interface ArtifactPage {
    items: Artifact[];
    next_cursor: string | null;
    has_more: boolean;
}
export interface Artifact {
    preview?: ArtifactPreview;
    provenance?: ArtifactProvenance;
    media_properties?: { width: number | null; height: number | null; duration_seconds: number | null; fps: number | null };
    sha256?: string | null;
    generation?: { job_id: string; execution_id: string } | null;
    inputs?: ArtifactInput[];
    artifact_id: string;
    media_type: MediaKind;
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
