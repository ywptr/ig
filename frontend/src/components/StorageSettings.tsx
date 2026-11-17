import { useEffect, useState } from "react";

type Storage = {
    google_drive_enabled: boolean;
    artifact_storage_preference: "ig_managed" | "google_drive";
    connection: null | { status: string; account_email: string | null; account_name: string | null;
        target_folder: string; checked_at: string | null };
};

async function request<T>(path: string, method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
    const response = await fetch(path, { method, signal, headers: body === undefined ? undefined : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body) });
    if (!response.ok) throw new Error("Unable to update storage settings. Check the connection and try again.");
    return response.json() as Promise<T>;
}

export function StorageSettings() {
    const [storage, setStorage] = useState<Storage | null>(null);
    const [error, setError] = useState("");
    const [busy, setBusy] = useState(false);
    useEffect(() => {
        const controller = new AbortController();
        void request<Storage>("/v2/user/storage", "GET", undefined, controller.signal).then(setStorage)
            .catch(() => { if (!controller.signal.aborted) setError("Unable to load storage settings."); });
        return () => controller.abort();
    }, []);
    async function change(action: string, body?: unknown) {
        setBusy(true); setError("");
        try {
            if (action === "connect") {
                const result = await request<{ authorization_url: string }>("/v2/user/storage/google-drive/connect", "POST");
                window.location.assign(result.authorization_url);
            } else {
                setStorage(await request<Storage>(action === "preference" ? "/v2/user/storage" : `/v2/user/storage/google-drive/${action}`,
                    action === "preference" ? "PATCH" : "POST", body));
            }
        } catch { setError("Unable to update storage settings. Check the connection and try again."); }
        finally { setBusy(false); }
    }
    const connected = storage?.connection;
    const driveAvailable = storage?.google_drive_enabled && connected?.status === "healthy";
    return <section aria-labelledby="storage-title">
        <h2 id="storage-title">My integrations / Storage</h2>
        {error && <p role="alert">{error}</p>}
        {!storage ? <p>Loading storage settings…</p> : <>
            <h3>Google Drive</h3>
            <p>{connected ? "Connected" : "Not connected"}</p>
            {!storage.google_drive_enabled && <p>Google Drive integration is disabled for this tenant. New Drive writes are disabled. Existing files remain available while authorization is valid.</p>}
            {connected && <>
                <p>{connected.account_name} {connected.account_email}</p>
                <p>Connection health: {connected.status.replaceAll("_", " ")}</p>
                <p>Target folder: {connected.target_folder}</p>
                <button disabled={busy} onClick={() => void change("health")}>Check connection</button>
                <button disabled={busy} onClick={() => void change("disconnect")}>Disconnect Google Drive</button>
            </>}
            <button disabled={busy || !storage.google_drive_enabled} onClick={() => void change("connect")}>{connected ? "Reconnect Google Drive" : "Connect Google Drive"}</button>
            <p>Drive authorization is separate from your IG sign-in. Replacing or disconnecting an account can make its existing files unavailable in IG.</p>
            <fieldset disabled={busy}>
                <legend>Storage preference for new artifacts</legend>
                <label><input type="radio" name="storage-preference" checked={storage.artifact_storage_preference === "ig_managed"}
                    onChange={() => void change("preference", { artifact_storage_preference: "ig_managed" })} /> IG managed</label>
                <label><input type="radio" name="storage-preference" disabled={!driveAvailable} checked={storage.artifact_storage_preference === "google_drive"}
                    onChange={() => void change("preference", { artifact_storage_preference: "google_drive" })} /> My Google Drive</label>
            </fieldset>
            {storage.artifact_storage_preference === "google_drive" && !driveAvailable && <p role="status">Drive is selected but unavailable. New writes will fail until it is available or you choose IG managed.</p>}
            <p>Changing preference affects new files only. Existing artifacts are kept in their original storage.</p>
        </>}
    </section>;
}
