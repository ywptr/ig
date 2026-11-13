import { useEffect, useState } from "react";
import { authenticatedOptions, isEditorRequest } from "../api/editorContext";

export function useArtifactAsset(url: string, revision = 0) {
    const [blobUrl, setBlobUrl] = useState<string>();
    const editor = isEditorRequest();
    useEffect(() => {
        if (!editor) return;
        const controller = new AbortController();
        let objectUrl: string | undefined;
        void fetch(url, authenticatedOptions(url, { signal: controller.signal })).then(async response => {
            if (!response.ok || response.status === 202) { if (!controller.signal.aborted) setBlobUrl(""); return; }
            const blob = await response.blob();
            if (!controller.signal.aborted) { objectUrl = URL.createObjectURL(blob); setBlobUrl(objectUrl); }
        }).catch(() => { if (!controller.signal.aborted) setBlobUrl(""); });
        return () => { controller.abort(); if (objectUrl) URL.revokeObjectURL(objectUrl); };
    }, [url, editor, revision]);
    return editor ? blobUrl : url;
}
