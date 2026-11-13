import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getArtifact } from "../api/artifacts";
import { requestJSON, safeMessage } from "../api/client";
import { setEditorCredential } from "../api/editorContext";
import { getJobExecutions, cancelJob } from "../api/jobs";
import { submitMedia } from "../api/media";
import { useGenerationFeed } from "../hooks/useGenerationFeed";
import { editorEvent, editorForm, validParentMessage, type EditorBootstrap } from "../lib/editor";
import { isActiveStatus, type LabForm } from "../lib/generation";
import type { MediaGenerationRequest } from "../types/media";
import { GenerationLab } from "./GenerationLab";

// Module-level promise prevents StrictMode's development effect replay from
// redeeming a one-time launch twice. Never persisted across document loads.
let bootstrapPromise: Promise<{ session: EditorBootstrap; form: LabForm }> | undefined;
async function bootstrap() {
    const launch = new URLSearchParams(location.hash.slice(1)).get("launch");
    history.replaceState(null, "", location.pathname);
    if (!launch) throw new Error("This launch was already used or is missing. Request a new editor launch.");
    const sid = location.pathname.split("/").pop();
    const parentOrigin = document.getElementById("root")?.dataset.parentOrigin;
    const target = window.parent !== window ? window.parent : window.opener as Window | null;
    if (!target || !parentOrigin) throw new Error("Open this editor from its approved application.");
    // Confirm the actual parent/opener before consuming a launch. A copied URL
    // in a top-level tab cannot silently turn into a reusable editor session.
    const nonce = crypto.randomUUID();
    await new Promise<void>((resolve, reject) => {
        const listener = (event: MessageEvent) => {
            if (event.origin === parentOrigin && event.source === target && event.data?.type === "ig.editor.authorize"
                && event.data?.version === 1 && event.data?.session_id === sid && event.data?.nonce === nonce) {
                clearTimeout(timer); window.removeEventListener("message", listener); resolve();
            }
        };
        const timer = window.setTimeout(() => { window.removeEventListener("message", listener); reject(new Error("Approved parent did not authorize this editor launch.")); }, 10_000);
        window.addEventListener("message", listener);
        target.postMessage({ type: "ig.editor.handshake", version: 1, session_id: sid, nonce }, parentOrigin);
    });
    const response = await fetch(`/v2/editor/sessions/${encodeURIComponent(sid ?? "")}/redeem`, {
        method: "POST", credentials: "omit", redirect: "error", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ launch_token: launch }),
    });
    if (!response.ok) throw new Error("Editor launch expired or was already used. Request a new launch.");
    const { editor_token, ...session } = await response.json() as EditorBootstrap & { editor_token: string };
    if (session.parent_origin !== parentOrigin || session.session_id !== sid) throw new Error("Editor launch binding mismatch.");
    setEditorCredential({ session: session.session_id, token: editor_token });
    const artifacts = await Promise.all((session.draft.references ?? []).map(ref => getArtifact(ref.artifact_id)));
    return { session, form: editorForm(session.draft, artifacts) };
}

function EditorWorkspace({ session, form, onClosed }: { session: EditorBootstrap; form: LabForm; onClosed: () => void }) {
    const feed = useGenerationFeed();
    const [requestPending, setRequestPending] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const target = window.parent !== window ? window.parent : window.opener as Window | null;
    const attempt = useRef<{ body: string; id: string } | null>(null);
    const attempts = useRef(new Map<string, string>());
    const delivered = useRef(new Set<string>());
    const post = useCallback((type: string, payload: Record<string, unknown> = {}) => {
        target?.postMessage(editorEvent(session, type, payload), session.parent_origin);
    }, [target, session]);
    useEffect(() => { post("ig.editor.ready"); }, [post]);
    const submit = useCallback(async (request: MediaGenerationRequest, signal?: AbortSignal) => {
        const body = JSON.stringify(request);
        if (!attempt.current || attempt.current.body !== body) attempt.current = { body, id: crypto.randomUUID() };
        const id = attempt.current.id;
        setRequestPending(true);
        let receipt;
        try { receipt = await submitMedia(request, signal, id); }
        finally { setRequestPending(false); }
        attempt.current = null;
        if (receipt.job_id) attempts.current.set(receipt.job_id, id);
        post("ig.editor.submitted", { attempt_id: id, job_id: receipt.job_id, media_type: receipt.media_type });
        return receipt;
    }, [post]);
    const editor = useMemo(() => ({ initialForm: form, policy: session.policy, draftErrors: session.draft_errors, submit }), [form, session, submit]);
    useEffect(() => {
        for (const result of feed.data.results) {
            if (!["completed", "canceled", "failed"].includes(result.status) || delivered.current.has(result.job_id)) continue;
            delivered.current.add(result.job_id);
            if (result.status === "completed") {
                void getJobExecutions(result.job_id).then(executions => {
                    const completed = executions.filter(execution => execution.status === "completed");
                    post("ig.editor.completed", { attempt_id: attempts.current.get(result.job_id) ?? feed.data.jobs.find(job => job.job_id === result.job_id)?.editor_attempt_id, job_id: result.job_id,
                        execution_ids: completed.map(execution => execution.execution_id), media_type: result.media_type,
                        artifacts: completed.flatMap(execution => typeof execution.metadata?.artifact_id === "string"
                            ? [{ artifact_id: execution.metadata.artifact_id, media_type: result.media_type }] : []) });
                }).catch(() => { delivered.current.delete(result.job_id); setError("Unable to load result identities. Refresh results to retry."); });
            } else post(result.status === "canceled" ? "ig.editor.canceled" : "ig.editor.error", {
                attempt_id: attempts.current.get(result.job_id) ?? feed.data.jobs.find(job => job.job_id === result.job_id)?.editor_attempt_id,
                job_id: result.job_id, code: result.status === "failed" ? feed.data.jobs.find(job => job.job_id === result.job_id)?.error_code ?? "GENERATION_FAILED" : undefined });
        }
    }, [feed.data.results, feed.data.jobs, post]);
    const close = useCallback(async () => {
        if (requestPending) { setError("Wait for submission acknowledgement before closing this editor."); return; }
        try {
            await requestJSON("/v2/editor/session/close", { method: "POST" });
            post("ig.editor.closed", { running_job_ids: feed.data.jobs.filter(job => isActiveStatus(job.status)).map(job => job.job_id) });
            onClosed();
        } catch { setError("Unable to close editor. Retry; running generations are unchanged."); }
    }, [feed.data.jobs, post, requestPending, onClosed]);
    useEffect(() => {
        const listener = (event: MessageEvent) => { if (validParentMessage(event, session, target)) void close(); };
        window.addEventListener("message", listener);
        return () => window.removeEventListener("message", listener);
    }, [close, session, target]);
    return <main className="main"><button type="button" disabled={requestPending} onClick={() => void close()}>Close editor</button>
        <p className="lab-help">Editor expires {new Date(session.expires_at).toLocaleTimeString()}. Closing does not cancel generation.</p>
        {error && <p className="error" role="alert">{error}</p>}
        <GenerationLab {...{ results: feed.data.results, receipts: feed.receipts, loading: feed.loading, error: feed.error,
            onRefresh: feed.refresh, onAccepted: feed.accepted, editor }} />
        {feed.data.jobs.filter(job => isActiveStatus(job.status)).map(job => <button type="button" key={job.job_id}
            onClick={() => { void cancelJob(job.job_id).then(feed.refresh).catch(() => setError("Unable to request cancellation.")); }}>
            Cancel generation {job.job_id}</button>)}
    </main>;
}

export default function EmbeddedEditor() {
    const [state, setState] = useState<{ session: EditorBootstrap; form: LabForm }>();
    const [error, setError] = useState<string>();
    const [closed, setClosed] = useState(false);
    const onClosed = useCallback(() => setClosed(true), []);
    useEffect(() => {
        let active = true;
        bootstrapPromise ??= bootstrap();
        void bootstrapPromise.then(value => { if (active) setState(value); }).catch(error => {
            if (active) setError(safeMessage(error instanceof Error ? error.message : null));
        });
        return () => { active = false; };
    }, []);
    useEffect(() => {
        if (!state) return;
        const timer = window.setTimeout(() => {
            setError("Editor session expired. Request a new launch; existing generations continue in IG.");
        }, Math.max(0, Date.parse(state.session.expires_at) - Date.now()));
        return () => window.clearTimeout(timer);
    }, [state]);
    return closed ? <p role="status">Editor closed. Running generations continue in IG.</p>
        : error ? <p role="alert" className="error">{error}</p> : state ? <EditorWorkspace {...state} onClosed={onClosed} /> : <p role="status">Opening Advanced Generation Editor…</p>;
}
