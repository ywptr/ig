import type { SpeechCapabilities, SpeechOptions } from "../types/media";
import { requestJSON } from "./client";

export interface WorkflowModel {
    provider: string;
    model: string;
    label: string;
    speech?: SpeechCapabilities;
}

export interface WorkflowPlanStep {
    operation: string;
    output: string;
    requires_image: boolean;
    models: WorkflowModel[];
}

export interface WorkflowPreset {
    id: string;
    label: string;
    available: boolean;
    experimental: boolean;
    reason: string | null;
    steps: WorkflowPlanStep[];
}

interface WorkflowOutput {
    artifact_id: string;
    media_type: string;
    filename: string | null;
    mime_type: string | null;
}

export interface WorkflowRun {
    workflow_id: string;
    status: string;
    preset: string;
    steps: {
        step_id: string;
        operation: string;
        status: string;
        error?: string | null;
        retryable?: boolean;
        dependencies: string[];
        selection?: Omit<WorkflowModel, "label" | "speech"> | null;
        speech?: SpeechOptions | null;
        job_ids: string[];
        inputs: Record<string, { kind: string; step_id?: string; artifact_id?: string }>;
        outputs: WorkflowOutput[];
    }[];
}

const endpoint = "/v2/workflows";

export function getWorkflowPresets(signal: AbortSignal) {
    return requestJSON<WorkflowPreset[]>(`${endpoint}/presets`, { signal });
}

export function getWorkflow(id: string, signal?: AbortSignal) {
    return requestJSON<WorkflowRun>(`${endpoint}/${encodeURIComponent(id)}`, { signal });
}

export function submitWorkflow(input: {
    preset: string;
    operation?: string;
    prompt: string;
    dialogue?: string;
    image_artifact_id?: string;
    selection?: Omit<WorkflowModel, "label">;
    voice_selection?: Omit<WorkflowModel, "label" | "speech">;
    speech?: SpeechOptions;
}, key: string) {
    return requestJSON<WorkflowRun>(endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Idempotency-Key": key },
        body: JSON.stringify(input),
    });
}

export function updateWorkflow(id: string, action: "cancel" | "retry") {
    return requestJSON<WorkflowRun>(`${endpoint}/${encodeURIComponent(id)}/${action}`, { method: "POST" });
}
