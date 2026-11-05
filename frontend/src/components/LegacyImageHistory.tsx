import { useEffect, useState } from "react";
import { getImages } from "../api/images";
import { safeMessage } from "../api/client";
import ImageGrid from "./ImageGrid";
import type { Image } from "./ImageCard";

export function LegacyImageHistory() {
    const [open, setOpen] = useState(false);
    const [revision, setRevision] = useState(0);
    const [images, setImages] = useState<Image[]>([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    useEffect(() => {
        if (!open) return;
        const controller = new AbortController();
        void getImages(controller.signal).then(value => { if (!controller.signal.aborted) { setImages(value); setError(null); } })
            .catch(error => { if (!controller.signal.aborted) setError(safeMessage(error instanceof Error ? error.message : null)); })
            .finally(() => { if (!controller.signal.aborted) setLoading(false); });
        return () => controller.abort();
    }, [open, revision]);
    return <details className="legacy-history" onToggle={event => { setLoading(event.currentTarget.open); setOpen(event.currentTarget.open); }}>
        <summary>Legacy image history</summary>
        <p className="lab-help">V1 image records, including image compatibility records from recent generations. Refresh manually; videos appear above.</p>
        <button type="button" className="secondary-button" disabled={loading} onClick={() => { setLoading(true); setRevision(value => value + 1); }}>
            {loading ? "Loading…" : "Refresh image history"}</button>
        {error && <p className="error" role="alert">{error}</p>}
        <ImageGrid images={images} />
    </details>;
}
