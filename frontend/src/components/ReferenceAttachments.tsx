import { useEffect, useRef, useState } from "react";
import { useArtifactAsset } from "../hooks/useArtifactAsset";
import { isEditorRequest } from "../api/editorContext";
import { listArtifactPage, uploadArtifact, artifactPreviewUrl } from "../api/artifacts";
import { safeMessage } from "../api/client";
import { attachArtifact, attachmentError, canAttach, removeAttachment, validateUpload, UPLOAD_MIMES, type Attachment } from "../lib/references";
import type { Artifact, ArtifactPage, ArtifactProvenance, ReferenceInputSlot } from "../types/media";

export function ReferenceThumbnail({ artifact }: { artifact: Artifact }) {
    const [failed, setFailed] = useState(false);
    const source = useArtifactAsset(isEditorRequest() ? artifactPreviewUrl(artifact.artifact_id) : artifact.preview?.url ?? artifactPreviewUrl(artifact.artifact_id));
    const unavailable = failed || source === "" || artifact.preview?.status === "failed" || artifact.preview?.status === "unsupported";
    return unavailable ? <span className="reference-thumbnail reference-fallback" role="img" aria-label="Preview unavailable">◇</span>
        : <img className="reference-thumbnail" src={source}
            alt="" loading="lazy" decoding="async" onError={() => setFailed(true)} />;
}
interface Props { references: Attachment[]; slots: ReferenceInputSlot[]; onChange: (references: Attachment[]) => void; allowUpload?: boolean }
export function ReferenceAttachments({ references, slots, onChange, allowUpload = true }: Props) {
    const [open, setOpen] = useState(false);
    return <section className="reference-section" aria-label="Generation references">
        <div className="section-heading"><strong>References</strong><button type="button" className="secondary-button"
            disabled={!slots.length || !slots.some(slot => references.filter(ref => slot.roles.includes(ref.role)).length < slot.max_count)}
            onClick={event => { event.currentTarget.focus(); setOpen(true); }}>Add reference</button></div>
        {!slots.length && <p className="lab-help">References are unavailable for this selection. Choose a model with reference inputs in Hard mode.</p>}
        {references.length === 0 && slots.length > 0 && <p className="lab-help">Select a previous artifact or upload an image.</p>}
        {references.map(ref => {
            const slot = slots.find(slot => slot.roles.includes(ref.role));
            const roles = slots.flatMap(slot => slot.roles);
            const error = attachmentError(ref, slots);
            return <div className="reference-card" key={ref.artifact_id}>
                <ReferenceThumbnail key={ref.artifact_id} artifact={ref.artifact} />
                <span className="reference-name">{ref.artifact.filename || ref.artifact_id}</span>
                <label>Role<select value={ref.role} onChange={event => onChange(references.map(item => item.artifact_id === ref.artifact_id
                    ? { ...item, role: event.target.value } : item))}>
                    {!roles.includes(ref.role) && <option value={ref.role}>{ref.role} · unavailable</option>}
                    {roles.map(role => <option key={role} value={role}>{role.replaceAll("_", " ")}</option>)}
                </select></label>
                {!!slot?.strengths.length && <label>Strength<select value={ref.strength ?? ""}
                    onChange={event => onChange(references.map(item => item.artifact_id === ref.artifact_id
                        ? { ...item, strength: (event.target.value || undefined) as Attachment["strength"] } : item))}>
                    <option value="">Default</option>
                    {ref.strength && !slot.strengths.includes(ref.strength) && <option value={ref.strength}>{ref.strength} · unavailable</option>}
                    {slot.strengths.map(strength => <option key={strength}>{strength}</option>)}
                </select></label>}
                {ref.strength && !slot?.strengths.length && <button type="button" onClick={() => onChange(references.map(item =>
                    item.artifact_id === ref.artifact_id ? { ...item, strength: undefined } : item))}>Clear unsupported strength</button>}
                <button type="button" className="secondary-button" aria-label={`Remove ${ref.artifact.filename || ref.artifact_id}`}
                    onClick={() => onChange(removeAttachment(references, ref.artifact_id))}>Remove</button>
                {error && <p className="error reference-error" role="alert">{error}</p>}
            </div>;
        })}
        {open && <ArtifactPicker slots={slots} references={references} onAttach={onChange} onClose={() => setOpen(false)} allowUpload={allowUpload} />}
    </section>;
}
interface PickerProps extends Omit<Props, "onChange"> { onAttach: Props["onChange"]; onClose: () => void }
export function ArtifactPicker({ slots, references, onAttach, onClose, allowUpload = true }: PickerProps) {
    const dialog = useRef<HTMLDialogElement>(null);
    const [slotName, setSlotName] = useState(slots[0]?.name ?? "");
    const slot = slots.find(slot => slot.name === slotName) ?? slots[0];
    const [role, setRole] = useState(slot?.roles[0] ?? "");
    const selectedRole = slot?.roles.includes(role) ? role : slot?.roles[0] ?? "";
    const [search, setSearch] = useState("");
    const [query, setQuery] = useState({ q: "", provenance: "" as "" | ArtifactProvenance });
    const [page, setPage] = useState<ArtifactPage | null>(null);
    const [loading, setLoading] = useState(true);
    const [moreLoading, setMoreLoading] = useState(false);
    const [uploading, setUploading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const active = useRef<AbortController | null>(null);
    const uploadingRef = useRef(false);
    const listing = useRef<AbortController | null>(null);
    const [retry, setRetry] = useState(0);
    const media = slot?.media_types.length === 1 ? slot.media_types[0] : undefined;
    useEffect(() => {
        const element = dialog.current;
        const previous = document.activeElement;
        element?.showModal();
        return () => { element?.close(); if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
    }, []);
    useEffect(() => () => { active.current?.abort(); listing.current?.abort(); }, []);
    useEffect(() => {
        const controller = new AbortController();
        listing.current = controller;
        void listArtifactPage({ page_size: 20, q: query.q, provenance: query.provenance || undefined, media_type: media }, controller.signal)
            .then(value => { if (!controller.signal.aborted) setPage(value); })
            .catch(error => { if (!controller.signal.aborted) setError(safeMessage(error instanceof Error ? error.message : null)); })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [query, media, retry, slotName]);
    function reset() { listing.current?.abort(); setPage(null); setLoading(true); setMoreLoading(false); setError(null); }
    async function loadMore() {
        if (!page?.next_cursor || moreLoading) return;
        const controller = new AbortController(); listing.current = controller;
        setMoreLoading(true); setError(null);
        try {
            const next = await listArtifactPage({ page_size: 20, cursor: page.next_cursor, q: query.q,
                provenance: query.provenance || undefined, media_type: media }, controller.signal);
            if (!controller.signal.aborted) setPage(previous => ({ ...next, items: [...(previous?.items ?? []), ...next.items]
                .filter((row, index, rows) => rows.findIndex(item => item.artifact_id === row.artifact_id) === index) }));
        } catch (error) { if (!controller.signal.aborted) setError(safeMessage(error instanceof Error ? error.message : null)); }
        finally { if (!controller.signal.aborted) setMoreLoading(false); }
    }
    function attach(artifact: Artifact) {
        if (!slot) return;
        try { onAttach(attachArtifact(artifact, references, slot, selectedRole)); onClose(); }
        catch (error) { setError(safeMessage(error instanceof Error ? error.message : null)); }
    }
    async function upload(file?: File) {
        if (!file || !slot || uploadingRef.current) return;
        setError(null);
        try { validateUpload(file, slot); }
        catch (error) { setError(safeMessage(error instanceof Error ? error.message : null)); return; }
        const controller = new AbortController(); active.current = controller; uploadingRef.current = true; setUploading(true);
        try {
            const artifact = await uploadArtifact(file, controller.signal);
            if (!controller.signal.aborted) attach(artifact);
        } catch (error) { if (!controller.signal.aborted) setError(safeMessage(error instanceof Error ? error.message : null)); }
        finally { uploadingRef.current = false; if (!controller.signal.aborted) setUploading(false); }
    }
    const full = !slot || references.filter(ref => slot.roles.includes(ref.role)).length >= slot.max_count;
    return <dialog className="artifact-picker" ref={dialog} aria-labelledby="artifact-picker-title" onCancel={event => { event.preventDefault(); onClose(); }}>
        <div className="section-heading"><h2 id="artifact-picker-title">Add reference</h2><button type="button" onClick={onClose} aria-label="Close artifact picker">Close</button></div>
        <p className="lab-help">Previews may be processing or unavailable. You can still select a compatible artifact.</p>
        <fieldset disabled={uploading} className="lab-fieldset">
            <legend className="sr-only">Reference selection</legend>
            {slots.length > 1 && <label>Input<select value={slot?.name} onChange={event => { reset(); setSlotName(event.target.value); }}>
                {slots.map(slot => <option key={slot.name}>{slot.name}</option>)}</select></label>}
            <label>Role<select value={selectedRole} onChange={event => setRole(event.target.value)}>
                {slot?.roles.map(role => <option key={role} value={role}>{role.replaceAll("_", " ")}</option>)}</select></label>
            <p className="lab-help">{slot ? `${slot.min_count}–${slot.max_count} references · up to ${slot.max_bytes / 1024 / 1024} MiB each · ${slot.mime_types.join(", ")}` : "No supported inputs."}</p>
            <label>Upload reference image<input type="file" disabled={!allowUpload || full || !slot?.media_types.includes("image") || !slot.mime_types.some(mime => UPLOAD_MIMES.includes(mime))}
                accept={slot?.mime_types.filter(mime => UPLOAD_MIMES.includes(mime)).join(",")}
                onChange={event => { const file = event.target.files?.[0]; event.target.value = ""; void upload(file); }} /></label>
            <p className="lab-help">{allowUpload ? "Uploads: PNG, JPEG or WebP, at most 10 MiB (or the input limit if smaller). Uploading attaches immediately." : "Uploads are disabled for this editor."}</p>
            <div className="picker-search"><label>Search filename<input type="search" value={search} onChange={event => setSearch(event.target.value)}
                    onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); reset(); setQuery(previous => ({ ...previous, q: search.trim() })); } }} /></label>
                <button type="button" onClick={() => { reset(); setQuery(previous => ({ ...previous, q: search.trim() })); }}>Search</button>
                <label>Source<select value={query.provenance} onChange={event => { reset(); setQuery(previous => ({ ...previous, provenance: event.target.value as typeof query.provenance })); }}>
                    <option value="">All sources</option><option value="generated">Generated</option><option value="uploaded">Uploaded</option><option value="unknown">Legacy / unknown</option>
                </select></label></div>
            {loading ? <p role="status">Loading artifacts…</p> : <>
                <div className="artifact-picker-list">{page?.items.map(artifact => {
                    const eligible = !!slot && canAttach(artifact, references, slot);
                    return <button type="button" className="artifact-picker-item" key={artifact.artifact_id} disabled={!eligible}
                        onClick={() => attach(artifact)}>
                        <ReferenceThumbnail artifact={artifact} /><span>{artifact.filename || artifact.artifact_id}</span>
                        <small>{artifact.mime_type} · {artifact.size_bytes === null ? "Size unknown" : `${Math.ceil(artifact.size_bytes / 1024)} KiB`}</small>
                        {!eligible && <small>Already attached, incompatible, or input full</small>}
                    </button>;
                })}</div>
                {page && !page.items.length && <p className="empty">{query.q ? "No artifacts match this search." : "No previous artifacts. Upload an image to get started."}</p>}
                {page?.items.length && !page.items.some(artifact => slot && canAttach(artifact, references, slot)) ? <p>No eligible artifacts on this page. Try another page or upload.</p> : null}
                {page?.has_more && <button type="button" disabled={moreLoading} onClick={() => void loadMore()}>{moreLoading ? "Loading more…" : "Load more"}</button>}
            </>}
        </fieldset>
        {uploading && <p role="status">Uploading image…</p>}
        {error && <div className="error" role="alert">{error} <button type="button" disabled={uploading} onClick={() => { reset(); setRetry(value => value + 1); }}>Reload artifacts</button></div>}
    </dialog>;
}
