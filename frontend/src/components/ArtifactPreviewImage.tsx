import { useState } from "react";
import { artifactPreviewUrl } from "../api/artifacts";
import { ArtifactLink } from "./ArtifactAsset";
import { useArtifactAsset } from "../hooks/useArtifactAsset";
import { isEditorRequest } from "../api/editorContext";

interface Props {
    artifactId: string;
    previewUrl?: string | null;
    originalUrl: string;
    alt: string;
    className?: string;
}

export function ArtifactPreviewImage({ artifactId, previewUrl, originalUrl, alt, className }: Props) {
    const [failed, setFailed] = useState(false);
    const [revision, setRevision] = useState(0);
    const source = useArtifactAsset(isEditorRequest() ? artifactPreviewUrl(artifactId) : previewUrl ?? artifactPreviewUrl(artifactId), revision);
    return failed || source === "" ? <div className="media-placeholder">
        <p>Preview is unavailable or still processing. Open the original explicitly, or retry the preview.</p>
        <button type="button" className="secondary-button" onClick={() => { setFailed(false); setRevision(value => value + 1); }}>Retry preview</button>
    </div> : <ArtifactLink href={originalUrl} target="_blank" rel="noreferrer">
        <img className={className} src={source} alt={alt}
            loading="lazy" decoding="async" onError={() => setFailed(true)} />
    </ArtifactLink>;
}
