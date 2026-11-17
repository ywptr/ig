import { useEffect, useState } from "react";
import { getRetentionPolicy, saveRetentionPolicy, type RetentionPolicy, type RetentionScope } from "../api/tenantSettings";
import { safeMessage } from "../api/client";

export function RetentionSettings({ scope = "managed" }: { scope?: RetentionScope }) {
    const [policy, setPolicy] = useState<RetentionPolicy | null>(null);
    const [enabled, setEnabled] = useState(false);
    const [days, setDays] = useState("");
    const [message, setMessage] = useState("");
    const [error, setError] = useState("");
    const [saving, setSaving] = useState(false);
    const external = scope === "external";
    const identity = external ? "external-retention" : "retention";
    useEffect(() => {
        const controller = new AbortController();
        void getRetentionPolicy(scope, controller.signal).then(value => {
            if (!controller.signal.aborted) {
                setPolicy(value); setEnabled(value.generated_artifact_retention_enabled);
                setDays(String(value.generated_artifact_retention_days));
            }
        }).catch(error => { if (!controller.signal.aborted) setError(safeMessage(error.message, "Unable to load retention settings.")); });
        return () => controller.abort();
    }, [scope]);
    async function save(event: React.SubmitEvent) {
        event.preventDefault();
        setSaving(true); setMessage(""); setError("");
        try {
            const value = await saveRetentionPolicy(scope, enabled, days);
            setPolicy(value); setEnabled(value.generated_artifact_retention_enabled);
            setDays(String(value.generated_artifact_retention_days));
            setMessage("Retention settings saved.");
        } catch (error) { setError(safeMessage(error instanceof Error ? error.message : null, "Unable to save retention settings.")); }
        finally { setSaving(false); }
    }
    return <section className="settings-card" aria-labelledby={`${identity}-title`}>
        <h2 id={`${identity}-title`}>{external ? "Google Drive-backed retention" : "IG-managed retention"}</h2>
        <p>{external ? "Generated media in users’ Google Drive defaults to no automatic cleanup. This is a separate tenant policy; enabling Drive does not enable retention."
            : "Generated media in IG-managed local/Vercel storage defaults to automatic cleanup after 30 days, unless this tenant overrides it."}</p>
        <p>Cleanup removes generated media and its previews. Uploads and reference uploads are excluded.
            Artifact history and lineage remain available. Generated media used as a reference may expire after its active jobs finish.</p>
        {!policy && !error && <p role="status">Loading retention settings…</p>}
        {policy && <>
            <p>Current policy: {policy.generated_artifact_retention_enabled ? `On · ${policy.generated_artifact_retention_days} days` : "Off"}
                {policy.retention_policy_source === "tenant" ? " · Tenant override" : " · Storage default"}</p>
            <form onSubmit={event => void save(event)} noValidate aria-label={external ? "Google Drive retention" : "IG-managed retention"}>
                <fieldset disabled={saving} className="settings-fields">
                    <label className="settings-check"><input type="checkbox" checked={enabled} onChange={event => { setEnabled(event.target.checked); setMessage(""); }} />
                        {external ? "Automatically remove generated media from users’ Google Drive" : "Automatically remove generated media from IG-managed storage"}</label>
                    <label className="settings-duration">Remove content after
                        <span><input aria-label={external ? "Google Drive retention days" : "Retention days"} aria-describedby={`${identity}-feedback`}
                            aria-invalid={Boolean(error)} type="number" min="1" max="3650" step="1" required
                            value={days} onChange={event => { setDays(event.target.value); setMessage(""); }} /> days</span>
                    </label>
                    <button type="submit" className="secondary-button">{saving ? "Saving…" : external ? "Save Google Drive retention" : "Save retention settings"}</button>
                </fieldset>
            </form>
        </>}
        <div id={`${identity}-feedback`}>
            {error && <p className="settings-error" role="alert">{error}</p>}
            {message && <p role="status">{message}</p>}
        </div>
    </section>;
}
