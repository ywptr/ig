export class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
        super(message);
        this.name = "ApiError";
        this.status = status;
    }
}

export function safeMessage(value: unknown, fallback = "Request failed. Please try again."): string {
    if (typeof value !== "string" || !value.trim() || /<[a-z!/][^>]*>|Traceback \(|\n\s*(?:File |at )|authorization\s*[:=]|api[_ -]?key\s*[:=]|bearer\s+/i.test(value)) return fallback;
    return value.slice(0, 600);
}

function errorDetail(body: unknown): string | undefined {
    if (!body || typeof body !== "object") return;
    const detail = (body as Record<string, unknown>).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
        return detail.map((item: unknown) => item && typeof item === "object"
            ? safeMessage((item as Record<string, unknown>).msg, "Invalid request field") : "Invalid request field").join("; ");
    }
    if (detail && typeof detail === "object") {
        const message = (detail as Record<string, unknown>).message;
        if (typeof message === "string") return message;
    }
}

export async function requestJSON<T>(url: string, options: RequestInit = {}): Promise<T> {
    let response: Response;
    try {
        response = await fetch(url, { ...options, credentials: "same-origin" });
    } catch (error) {
        if (options.signal?.aborted) throw error;
        throw new Error("Unable to reach IG. Check your connection.");
    }
    if (!response.ok) {
        let detail: string | undefined;
        try { detail = errorDetail(await response.json()); } catch { /* Do not display HTML error bodies. */ }
        throw new ApiError(response.status, response.status === 401
            ? "Your session has expired. Reload this page to sign in."
            : safeMessage(detail, `IG request failed (${response.status}). Please try again.`));
    }
    try { return await response.json() as T; }
    catch { throw new Error("IG returned an unreadable response. Please refresh."); }
}
