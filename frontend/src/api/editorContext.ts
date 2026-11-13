// A browser editor credential is held only in this isolated page's memory.
// Never serialize it into URLs, storage, events or application logs.
let credential: { session: string; token: string } | null = null;
export function setEditorCredential(value: typeof credential) { credential = value; }
export function isEditorRequest() { return credential !== null; }
export function authenticatedOptions(url: string, options: RequestInit = {}): RequestInit {
    if (!credential) return { ...options, credentials: "same-origin" };
    if (!url.startsWith("/v2/") || url.startsWith("//")) throw new Error("Editor requests must use IG API paths.");
    const headers = new Headers(options.headers);
    headers.set("X-IG-Editor-Session", credential.session);
    headers.set("X-IG-Editor-Token", credential.token);
    return { ...options, headers, credentials: "omit", redirect: "error" };
}
