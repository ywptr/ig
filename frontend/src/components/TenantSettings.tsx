import { useEffect, useState } from "react";
import { ApiError, safeMessage } from "../api/client";
import { getDriveCapability, getProviderSummaries, getTenantInfo, saveDriveCapability,
    type DriveCapability, type ProviderSummary, type TenantInfo } from "../api/tenantSettings";
import { RetentionSettings } from "./RetentionSettings";

type Access = { status: "loading" } | { status: "error"; message: string }
    | { status: "ready"; tenant: TenantInfo; capability: DriveCapability };

function DriveGovernance({ initial }: { initial: DriveCapability }) {
    const [enabled, setEnabled] = useState(initial.google_drive_enabled);
    const [draft, setDraft] = useState(initial.google_drive_enabled);
    const [saving, setSaving] = useState(false);
    const [message, setMessage] = useState("");
    const [error, setError] = useState("");
    async function save(event: React.SubmitEvent) {
        event.preventDefault(); setSaving(true); setError(""); setMessage("");
        try {
            const value = await saveDriveCapability(draft);
            setEnabled(value.google_drive_enabled); setDraft(value.google_drive_enabled);
            setMessage("Google Drive availability saved.");
        } catch (error) { setError(safeMessage(error instanceof Error ? error.message : null, "Unable to save tenant storage settings.")); }
        finally { setSaving(false); }
    }
    return <section className="settings-card" aria-labelledby="tenant-storage-title">
        <h2 id="tenant-storage-title">Artifact storage</h2>
        <p>Enabling Google Drive allows individual tenant users to connect and use their own account.
            Each connection is private and user-owned. This does not connect a shared tenant Drive.</p>
        <p>Disabling blocks new Google Drive connections and writes. Existing Drive-backed artifacts and ready previews
            remain readable while their owner’s authorization is valid; no files are automatically deleted or migrated.</p>
        <p>Google Drive integration: <strong>{enabled ? "Enabled" : "Disabled"}</strong></p>
        <form onSubmit={event => void save(event)} aria-label="Google Drive availability">
            <fieldset disabled={saving} className="settings-fields">
                <label className="settings-check"><input type="checkbox" checked={draft} onChange={event => { setDraft(event.target.checked); setMessage(""); }} />
                    Enable Google Drive for tenant users</label>
                <button type="submit" className="secondary-button">{saving ? "Saving…" : "Save Drive availability"}</button>
            </fieldset>
        </form>
        {error && <p className="settings-error" role="alert">{error}</p>}
        {message && <p role="status">{message}</p>}
    </section>;
}

function ProviderSettings() {
    const [providers, setProviders] = useState<ProviderSummary[] | null>(null);
    const [error, setError] = useState("");
    useEffect(() => {
        const controller = new AbortController();
        void getProviderSummaries(controller.signal).then(value => { if (!controller.signal.aborted) setProviders(value); })
            .catch(error => { if (!controller.signal.aborted) setError(safeMessage(error.message, "Unable to load provider settings.")); });
        return () => controller.abort();
    }, []);
    return <section className="settings-card" aria-labelledby="provider-settings-title">
        <h2 id="provider-settings-title">Providers / Generation settings</h2>
        <p>Provider enablement and model defaults are tenant-scoped. This summary shows existing configuration.
            An operator can update credentials, provider configuration and defaults using the supported tenant provider API
            or <code>scripts/configure-tenant-provider.py</code>.</p>
        {error && <p className="settings-error" role="alert">{error}</p>}
        {!providers && !error && <p role="status">Loading provider settings…</p>}
        {providers?.length === 0 && <p>No providers configured for this tenant.</p>}
        {providers && providers.length > 0 && <ul className="settings-providers">{providers.map(provider => <li key={provider.provider}>
            <strong>{provider.provider}</strong>
            <span>{provider.enabled ? "Enabled" : "Disabled"} · Credentials {provider.has_credentials ? "configured" : "not configured"}</span>
            {provider.defaults.length ? <ul>{provider.defaults.map(value => <li key={value.media}>{value.media}: {value.model}</li>)}</ul>
                : <span>No tenant model defaults configured.</span>}
        </li>)}</ul>}
        <p>Provider secrets are not displayed or edited here. No additional generation policy is introduced by this page.</p>
    </section>;
}

export function TenantSettings() {
    const [access, setAccess] = useState<Access>({ status: "loading" });
    useEffect(() => {
        const controller = new AbortController();
        // The capability endpoint authorizes on the server before any controls render.
        void Promise.all([getTenantInfo(controller.signal), getDriveCapability(controller.signal)]).then(([tenant, capability]) => {
            if (!controller.signal.aborted) setAccess(tenant.membership.role === "admin"
                ? { status: "ready", tenant, capability }
                : { status: "error", message: "Tenant admin access required." });
        }).catch(error => {
            if (!controller.signal.aborted) setAccess({ status: "error", message: error instanceof ApiError && error.status === 403
                ? "Tenant admin access required." : safeMessage(error.message, "Unable to load Tenant Settings.") });
        });
        return () => controller.abort();
    }, []);
    if (access.status === "loading") return <section className="settings-page"><h1>Tenant Settings</h1><p role="status">Checking tenant access…</p></section>;
    if (access.status === "error") return <section className="settings-page"><h1>Tenant Settings</h1><p role="alert">{access.message}</p></section>;
    const { tenant, capability } = access;
    return <div className="settings-page">
        <header className="settings-heading"><h1>Tenant Settings</h1><p>IG-specific configuration for {tenant.name}.</p>
            <p>IS/Nexus remains authoritative for tenant identity, lifecycle, membership and platform entitlements.</p></header>
        <section className="settings-card" aria-labelledby="tenant-information-title">
            <h2 id="tenant-information-title">Tenant information</h2>
            <dl className="settings-identity"><div><dt>Name</dt><dd>{tenant.name}</dd></div>
                <div><dt>Slug</dt><dd>{tenant.slug}</dd></div><div><dt>Tenant ID</dt><dd>{tenant.tenant_id}</dd></div></dl>
            <p>This information is read-only in IG. Tenant creation, deletion, membership and platform administration belong to IS/Nexus.</p>
        </section>
        <DriveGovernance initial={capability} />
        <RetentionSettings scope="managed" />
        <RetentionSettings scope="external" />
        <ProviderSettings />
    </div>;
}
