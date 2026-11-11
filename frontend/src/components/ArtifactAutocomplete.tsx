import { useEffect, useId, useImperativeHandle, useState, type KeyboardEvent, type Ref } from "react";
import { listArtifactPage } from "../api/artifacts";
import { safeMessage } from "../api/client";
import { mentionChoices } from "../lib/mentions";
import type { Attachment } from "../lib/references";
import type { Artifact, ArtifactPage, ReferenceInputSlot } from "../types/media";
import { ReferenceThumbnail } from "./ReferenceAttachments";

export interface AutocompleteHandle { keyDown: (event: KeyboardEvent<HTMLTextAreaElement>) => void }
interface Props {
    query: string; slots: ReferenceInputSlot[]; references: Attachment[]; listId: string;
    onChoose: (artifact: Artifact, role: string) => void; onClose: () => void;
    onActive: (id: string | undefined) => void; ref: Ref<AutocompleteHandle>;
}
export function ArtifactAutocomplete({ query, slots, references, listId, onChoose, onClose, onActive, ref }: Props) {
    const [page, setPage] = useState<ArtifactPage | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [loading, setLoading] = useState(true);
    const [more, setMore] = useState(false);
    const [index, setIndex] = useState(0);
    const [pending, setPending] = useState<Artifact | null>(null);
    const [role, setRole] = useState("");
    const roleId = useId();
    const [retry, setRetry] = useState(0);
    const mediaTypes = [...new Set(slots.flatMap(slot => slot.media_types))];
    const media = mediaTypes.length === 1 ? mediaTypes[0] : undefined;
    useEffect(() => {
        const controller = new AbortController();
        const timer = window.setTimeout(() => {
            void listArtifactPage({ page_size: 10, q: query, media_type: media }, controller.signal)
                .then(value => { if (!controller.signal.aborted) { setPage(value); setError(null); } })
                .catch(error => { if (!controller.signal.aborted) setError(safeMessage(error instanceof Error ? error.message : null)); })
                .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        }, 180);
        return () => { window.clearTimeout(timer); controller.abort(); };
    }, [query, media, retry]);
    // Query/capability changes remount this component; no stale pages survive.
    const [moreController] = useState(() => new AbortController());
    useEffect(() => () => moreController.abort(), [moreController]);
    const rows = page?.items ?? [];
    const eligible = rows.filter(artifact => mentionChoices(artifact, references, slots).length > 0);
    const active = eligible.length ? eligible[Math.min(index, eligible.length - 1)] : undefined;
    const activeId = active && !pending ? `${listId}-${active.artifact_id}` : undefined;
    useEffect(() => { onActive(activeId); if (activeId) document.getElementById(activeId)?.scrollIntoView({ block: "nearest" });
        return () => onActive(undefined); }, [activeId, onActive]);
    function choose(artifact: Artifact) {
        const choices = mentionChoices(artifact, references, slots);
        if (choices.length === 1) onChoose(artifact, choices[0].role);
        else if (choices.length) { setPending(artifact); setRole(""); }
    }
    useImperativeHandle(ref, () => ({ keyDown(event) {
        if (event.nativeEvent.isComposing) return;
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); }
        else if (!pending && (event.key === "ArrowDown" || event.key === "ArrowUp")) {
            event.preventDefault();
            setIndex(value => eligible.length ? (value + (event.key === "ArrowDown" ? 1 : -1) + eligible.length) % eligible.length : 0);
        } else if (!pending && event.key === "Enter") {
            event.preventDefault(); if (active) choose(active);
        }
    } }));
    async function loadMore() {
        if (!page?.next_cursor || more) return;
        setMore(true); setError(null);
        try {
            const next = await listArtifactPage({ page_size: 10, q: query, media_type: media, cursor: page.next_cursor }, moreController.signal);
            if (!moreController.signal.aborted) setPage(previous => ({ ...next, items: [...(previous?.items ?? []), ...next.items]
                .filter((row, i, all) => all.findIndex(other => other.artifact_id === row.artifact_id) === i) }));
        } catch (error) { if (!moreController.signal.aborted) setError(safeMessage(error instanceof Error ? error.message : null)); }
        finally { if (!moreController.signal.aborted) setMore(false); }
    }
    return <div className="artifact-autocomplete" aria-label="Artifact autocomplete" onKeyDown={event => {
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); }
    }}>
        <div className="section-heading"><strong>Choose an artifact</strong><button type="button" onClick={onClose}>Close suggestions</button></div>
        {loading && <p role="status">Searching artifacts…</p>}
        {pending ? <div className="mention-role-choice">
            <p>{pending.filename || "Artifact"}: choose its reference role.</p>
            <label htmlFor={roleId}>Mention role</label><select id={roleId} autoFocus value={role} onChange={event => setRole(event.target.value)}>
                <option value="">Choose a role</option>{mentionChoices(pending, references, slots).map(choice =>
                    <option key={choice.role} value={choice.role}>{choice.role.replaceAll("_", " ")}</option>)}
            </select>
            <button type="button" disabled={!role} onClick={() => onChoose(pending, role)}>Insert mention</button>
            <button type="button" onClick={() => setPending(null)}>Back to suggestions</button>
        </div> : <div id={listId} role="listbox" aria-label="Artifact suggestions">
            {rows.map(artifact => {
                const allowed = mentionChoices(artifact, references, slots).length > 0;
                return <button type="button" role="option" id={`${listId}-${artifact.artifact_id}`} key={artifact.artifact_id}
                    aria-selected={active?.artifact_id === artifact.artifact_id} aria-disabled={!allowed} disabled={!allowed}
                    className="mention-option" onClick={() => choose(artifact)}>
                    <ReferenceThumbnail artifact={artifact} /><span>{artifact.filename || "Artifact"}
                        <small>{artifact.media_type} · {artifact.provenance ?? "unknown"} · {artifact.created_at ? new Date(artifact.created_at).toLocaleString() : "Date unknown"}</small>
                        {!allowed && <small>Incompatible, unsupported role, or input full</small>}</span>
                </button>;
            })}
        </div>}
        {!loading && page && !rows.length && <p role="status">No artifacts match. Unselected @text remains ordinary prompt text.</p>}
        {!loading && rows.length > 0 && !eligible.length && <p>No eligible artifacts on this page. Change model, remove a reference, or load more.</p>}
        {!pending && page?.has_more && <button type="button" disabled={more} onClick={() => void loadMore()}>{more ? "Loading more…" : "More artifacts"}</button>}
        {error && <p role="alert" className="error">{error} <button type="button" onClick={() => setRetry(value => value + 1)}>Retry search</button></p>}
    </div>;
}
