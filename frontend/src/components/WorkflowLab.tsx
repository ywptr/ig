import { useCallback, useEffect, useRef, useState } from "react";
import { artifactContentUrl, listArtifactPage } from "../api/artifacts";
import { safeMessage } from "../api/client";
import { getWorkflow, getWorkflowPresets, submitWorkflow, updateWorkflow, type WorkflowPlanStep, type WorkflowPreset, type WorkflowRun } from "../api/workflows";
import { useActivePolling } from "../hooks/useActivePolling";
import { ArtifactAudio } from "./ArtifactAudio";
import { SpeechControls } from "./SpeechControls";
import { resolveSpeechOptions } from "../lib/generation";
import type { Artifact, SpeechOptions } from "../types/media";
import { ArtifactPreviewImage } from "./ArtifactPreviewImage";

const active = (run: WorkflowRun) => ["queued", "running", "cancel_requested"].includes(run.status);
const label = (operation: string) => operation.replaceAll("_", " ");
const errorMessage = (error: unknown) => safeMessage(error instanceof Error ? error.message : null);

function WorkflowDetails({ initial }: { initial: WorkflowRun }) {
    const load = useCallback((signal: AbortSignal) => getWorkflow(initial.workflow_id, signal), [initial.workflow_id]);
    const { data: run, error, refresh } = useActivePolling(load, active, initial);
    const [actionError, setActionError] = useState("");
    const [busy, setBusy] = useState(false);

    async function act(action: "cancel" | "retry") {
        setBusy(true);
        setActionError("");
        try {
            await updateWorkflow(run.workflow_id, action);
            refresh();
        } catch (failure) {
            setActionError(errorMessage(failure));
        } finally {
            setBusy(false);
        }
    }

    return <section className="settings-card">
        <h2>Workflow run</h2>
        <p><code>{run.workflow_id}</code></p>
        <p role="status">Status {label(run.status)}</p>
        {active(run) && run.status !== "cancel_requested" &&
            <button type="button" disabled={busy} onClick={() => void act("cancel")}>Cancel workflow</button>}
        {run.status === "failed" && run.steps.some(step => step.retryable) &&
            <button type="button" disabled={busy} onClick={() => void act("retry")}>Retry failed step</button>}
        {(actionError || error) && <p role="alert">{actionError || error}</p>}
        <ol>
            {run.steps.map((step, index) => <li key={step.step_id}>
                <h3>{label(step.operation)}</h3>
                <p>{label(step.status)}{step.job_ids.length ? ` · Attempt ${step.job_ids.length}` : ""}</p>
                {step.selection && <p>{step.selection.provider} / {step.selection.model}</p>}
                {step.speech?.voice && <p>Voice {step.speech.voice}</p>}
                {step.error && <p>{step.error}</p>}
                {Object.entries(step.inputs).filter(([, input]) => input.kind !== "text").map(([name, input]) => {
                    const source = run.steps.find(other => other.step_id === input.step_id);
                    const id = input.artifact_id ?? source?.outputs[0]?.artifact_id;
                    return <p key={name}>{label(name)} {id ? <a href={artifactContentUrl(id)}>{id}</a> : `from ${input.step_id}`}</p>;
                })}
                {step.outputs.map(output => <div key={output.artifact_id}>
                    <p>{index === run.steps.length - 1 ? "Final" : "Intermediate"} {output.media_type} · {output.filename || output.artifact_id}{output.mime_type && ` · ${output.mime_type}`}</p>
                    {output.media_type === "audio" ? <ArtifactAudio artifactId={output.artifact_id} /> : <ArtifactPreviewImage artifactId={output.artifact_id}
                        originalUrl={artifactContentUrl(output.artifact_id)} alt={`${output.media_type} result preview`} />}
                    <p><a href={artifactContentUrl(output.artifact_id)} target="_blank" rel="noreferrer">Open {output.media_type} original</a>
                        {" · "}<code>{output.artifact_id}</code></p>
                </div>)}
            </li>)}
        </ol>
    </section>;
}

export function WorkflowLab() {
    const [presets, setPresets] = useState<WorkflowPreset[]>([]);
    const [presetId, setPresetId] = useState("single_generation");
    const [operation, setOperation] = useState("text_to_image");
    const [modelIndex, setModelIndex] = useState("");
    const [voiceModelIndex, setVoiceModelIndex] = useState("");
    const [prompt, setPrompt] = useState("");
    const [speechOptions, setSpeechOptions] = useState<SpeechOptions>({});
    const [dialogue, setDialogue] = useState("");
    const [imageId, setImageId] = useState("");
    const [images, setImages] = useState<Artifact[]>([]);
    const [imageError, setImageError] = useState("");
    const [run, setRun] = useState<WorkflowRun | null>(null);
    const [inspection, setInspection] = useState(0);
    const [inspectId, setInspectId] = useState(() => new URLSearchParams(window.location.search).get("id") ?? "");
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    const submission = useRef<{ serialized: string; key: string } | null>(null);

    const preset = presets.find(item => item.id === presetId);
    const isVoice = presetId === "voice_then_video";
    const plan = preset?.steps.filter(step => isVoice || step.operation === operation) ?? [];
    const outputStep = plan.at(-1);
    const speechStep = plan.find(step => step.operation === "text_to_speech");
    const speech = speechStep?.models[Number((isVoice ? voiceModelIndex : modelIndex) || "0")]?.speech;
    const requiresImage = outputStep?.requires_image ?? false;

    useEffect(() => {
        const controller = new AbortController();
        void getWorkflowPresets(controller.signal).then(setPresets)
            .catch(failure => { if (!controller.signal.aborted) setError(errorMessage(failure)); });
        return () => controller.abort();
    }, []);

    useEffect(() => {
        if (!requiresImage) return;
        const controller = new AbortController();
        void listArtifactPage({ media_type: "image", page_size: 30 }, controller.signal)
            .then(page => { setImages(page.items); setImageError(""); })
            .catch(failure => { if (!controller.signal.aborted) setImageError(errorMessage(failure)); });
        return () => controller.abort();
    }, [requiresImage]);

    useEffect(() => {
        const id = new URLSearchParams(window.location.search).get("id");
        if (!id) return;
        const controller = new AbortController();
        void getWorkflow(id, controller.signal)
            .then(next => { if (!controller.signal.aborted) setRun(next); })
            .catch(failure => { if (!controller.signal.aborted) setError(errorMessage(failure)); });
        return () => controller.abort();
    }, []);

    function show(next: WorkflowRun) {
        setRun(next);
        setInspection(value => value + 1);
        setInspectId(next.workflow_id);
        const url = new URL(window.location.href);
        url.searchParams.set("id", next.workflow_id);
        window.history.replaceState(window.history.state, "", url);
    }

    function modelAt(models: WorkflowPlanStep["models"], index: string) {
        if (!index) return undefined;
        const selected = models[Number(index)];
        return selected ? { provider: selected.provider, model: selected.model } : undefined;
    }

    async function submit() {
        setBusy(true);
        setError("");
        const input = {
            preset: presetId,
            operation: isVoice ? undefined : operation,
            prompt,
            dialogue: isVoice ? dialogue : undefined,
            image_artifact_id: requiresImage ? imageId.trim() : undefined,
            selection: modelAt(outputStep?.models ?? [], modelIndex),
            voice_selection: isVoice ? modelAt(plan[0]?.models ?? [], voiceModelIndex) : undefined,
            speech: speech ? resolveSpeechOptions(speech, speechOptions) : undefined,
        };
        const serialized = JSON.stringify(input);
        if (serialized !== submission.current?.serialized) {
            submission.current = { serialized, key: crypto.randomUUID() };
        }
        try {
            show(await submitWorkflow(input, submission.current.key));
            submission.current = null;
        } catch (failure) {
            setError(errorMessage(failure));
        } finally {
            setBusy(false);
        }
    }

    async function inspect() {
        setBusy(true);
        setError("");
        try {
            show(await getWorkflow(inspectId.trim()));
        } catch (failure) {
            setError(errorMessage(failure));
        } finally {
            setBusy(false);
        }
    }

    return <div className="settings-page workflow-lab">
        <h1>Workflow Lab</h1>
        <form className="settings-card" onSubmit={event => { event.preventDefault(); void submit(); }}>
            <label>Workflow preset
                <select value={presetId} onChange={event => {
                    setPresetId(event.target.value); setModelIndex(""); setVoiceModelIndex("");
                }}>
                    {presets.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}
                </select>
            </label>
            {preset && !isVoice && <label>Operation
                <select value={operation} onChange={event => { setOperation(event.target.value); setModelIndex(""); }}>
                    {preset.steps.map(step => <option key={step.operation} value={step.operation}>{label(step.operation)}</option>)}
                </select>
            </label>}
            {plan.length > 0 && <ol aria-label="Planned steps">
                {plan.map((step, index) => <li key={step.operation}>
                    {label(step.operation)} → {step.output}
                    {isVoice && ` · ${step.models.length ? "Available" : "Unavailable"}`}
                    {isVoice && index === 1 && " · Requires a first-frame image and the voice step’s audio"}
                </li>)}
            </ol>}
            <label>{isVoice ? "Shot prompt" : "Prompt"}<textarea required maxLength={!isVoice && speech ? speech.max_text_characters : 32000} value={prompt}
                onChange={event => setPrompt(event.target.value)} /></label>
            {isVoice && <label>Dialogue<textarea required maxLength={speech?.max_text_characters ?? 32000} value={dialogue}
                onChange={event => setDialogue(event.target.value)} /></label>}
            {requiresImage && <label>Image Artifact ID
                <input required maxLength={36} list="workflow-image-artifacts" value={imageId} onChange={event => setImageId(event.target.value)} />
                <datalist id="workflow-image-artifacts">{images.map(image =>
                    <option key={image.artifact_id} value={image.artifact_id}>{image.filename}</option>)}</datalist>
            </label>}
            {requiresImage && imageError && <p role="status">{imageError} You can enter an Artifact ID.</p>}
            {plan.map((step, index) => {
                const voice = isVoice && index === 0;
                return step.models.length > 0 && <label key={step.operation}>{voice ? "Voice provider / model" : "Provider / model"}
                    <select value={voice ? voiceModelIndex : modelIndex} onChange={event =>
                        (voice ? setVoiceModelIndex : setModelIndex)(event.target.value)}>
                        <option value="">Automatic selection</option>
                        {step.models.map((model, i) => <option key={`${model.provider}/${model.model}`} value={i}>{model.provider} / {model.label}</option>)}
                    </select>
                </label>;
            })}
            {speech && <SpeechControls capabilities={speech} values={resolveSpeechOptions(speech, speechOptions)}
                onChange={setSpeechOptions} />}
            {preset && (!preset.available || !outputStep?.models.length) && <p role="status">
                {preset.experimental && "Experimental. "}{preset.reason ?? "No compatible tenant model is enabled for this operation."}
            </p>}
            <button type="submit" disabled={busy || !preset?.available || !outputStep?.models.length}>Run workflow</button>
        </form>
        <form className="settings-card" onSubmit={event => { event.preventDefault(); void inspect(); }}>
            <label>Workflow ID<input required value={inspectId} onChange={event => setInspectId(event.target.value)} /></label>
            <button disabled={busy} type="submit">Inspect run</button>
        </form>
        {error && <p role="alert">{error}</p>}
        {run && <WorkflowDetails key={`${run.workflow_id}:${inspection}`} initial={run} />}
    </div>;
}
