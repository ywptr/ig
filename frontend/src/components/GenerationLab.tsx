import { ArtifactPromptEditor } from "./ArtifactPromptEditor";
import { mentionErrors, updateAttachments } from "../lib/mentions";
import { ReferenceAttachments } from "./ReferenceAttachments";
import { referenceErrors } from "../lib/references";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { getMediaCapabilities, submitMedia } from "../api/media";
import { ApiError, safeMessage } from "../api/client";
import { buildMediaRequest, humanize, INITIAL_FORM, matchesSubmission, pendingReceipt, resolveLabForm, type LabForm } from "../lib/generation";
import type { GenerationResult, GenerationSubmission, InterfaceLevel, MediaCapabilities, MediaType } from "../types/media";
import { GenerationControls } from "./GenerationControls";
import { MediaResultCard } from "./MediaResultCard";
import { LegacyImageHistory } from "./LegacyImageHistory";
import { editorFormErrors, restrictedCapabilities, type EditorPolicy } from "../lib/editor";
import type { MediaGenerationRequest } from "../types/media";

interface Props {
    results: GenerationResult[]; receipts: GenerationSubmission[]; loading: boolean;
    error: string | null; onRefresh: () => void; onAccepted: (receipt: GenerationSubmission) => void;
    editor?: { initialForm: LabForm; policy: EditorPolicy; draftErrors: string[];
        submit: (request: MediaGenerationRequest, signal?: AbortSignal) => Promise<GenerationSubmission> };
}
export function GenerationLab({ results, receipts, loading, error, onRefresh, onAccepted, editor }: Props) {
    const [capabilities, setCapabilities] = useState<MediaCapabilities | null>(null);
    const [capLoading, setCapLoading] = useState(true);
    const [capError, setCapError] = useState<string | null>(null);
    const [revision, setRevision] = useState(0);
    const [form, setForm] = useState<LabForm>(editor?.initialForm ?? INITIAL_FORM);
    const [draftTouched, setDraftTouched] = useState(false);
    const [submitting, setSubmitting] = useState(false);
    const [submitError, setSubmitError] = useState<string | null>(null);
    const [acceptedMessage, setAcceptedMessage] = useState(false);
    const submittingRequest = useRef<AbortController | null>(null);
    useEffect(() => {
        const controller = new AbortController();
        void getMediaCapabilities(controller.signal).then(value => {
            if (!controller.signal.aborted) { setCapabilities(editor ? restrictedCapabilities(value, editor.policy) : value); setCapError(null); }
        }).catch(error => { if (!controller.signal.aborted) setCapError(safeMessage(error instanceof Error ? error.message : null)); })
            .finally(() => { if (!controller.signal.aborted) setCapLoading(false); });
        return () => controller.abort();
    }, [revision, editor]);
    useEffect(() => () => submittingRequest.current?.abort(), []);
    const resolved = capabilities ? resolveLabForm(capabilities, form) : null;
    const attachmentErrors = resolved ? [...referenceErrors(form.references, resolved.referenceSlots), ...mentionErrors(form, resolved.referenceSlots)] : [];
    const preservedErrors = editor && capabilities ? editorFormErrors(capabilities, form) : [];
    const operation = form.media === "video" ? (form.references.length ? "image_to_video" : "text_to_video") : (form.references.length ? "reference_image_conditioning" : "text_to_image");
    if (editor?.policy.allowed_operations.length && !editor.policy.allowed_operations.includes(operation)) preservedErrors.push("This operation is excluded by the editor policy.");
    function update(patch: Partial<LabForm>) {
        setDraftTouched(true);
        setForm(previous => ({ ...(!editor && capabilities ? resolveLabForm(capabilities, previous).values : previous), ...patch }));
    }
    async function generate(event: FormEvent<HTMLFormElement>) {
        event.preventDefault();
        if (!capabilities || submittingRequest.current) return;
        const controller = new AbortController();
        submittingRequest.current = controller;
        setSubmitting(true); setSubmitError(null); setAcceptedMessage(false);
        try {
            const receipt = await (editor?.submit ?? submitMedia)(buildMediaRequest(capabilities, form), controller.signal);
            if (!controller.signal.aborted) { onAccepted(receipt); setAcceptedMessage(true); }
        } catch (error) {
            if (!controller.signal.aborted) {
                const uncertain = !(error instanceof ApiError) || error.status >= 500;
                setSubmitError(safeMessage(error instanceof Error ? error.message : null)
                    + (uncertain ? " Submission may have been accepted. Check Jobs before submitting again." : ""));
                onRefresh();
            }
        } finally {
            submittingRequest.current = null;
            if (!controller.signal.aborted) setSubmitting(false);
        }
    }
    const unmatched = receipts.filter(receipt => !results.some(result => matchesSubmission(result, receipt)));
    return <>
        <div className="lab-heading"><div><h1>{editor ? "Advanced Generation Editor" : "Generation Lab"}</h1><p>Generate media through your tenant’s available capabilities.</p></div>
            <button type="button" className="secondary-button" disabled={capLoading} onClick={() => { setCapLoading(true); setCapError(null); setRevision(value => value + 1); }}>Refresh capabilities</button></div>
        {capLoading && <p role="status">Loading generation capabilities…</p>}
        {capError && <div className="error" role="alert">{capError} You can still use Jobs and Executions.</div>}
        {editor && !draftTouched && editor.draftErrors.map(message => <p key={message} role="alert" className="error">{message}</p>)}
        {preservedErrors.map(message => <p key={message} role="alert" className="error">{message}</p>)}
        {editor && preservedErrors.length > 0 && <p className="lab-help">Preserved selection: {form.media} · {form.provider || "Auto provider"} · {form.model || "Auto model"} · {form.aspect} · {form.resolution} {form.duration !== null ? `· ${form.duration}s` : ""}</p>}
        {!capLoading && !capError && resolved && capabilities && <form className="lab-form" onSubmit={generate}>
            <fieldset disabled={submitting || editor?.policy.model_switching === false} className="lab-fieldset"><legend>Mode</legend><div className="lab-segments">
                {(Object.keys(capabilities.interface_levels).filter(mode => ["easy", "medium", "hard"].includes(mode)) as InterfaceLevel[]).map(mode => <button type="button" key={mode}
                    aria-pressed={form.mode === mode} onClick={() => update({ mode, provider: "", model: "" })}>{humanize(mode)}</button>)}
            </div></fieldset>
            {resolved.mediaOptions.length === 0 ? <p className="empty">No image or video text-generation capabilities are available in this mode for your tenant.</p> : <>
                <fieldset disabled={submitting} className="lab-fieldset"><legend>Media</legend><div className="lab-segments">
                    {capabilities.media_types.filter(media => media.id === "image" || media.id === "video").map(media => <button type="button" key={media.id}
                        disabled={!resolved.mediaOptions.includes(media)} aria-pressed={resolved.values.media === media.id}
                        onClick={() => update({ media: media.id as MediaType, provider: "", model: "", aspect: "", resolution: "", duration: null, audio: false, count: null })}>
                        {humanize(media.id)}{!resolved.mediaOptions.includes(media) ? " · unavailable" : ""}</button>)}
                </div></fieldset>
                <fieldset disabled={submitting} className="lab-fieldset">
                    <legend className="sr-only">Generation request</legend>
                    <ArtifactPromptEditor draft={form} slots={editor?.policy.reference_editing === false ? [] : resolved.referenceSlots} onChange={update} />
                    {capabilities.interface_levels[form.mode].explicit_provider_selection ? <fieldset disabled={editor?.policy.model_switching === false} className="lab-controls provider-controls">
                        <label>Provider<select value={editor ? form.provider : resolved.values.provider} onChange={event => update({ provider: event.target.value, model: "" })}>
                            <option value="">Auto</option>{resolved.providers.map(provider => <option key={provider.id} value={provider.id}>{provider.display_name}</option>)}
                        </select></label>
                        {resolved.provider && capabilities.interface_levels[form.mode].explicit_model_selection && <label>Model
                            <select value={editor ? form.model : resolved.values.model} onChange={event => update({ model: event.target.value })}>
                                <option value="" disabled={!resolved.provider.default_model_available}>Auto · provider default</option>
                                {resolved.models.map(model => <option key={model.id} value={model.id}>{model.display_name}</option>)}
                            </select></label>}
                    </fieldset> : <p className="lab-help">Provider / model: <strong>Auto</strong></p>}
                    {resolved.controls && resolved.media && <GenerationControls media={resolved.values.media} controls={resolved.controls}
                        purposes={resolved.media.purposes} values={editor ? form : resolved.values} onChange={update} />}
                    {!resolved.usable && <p className="error">No usable shared controls or provider default. Choose an available model in Hard mode.</p>}
                    <p className="lab-help">{resolved.values.provider ? "Using the selected provider’s generic controls." : "Auto shows controls shared by available models. IG chooses the provider and model."}
                        {form.mode === "easy" && resolved.values.media === "image" ? " Medium and Hard add quality and style controls." : ""}</p>
                    <fieldset disabled={editor?.policy.reference_editing === false} className="lab-fieldset">
                        <ReferenceAttachments key={JSON.stringify(resolved.referenceSlots)} references={form.references} slots={resolved.referenceSlots}
                            allowUpload={editor?.policy.upload ?? true} onChange={references => update(updateAttachments(form, references))} />
                    </fieldset>
                    {attachmentErrors.length > 0 && <p className="error" role="alert">{attachmentErrors[0]}</p>}
                    <button className="generate-button" type="submit" disabled={!resolved.usable || !form.prompt.trim() || submitting || attachmentErrors.length > 0 || preservedErrors.length > 0}>
                        {submitting ? "Submitting…" : `Generate ${resolved.values.media}`}</button>
                </fieldset>
            </>}
        </form>}
        {submitError && <p className="error" role="alert">{submitError}</p>}
        {acceptedMessage && <p className="generation-status" role="status">Generation accepted. Follow its progress below; you can submit another request.</p>}
        <section className="lab-results" aria-label="Recent generations"><div className="section-heading"><h2>Recent generations</h2>
            <button type="button" className="secondary-button" onClick={onRefresh} disabled={loading}>{loading ? "Refreshing…" : "Refresh results"}</button></div>
            {error && <p className="error" role="alert">{error}</p>}
            {!loading && !results.length && !unmatched.length && <p className="empty">No recent image or video generation Jobs.</p>}
            <div className="media-results">
                {unmatched.map(receipt => <article className="media-result" key={receipt.job_id ?? receipt.request_id}>
                    <div className="result-heading"><strong>{humanize(receipt.media_type)}</strong><span className="job-status queued">Accepted</span></div>
                    <p>{receipt.prompt}</p><p className="lab-help">{pendingReceipt(receipt, results) ? "Finding the accepted Job…"
                        : "Accepted, but not found in the recent Job window. Refresh Jobs before submitting again."}</p>
                </article>)}
                {results.map(result => <MediaResultCard key={result.job_id} result={result} />)}
            </div>
        </section>
        {!editor && <LegacyImageHistory />}
    </>;
}
