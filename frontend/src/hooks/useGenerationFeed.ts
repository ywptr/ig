import { useCallback, useState } from "react";
import { createGenerationLoader, EMPTY_SNAPSHOT } from "../api/generations";
import { isActiveStatus, matchesSubmission, pendingReceipt } from "../lib/generation";
import type { GenerationSubmission } from "../types/media";
import { useActivePolling } from "./useActivePolling";

export function useGenerationFeed() {
    const [loadSnapshot] = useState(createGenerationLoader);
    const [receipts, setReceipts] = useState<GenerationSubmission[]>([]);
    const load = useCallback(async (signal: AbortSignal) => {
        const snapshot = await loadSnapshot(signal);
        if (!signal.aborted) setReceipts(previous => {
            // A server Job acknowledges a local receipt once, permanently.
            const next = previous.filter(receipt => !snapshot.results.some(result => matchesSubmission(result, receipt)));
            return next.length === previous.length ? previous : next;
        });
        return snapshot;
    }, [loadSnapshot]);
    const feed = useActivePolling(load, value => value.jobs.some(job => isActiveStatus(job.status))
        || receipts.some(receipt => pendingReceipt(receipt, value.results)), EMPTY_SNAPSHOT);
    const { refresh } = feed;
    const accepted = useCallback((receipt: GenerationSubmission) => {
        setReceipts(previous => [receipt, ...previous].slice(0, 20));
        refresh();
    }, [refresh]);
    return { ...feed, receipts, accepted };
}
