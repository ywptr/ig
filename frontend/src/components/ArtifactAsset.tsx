import type { AnchorHTMLAttributes } from "react";
import { authenticatedOptions, isEditorRequest } from "../api/editorContext";

export function ArtifactLink({ href = "", children, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) {
    return <a {...props} href={isEditorRequest() ? undefined : href} role="link" tabIndex={0}
        onKeyDown={event => { if (isEditorRequest() && event.key === "Enter") { event.preventDefault(); event.currentTarget.click(); } }} onClick={event => {
        if (!isEditorRequest()) return;
        event.preventDefault();
        const target = window.open("about:blank", "_blank");
        if (target) target.opener = null;
        void fetch(href, authenticatedOptions(href)).then(async response => {
            if (!response.ok) throw new Error("Content unavailable");
            const url = URL.createObjectURL(await response.blob());
            if (target) target.location.href = url;
            setTimeout(() => URL.revokeObjectURL(url), 60_000);
        }).catch(() => { target?.close(); });
    }}>{children}</a>;
}
