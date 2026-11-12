import { useEffect, useState } from "react";

interface Policy {
    generated_artifact_retention_enabled: boolean;
    generated_artifact_retention_days: number;
}

export function RetentionSettings() {
    const [policy, setPolicy] = useState<Policy | null>(null);
    const [days, setDays] = useState("28");
    const [message, setMessage] = useState("");
    const [saving, setSaving] = useState(false);
    useEffect(() => {
        let active = true;
        void fetch("/v2/tenant/settings/retention").then(async response => {
            if (!response.ok) throw new Error();
            const value: Policy = await response.json();
            if (active) { setPolicy(value); setDays(String(value.generated_artifact_retention_days)); }
        }).catch(() => { if (active) setMessage("Unable to load retention settings."); });
        return () => { active = false; };
    }, []);
    async function save(event: React.SubmitEvent) {
        event.preventDefault();
        const duration = Number(days);
        if (!Number.isInteger(duration) || duration < 1 || duration > 3650) {
            setMessage("Enter a whole number of days from 1 to 3650."); return;
        }
        setSaving(true); setMessage("");
        try {
            const response = await fetch("/v2/tenant/settings/retention", {
                method: "PATCH", headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ ...policy, generated_artifact_retention_days: duration }),
            });
            if (!response.ok) throw new Error();
            setPolicy(await response.json()); setMessage("Retention settings saved.");
        } catch { setMessage("Unable to save retention settings. Check the duration and try again."); }
        finally { setSaving(false); }
    }
    return <section aria-labelledby="retention-title">
        <h2 id="retention-title">Generated media retention</h2>
        <p>Automatically removes generated media and its previews. Uploads and reference uploads are excluded.
            Historical metadata and lineage remain available. Generated media used as a reference may expire after its active jobs finish.</p>
        <p>For user-owned external storage, automatic cleanup may be disabled by default.</p>
        {policy && <form onSubmit={event => void save(event)}>
            <label><input type="checkbox" checked={policy.generated_artifact_retention_enabled}
                onChange={event => setPolicy({ ...policy, generated_artifact_retention_enabled: event.target.checked })} />
                Automatically delete generated media after:</label>{" "}
            <label><input aria-label="Retention days" type="number" min="1" max="3650" step="1" required
                value={days} onChange={event => setDays(event.target.value)} /> days</label>{" "}
            <button type="submit" disabled={saving}>{saving ? "Saving…" : "Save retention settings"}</button>
        </form>}
        {message && <p role="status">{message}</p>}
    </section>;
}
