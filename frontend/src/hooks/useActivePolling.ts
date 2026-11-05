import { useCallback, useEffect, useEffectEvent, useRef, useState } from "react";
import { ApiError, safeMessage } from "../api/client";
import { POLL_INTERVAL_MS } from "../lib/generation";

export function useActivePolling<T>(load: (signal: AbortSignal) => Promise<T>, shouldPoll: (value: T) => boolean, initial: T) {
    const [data, setData] = useState(initial);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [revision, setRevision] = useState(0);
    const latest = useRef(initial);
    const active = useEffectEvent(shouldPoll);
    const refresh = useCallback(() => setRevision(value => value + 1), []);
    useEffect(() => {
        const controller = new AbortController();
        let timer: number | undefined;
        async function tick() {
            setLoading(true);
            let retry = true;
            try {
                const next = await load(controller.signal);
                if (controller.signal.aborted) return;
                latest.current = next;
                setData(next);
                setError(null);
            } catch (error) {
                if (controller.signal.aborted) return;
                retry = !(error instanceof ApiError && error.status === 401);
                setError(safeMessage(error instanceof Error ? error.message : null));
            } finally {
                if (!controller.signal.aborted) setLoading(false);
            }
            // Schedule after completion, never overlap requests, never poll idle lists.
            if (!controller.signal.aborted && retry && active(latest.current)) timer = window.setTimeout(tick, POLL_INTERVAL_MS);
        }
        void tick();
        return () => { controller.abort(); window.clearTimeout(timer); };
    }, [load, revision]);
    return { data, loading, error, refresh };
}
