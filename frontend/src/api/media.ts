import { requestJSON } from "./client";
import type { GenerationSubmission, MediaCapabilities, MediaGenerationRequest, MediaSubmission } from "../types/media";

function validRange(value: unknown): boolean {
    if (!value || typeof value !== "object") return false;
    const range = value as Record<string, unknown>;
    return typeof range.min === "number" && Number.isFinite(range.min) && typeof range.max === "number"
        && Number.isFinite(range.max) && range.min <= range.max;
}

export async function getMediaCapabilities(signal?: AbortSignal): Promise<MediaCapabilities> {
    const value = await requestJSON<MediaCapabilities>("/v2/media/capabilities", { signal });
    try {
        const strings = (values: unknown) => Array.isArray(values) && values.every(value => typeof value === "string");
        const validSlots = (slots: unknown) => slots === undefined || (Array.isArray(slots) && slots.every(slot =>
            slot && typeof slot.name === "string" && strings(slot.media_types) && slot.media_types.length > 0
            && strings(slot.mime_types) && slot.mime_types.length > 0 && strings(slot.roles) && slot.roles.length > 0
            && strings(slot.strengths) && slot.strengths.every((strength: string) => ["loose", "normal", "strong"].includes(strength))
            && Number.isInteger(slot.min_count) && slot.min_count >= 0 && Number.isInteger(slot.max_count)
            && slot.max_count >= Math.max(1, slot.min_count) && Number.isInteger(slot.max_bytes) && slot.max_bytes > 0));
        const valid = value.schema_version === 1 && ["easy", "medium", "hard"].every(level => {
            const rules = value.interface_levels[level as keyof typeof value.interface_levels];
            return typeof rules.explicit_provider_selection === "boolean" && typeof rules.explicit_model_selection === "boolean";
        }) && Array.isArray(value.media_types) && value.media_types.every(media =>
            typeof media.id === "string" && typeof media.available === "boolean" && strings(media.purposes)
            && Array.isArray(media.providers) && media.providers.every(provider =>
                typeof provider.id === "string" && typeof provider.display_name === "string" && typeof provider.available === "boolean"
                && Array.isArray(provider.models) && provider.models.every(model => {
                    const controls = model.capabilities;
                    const technical = model.technical;
                    return typeof model.usable === "boolean" && (technical === null || (
                        technical.version === 1 && validSlots(technical.reference_inputs) && strings(technical.input_media) && strings(technical.output_media)
                        && strings(technical.operations) && strings(technical.reference_mime_types)
                        && strings(technical.output_mime_types)
                        && ["synchronous", "asynchronous"].includes(technical.execution.mode)
                        && typeof technical.execution.polling_required === "boolean"
                        && typeof technical.execution.provider_cancellation_supported === "boolean"))
                        && typeof model.id === "string" && typeof model.display_name === "string" && typeof model.available === "boolean"
                        && strings(model.supported_interface_levels) && strings(model.selectable_in)
                        && typeof controls.text_to_media === "boolean" && typeof controls.audio === "boolean"
                        && [controls.aspect_ratios, controls.resolutions, controls.styles, controls.quality_profiles, controls.layout_types].every(strings)
                        && validRange(controls.count) && (controls.duration_seconds === null || validRange(controls.duration_seconds));
                })));
        if (valid) return value;
    } catch { /* Report invalid discovery data locally; preserve the other views. */ }
    throw new Error("IG returned an invalid capability document. Please retry.");
}

export async function submitMedia(request: MediaGenerationRequest, signal?: AbortSignal, attemptId?: string): Promise<GenerationSubmission> {
    const response = await requestJSON<MediaSubmission>("/v2/media/generations", {
        method: "POST", headers: { "Content-Type": "application/json", ...(attemptId ? { "Idempotency-Key": attemptId } : {}) }, body: JSON.stringify(request), signal,
    });
    return { ...response, submitted_at: new Date().toISOString(), prompt: request.intent.description,
        purpose: request.media_type === "image" ? request.output.purpose : undefined };
}
