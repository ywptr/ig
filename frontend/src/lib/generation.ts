import { compilePrompt, type ArtifactMention } from "./mentions";
import { referenceErrors, sharedReferenceSlots, structuredReferences, type Attachment } from "./references";
import type { Artifact, GenerationCapabilities, GenerationResult, GenerationSubmission, InterfaceLevel,
    MediaCapabilities, MediaCapability, MediaGenerationRequest, MediaModel, MediaType } from "../types/media";
import type { JobDetail } from "../types/Job";
import type { JobExecution } from "../api/executions";

export const POLL_INTERVAL_MS = 3000;
export function isActiveStatus(status: string) { return ["queued", "running", "waiting", "polling", "cancel_requested"].includes(status); }
export function humanize(value: string) { return value.replaceAll("_", " ").replace(/\b\w/g, c => c.toUpperCase()); }
function record(value: unknown): Record<string, unknown> { return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
function text(value: unknown): string | null { return typeof value === "string" && value.length > 0 ? value : null; }
export function generationMediaType(capability: string): MediaType | null {
    return capability === "image.generate" ? "image" : capability === "video.generate" ? "video" : null;
}

export interface LabForm {
    mentions: ArtifactMention[];
    references: Attachment[];
    mode: InterfaceLevel; media: MediaType; prompt: string; purpose: string;
    provider: string; model: string; aspect: string; quality: string; style: string;
    resolution: string; duration: number | null; audio: boolean; count: number | null;
}
export const INITIAL_FORM: LabForm = {
    mentions: [], references: [],
    mode: "easy", media: "image", prompt: "", purpose: "general", provider: "", model: "",
    aspect: "", quality: "", style: "", resolution: "", duration: null, audio: false, count: null,
};

export function eligibleModels(media: MediaCapability, mode: InterfaceLevel): MediaModel[] {
    return media.providers.filter(provider => provider.available).flatMap(provider => provider.models)
        .filter(model => model.available && model.supported_interface_levels.includes(mode)
            && model.capabilities.text_to_media && model.capabilities.layout_types.includes("single"));
}
export function availableMedia(capabilities: MediaCapabilities, mode: InterfaceLevel) {
    return capabilities.media_types.filter(media => (media.id === "image" || media.id === "video")
        && media.available && eligibleModels(media, mode).length > 0);
}

export function sharedControls(models: MediaModel[]): GenerationCapabilities | null {
    if (!models.length) return null;
    const controls = models.map(model => model.capabilities);
    const first = controls[0];
    const common = (key: "aspect_ratios" | "resolutions" | "quality_profiles" | "styles" | "layout_types") =>
        first[key].filter(value => controls.every(control => control[key].includes(value)));
    const count = { min: Math.max(...controls.map(c => c.count.min)), max: Math.min(...controls.map(c => c.count.max)) };
    if (count.min > count.max) return null;
    const durations = controls.map(c => c.duration_seconds);
    const duration = durations.every(value => value !== null)
        ? { min: Math.max(...durations.map(value => value!.min)), max: Math.min(...durations.map(value => value!.max)) } : null;
    if (duration && duration.min > duration.max) return null;
    return { ...first, count, duration_seconds: duration, audio: controls.every(c => c.audio),
        aspect_ratios: common("aspect_ratios"), resolutions: common("resolutions"),
        quality_profiles: common("quality_profiles"), styles: common("styles"), layout_types: common("layout_types") };
}

function choice(current: string, options: string[], preferred = "") {
    return options.includes(current) ? current : options.includes(preferred) ? preferred : options[0] ?? "";
}
function bounded(value: number | null, range: { min: number; max: number } | null) {
    return range ? Math.max(range.min, Math.min(range.max, Number.isFinite(value) && value !== null ? Math.round(value) : range.min)) : null;
}

export function resolveLabForm(capabilities: MediaCapabilities, form: LabForm) {
    const mediaOptions = availableMedia(capabilities, form.mode);
    const media = mediaOptions.find(item => item.id === form.media) ?? mediaOptions[0];
    const rules = capabilities.interface_levels[form.mode];
    const providers = media?.providers.filter(provider => provider.available && provider.models.some(model =>
        eligibleModels(media, form.mode).includes(model) && model.selectable_in.includes(form.mode))) ?? [];
    const provider = rules.explicit_provider_selection ? providers.find(item => item.id === form.provider) : undefined;
    const models = provider?.models.filter(model => model.available && model.supported_interface_levels.includes(form.mode)
        && model.selectable_in.includes(form.mode) && model.capabilities.text_to_media && model.capabilities.layout_types.includes("single")) ?? [];
    const selectedModel = rules.explicit_model_selection ? models.find(model => model.id === form.model) : undefined;
    // Auto never predicts IG routing: show only controls shared by eligible models.
    const controls = sharedControls(selectedModel ? [selectedModel] : provider ? models : media ? eligibleModels(media, form.mode) : []);
    const referenceModels = selectedModel ? [selectedModel] : provider ? models.filter(model => model.id === provider.default_model) : media ? eligibleModels(media, form.mode) : [];
    const referenceSlots = sharedReferenceSlots(referenceModels.map(model => model.technical?.reference_inputs ?? []));
    const validDefault = !provider || !!(provider.default_model_available && models.some(model => model.id === provider.default_model));
    const values = { ...form, media: (media?.id ?? form.media) as MediaType,
        provider: provider?.id ?? "", model: selectedModel?.id ?? "",
        purpose: choice(form.purpose, media?.purposes ?? [], "general"),
        aspect: choice(form.aspect, controls?.aspect_ratios ?? []),
        quality: choice(form.quality, controls?.quality_profiles ?? [], "standard"),
        style: choice(form.style, controls?.styles ?? [], "auto"),
        resolution: choice(form.resolution, controls?.resolutions ?? []),
        duration: bounded(form.duration, controls?.duration_seconds ?? null),
        count: bounded(form.count, controls?.count ?? null), audio: !!controls?.audio && form.audio,
    };
    return { mediaOptions, media, providers, provider, models, controls, values, referenceSlots,
        usable: !!media && !!controls && (!!selectedModel || validDefault) };
}

export function buildMediaRequest(capabilities: MediaCapabilities, form: LabForm): MediaGenerationRequest {
    const { values, usable, controls, referenceSlots } = resolveLabForm(capabilities, form);
    if (!usable) throw new Error("Choose an available media type and a supported model or default.");
    if (!values.prompt.trim()) throw new Error("Describe what you want to generate.");
    const errors = referenceErrors(form.references, referenceSlots);
    if (errors.length) throw new Error(errors[0]);
    const base = { ...(form.references.length ? { references: structuredReferences(form.references) } : {}), interface_level: values.mode, intent: { description: compilePrompt(form, referenceSlots) },
        ...(values.provider ? { advanced: { provider: values.provider, ...(values.model ? { model: values.model } : {}) } } : {}) };
    const aspect = values.aspect ? { aspect_ratio: values.aspect } : {};
    if (values.media === "image") return { ...base, media_type: "image", output: {
        ...aspect, ...(values.purpose ? { purpose: values.purpose } : {}),
        ...(values.count !== null ? { count: values.mode === "easy" ? controls!.count.min : values.count } : {}),
        ...(values.mode !== "easy" ? { ...(values.quality ? { quality: values.quality } : {}), ...(values.style ? { style: values.style } : {}) } : {}),
    } };
    return { ...base, media_type: "video", output: { ...aspect, audio: values.audio,
        ...(values.resolution ? { resolution: values.resolution } : {}),
        ...(values.duration !== null ? { duration_seconds: values.duration } : {}),
    } };
}

export function latestExecution(executions: JobExecution[]) {
    return executions.reduce<JobExecution | undefined>((latest, current) => !latest || current.attempt > latest.attempt ? current : latest, undefined);
}
export function executionArtifactId(execution?: JobExecution): string | null {
    return execution?.status === "completed" ? text(execution.metadata?.artifact_id) : null;
}
export function resultFromJob(job: JobDetail, execution?: JobExecution, artifact: Artifact | null = null): GenerationResult {
    const media = generationMediaType(job.capability);
    if (!media) throw new Error("This Job is not a supported media generation.");
    const request = media === "video" ? record(job.input.request) : job.input;
    const selection = record(job.input.selection);
    const output = record(request.output);
    const metadata = artifact?.metadata ?? {};
    return { job_id: job.job_id, request_id: text(job.input.request_id) ?? undefined, media_type: media,
        status: job.status, prompt: text(record(request.intent).description) ?? text(job.input.prompt) ?? "Prompt unavailable",
        provider: execution?.provider ?? text(metadata.provider) ?? text(selection.provider),
        model: execution?.model ?? text(metadata.model) ?? text(selection.model),
        purpose: media === "image" ? text(selection.purpose) ?? text(output.purpose) : null,
        created_at: job.created_at, completed_at: job.completed_at,
        error: job.error_message ?? execution?.error_message ?? null, artifact };
}
export function matchesSubmission(result: GenerationResult, receipt: GenerationSubmission) {
    return receipt.job_id ? result.job_id === receipt.job_id
        : receipt.media_type === "image" && !!receipt.request_id && result.media_type === "image" && result.request_id === receipt.request_id;
}
export function pendingReceipt(receipt: GenerationSubmission, results: GenerationResult[], now = Date.now()) {
    return !results.some(result => matchesSubmission(result, receipt)) && now - Date.parse(receipt.submitted_at) < 60_000;
}
