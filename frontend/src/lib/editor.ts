import { INITIAL_FORM, resolveLabForm, type LabForm } from "./generation";
import type { Artifact, MediaCapabilities, MediaGenerationRequest, MediaType } from "../types/media";

export interface EditorPolicy { allowed_media: MediaType[]; allowed_operations: string[]; reference_editing: boolean; upload: boolean; model_switching: boolean }
export interface EditorBootstrap {
    session_id: string; correlation_id: string; parent_origin: string; expires_at: string;
    draft: MediaGenerationRequest; policy: EditorPolicy; callback_context: Record<string, string>; draft_errors: string[];
}
export function editorForm(draft: MediaGenerationRequest, artifacts: Artifact[]): LabForm {
    const output = draft.output as Record<string, unknown>;
    return { ...INITIAL_FORM, mode: draft.interface_level ?? "hard", media: draft.media_type, prompt: draft.intent.description,
        provider: draft.advanced?.provider ?? "", model: draft.advanced?.model ?? "",
        aspect: String(output.aspect_ratio ?? (draft.media_type === "video" ? "16:9" : "")),
        resolution: String(output.resolution ?? (draft.media_type === "video" ? "720p" : "")),
        duration: typeof output.duration_seconds === "number" ? output.duration_seconds : draft.media_type === "video" ? 5 : null,
        audio: output.audio === true, count: typeof output.count === "number" ? output.count : null,
        quality: String(output.quality ?? ""), style: String(output.style ?? ""), purpose: String(output.purpose ?? "general"),
        references: (draft.references ?? []).map(ref => {
            const artifact = artifacts.find(item => item.artifact_id === ref.artifact_id);
            if (!artifact) throw new Error("A draft reference is unavailable. Request a corrected launch.");
            return { ...ref, role: ref.role ?? (draft.media_type === "video" ? "first_frame" : "previous_output"),
                ...(draft.media_type === "image" ? { strength: ref.strength ?? "normal" as const } : {}), artifact };
        }), mentions: [],
    };
}

export function editorFormErrors(capabilities: MediaCapabilities, form: LabForm): string[] {
    const resolved = resolveLabForm(capabilities, form);
    const differences = (["media", "provider", "model", "aspect", "resolution", "duration", "audio", "count", "quality", "style"] as const)
        .filter(key => form[key] !== "" && form[key] !== null && resolved.values[key] !== form[key]);
    const errors = differences.map(key => `Draft ${key} is unavailable. Choose a supported value; the original draft has been preserved.`);
    if (form.mode === "easy" && form.media === "image" && ((form.style && form.style !== "auto") ||
        (form.quality && form.quality !== "standard") || (form.count !== null && form.count !== resolved.controls?.count.min))) {
        errors.push("Choose Medium or Hard mode to preserve the draft style, quality or count.");
    }
    return errors;
}

export function restrictedCapabilities(caps: MediaCapabilities, policy: EditorPolicy): MediaCapabilities {
    return { ...caps, media_types: caps.media_types.filter(media => policy.allowed_media.includes(media.id as MediaType)) };
}

export function editorEvent(session: EditorBootstrap, type: string, payload: Record<string, unknown> = {}) {
    const fields: Record<string, string[]> = {
        "ig.editor.ready": [], "ig.editor.submitted": ["attempt_id", "job_id", "media_type"],
        "ig.editor.completed": ["attempt_id", "job_id", "execution_ids", "media_type", "artifacts"],
        "ig.editor.canceled": ["attempt_id", "job_id"], "ig.editor.error": ["attempt_id", "job_id", "code"],
        "ig.editor.closed": ["running_job_ids"],
    };
    const safe = Object.fromEntries((fields[type] ?? []).filter(key => payload[key] !== undefined).map(key => [key, payload[key]]));
    return { ...safe, type, version: 1, session_id: session.session_id, correlation_id: session.correlation_id };
}
export function validParentMessage(event: MessageEvent, session: EditorBootstrap, target: Window | null) {
    return event.origin === session.parent_origin && event.source === target && event.data?.version === 1
        && event.data?.session_id === session.session_id && event.data?.type === "ig.editor.close";
}
