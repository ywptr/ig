import { useState } from "react";
import { artifactPreviewUrl } from "../api/artifacts";

interface Props {
    artifactId: string;
    previewUrl?: string | null;
    originalUrl: string;
    alt: string;
    className?: string;
}

export function ArtifactPreviewImage({ artifactId, previewUrl, originalUrl, alt, className }: Props) {
    const [failed, setFailed] = useState(false);
    return failed ? <div className="media-placeholder">
        <p>Preview is unavailable or still processing. Open the original explicitly, or retry the preview.</p>
        <button type="button" className="secondary-button" onClick={() => setFailed(false)}>Retry preview</button>
    </div> : <a href={originalUrl} target="_blank" rel="noreferrer">
        <img className={className} src={previewUrl ?? artifactPreviewUrl(artifactId)} alt={alt}
            loading="lazy" decoding="async" onError={() => setFailed(true)} />
    </a>;
}
