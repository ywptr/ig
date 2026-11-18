import { useEffect, useState } from "react";
import { getModelGovernance, saveModelGovernance, type ModelGovernance, type GovernedMedia, type GovernedProvider } from "../api/tenantSettings";
import { safeMessage } from "../api/client";

export function ProviderModelForm({ provider, media, onSaved }: { provider: GovernedProvider; media: GovernedMedia; onSaved: (value: ModelGovernance) => void }) {
    const [restricted, setRestricted] = useState(media.restricted);
    const [selected, setSelected] = useState(media.models.filter(model => model.enabled).map(model => model.id));
    const [defaultModel, setDefaultModel] = useState(media.default_override ?? "");
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState("");
    const [message, setMessage] = useState("");
    const eligible = media.models.filter(model => (!restricted || selected.includes(model.id))
        && model.supported !== false);
    async function save(event: React.SubmitEvent) {
        event.preventDefault(); setError(""); setMessage("");
        if (defaultModel && !eligible.some(model => model.id === defaultModel)) {
            setError("The default must be one of the enabled supported models."); return;
        }
        setSaving(true);
        try {
            const value = await saveModelGovernance(provider.id, media.media_type, restricted ? selected : null, defaultModel || null);
            const saved = value.providers.find(item => item.id === provider.id)?.media.find(item => item.media_type === media.media_type);
            if (!saved) throw new Error("IG returned an unreadable model policy.");
            setRestricted(saved.restricted); setSelected(saved.models.filter(model => model.enabled).map(model => model.id));
            setDefaultModel(saved.default_override ?? ""); onSaved(value); setMessage("Model settings saved.");
        } catch (error) { setError(safeMessage(error instanceof Error ? error.message : null, "Unable to save model settings.")); }
        finally { setSaving(false); }
    }
    return <form onSubmit={event => void save(event)} aria-label={`${provider.display_name} ${media.media_type} models`}>
        <h4>{media.media_type} models</h4>
        <p>Current default: {media.models.find(model => model.id === media.default_model)?.display_name ?? "No eligible default"}</p>
        {!provider.enabled && <p>This provider is disabled for generation. Model policy can be prepared independently.</p>}
        {!provider.has_credentials && <p>Provider credentials are not configured. Ask an operator to configure them before using these models.</p>}
        {media.configuration_error && <p role="alert">{media.configuration_error}</p>}
        <fieldset disabled={saving || !provider.has_credentials} className="settings-fields">
            <legend>Enabled models and default</legend>
            <label className="settings-check"><input type="checkbox" checked={restricted} onChange={event => { setRestricted(event.target.checked); setMessage(""); }} /> Restrict this tenant to selected models</label>
            {media.models.map(model => <label className="settings-check" key={model.id}>
                <input type="checkbox" disabled={!restricted} checked={!restricted || selected.includes(model.id)} onChange={event => {
                    setSelected(previous => event.target.checked ? [...previous, model.id] : previous.filter(id => id !== model.id)); setMessage("");
                }} />{model.display_name}
                {model.lifecycle.deprecated && <span>Deprecated{model.lifecycle.sunset_at ? ` · Sunset ${model.lifecycle.sunset_at}` : ""}</span>}
            </label>)}
            <label>Default {media.media_type} model <select value={defaultModel} onChange={event => { setDefaultModel(event.target.value); setMessage(""); }}>
                <option value="">Automatic · catalog policy</option>
                {media.models.map(model => <option key={model.id} value={model.id} disabled={!eligible.includes(model)}>{model.display_name}{model.lifecycle.deprecated ? " (deprecated)" : ""}</option>)}
            </select></label>
            <button className="secondary-button" type="submit">{saving ? "Saving…" : "Save model settings"}</button>
        </fieldset>
        <p>Unrestricted includes future locally supported models. Restrict models to keep an explicit allowlist.
            Changes apply to future submissions; queued work keeps its recorded model and fails if that model is disabled.</p>
        {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
    </form>;
}

export function ProviderModelSettings() {
    const [catalog, setCatalog] = useState<ModelGovernance | null>(null);
    const [error, setError] = useState("");
    useEffect(() => {
        const controller = new AbortController();
        void getModelGovernance(controller.signal).then(value => { if (!controller.signal.aborted) setCatalog(value); })
            .catch(error => { if (!controller.signal.aborted) setError(safeMessage(error.message, "Unable to load model governance.")); });
        return () => controller.abort();
    }, []);
    return <div aria-label="Tenant model governance">
        <h3>Model governance</h3>
        {error && <p role="alert">{error}</p>}
        {!catalog && !error && <p role="status">Loading model catalog…</p>}
        {catalog?.providers.length === 0 && <p>Configure a provider before choosing models.</p>}
        {catalog?.providers.map(provider => <div key={provider.id}>
            <h3>{provider.display_name}</h3>
            <p>{provider.enabled ? "Enabled" : "Disabled"} · Credentials {provider.has_credentials ? "configured" : "not configured"}</p>
            {provider.media.map(media => <ProviderModelForm key={media.media_type} provider={provider} media={media} onSaved={setCatalog} />)}
        </div>)}
    </div>;
}
