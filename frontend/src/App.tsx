import { useEffect, useState } from "react";
import { RetentionSettings } from "./components/RetentionSettings";
import { AuthScreen } from "./components/AuthScreen";
import { GenerationLab } from "./components/GenerationLab";
import { Jobs } from "./components/Jobs";
import { Executions } from "./components/Executions";
import { getCurrentUser, logout, type User } from "./api/auth";
import { safeMessage } from "./api/client";
import { useGenerationFeed } from "./hooks/useGenerationFeed";

function Workspace({ user, onLogout }: { user: User; onLogout: () => Promise<void> }) {
    const [view, setView] = useState<"generate" | "jobs" | "executions" | "settings">("generate");
    const feed = useGenerationFeed();
    const [admin, setAdmin] = useState(false);
    useEffect(() => {
        let active = true;
        void fetch("/v2/tenant").then(async response => {
            if (response.ok) { const tenant = await response.json(); if (active) setAdmin(tenant.membership?.role === "admin"); }
        }).catch(() => {});
        return () => { active = false; };
    }, []);
    return <div className="app">
        <header className="app-header"><div className="header-inner">
            <div className="brand"><span className="brand-name">IG</span><span className="brand-subtitle">AI Media Control Plane</span></div>
            <nav className="app-nav" aria-label="Main navigation">
                <button className={view === "generate" ? "active" : ""} aria-current={view === "generate" ? "page" : undefined} onClick={() => setView("generate")}>Generate</button>
                <button className={view === "jobs" ? "active" : ""} aria-current={view === "jobs" ? "page" : undefined} onClick={() => { setView("jobs"); feed.refresh(); }}>Jobs</button>
                <button className={view === "executions" ? "active" : ""} aria-current={view === "executions" ? "page" : undefined} onClick={() => setView("executions")}>Executions</button>
                {admin && <button onClick={() => setView("settings")} aria-current={view === "settings" ? "page" : undefined}>Settings</button>}
            </nav>
            <div className="user-menu"><div className="user-info"><strong>{user.name || user.email}</strong>{user.name && <span>{user.email}</span>}</div>
                <button className="logout-button" onClick={() => void onLogout()}>Sign out</button></div>
        </div></header>
        <main className="main">
            <section hidden={view !== "generate"}>
                <GenerationLab results={feed.data.results} receipts={feed.receipts} loading={feed.loading} error={feed.error}
                    onRefresh={feed.refresh} onAccepted={feed.accepted} />
            </section>
            {view === "jobs" && <Jobs jobs={feed.data.jobs} error={feed.error} loading={feed.loading} onRefresh={feed.refresh} />}
            {view === "executions" && <Executions />}
            {admin && view === "settings" && <RetentionSettings />}
        </main>
    </div>;
}

function NormalApp() {
    const [user, setUser] = useState<User | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    useEffect(() => {
        let cancelled = false;
        void getCurrentUser().then(value => { if (!cancelled) setUser(value); })
            .catch(error => { if (!cancelled) setError(safeMessage(error instanceof Error ? error.message : null)); })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, []);
    async function signOut() {
        try { await logout(); }
        catch { /* Leave the workspace even if the network logout request fails. */ }
        finally { setUser(null); setError(null); }
    }
    if (loading) return <div className="auth-loading">Loading IG…</div>;
    if (!user) return <>{error && <p className="error" role="alert">{error}</p>}<AuthScreen onAuthenticated={setUser} /></>;
    // Remount all tenant/user data and cancel requests when the signed-in user changes.
    return <Workspace key={user.user_id} user={user} onLogout={signOut} />;
}

export default function App() {
    if (typeof window !== "undefined" && window.parent !== window) return <p role="alert">Launch the Advanced Generation Editor to embed IG.</p>;
    return <NormalApp />;
}
