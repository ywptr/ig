export type WorkspaceView = "generate" | "workflows" | "jobs" | "executions" | "settings" | "storage";

export function workspaceView(location: Pick<Location, "pathname" | "search">, stateView?: unknown): WorkspaceView {
    const path = location.pathname.replace(/\/$/, "");
    if (path === "/workflows") return "workflows";
    if (path === "/settings/tenant") return "settings";
    if (path === "/settings/me") return "storage";
    // Keep existing Drive OAuth return URLs working.
    if (new URLSearchParams(location.search).has("storage")) return "storage";
    if (stateView === "jobs" || stateView === "executions") return stateView;
    return "generate";
}
export function workspacePath(view: WorkspaceView): string {
    if (view === "workflows") return "/workflows";
    if (view === "settings") return "/settings/tenant";
    if (view === "storage") return "/settings/me";
    return "/";
}
