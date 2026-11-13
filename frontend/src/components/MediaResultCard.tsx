import { memo } from "react";
import { ArtifactPreviewImage } from "./ArtifactPreviewImage";
import { ArtifactLink } from "./ArtifactAsset";
import { artifactContentUrl } from "../api/artifacts";
import { safeMessage } from "../api/client";
import { humanize } from "../lib/generation";
import type { GenerationResult } from "../types/media";

function time(value: string) { return Number.isNaN(Date.parse(value)) ? "—" : new Date(value).toLocaleString(); }

export const MediaResultCard = memo(function MediaResultCard({ result }: { result: GenerationResult }) {
    const url = result.artifact ? artifactContentUrl(result.artifact.artifact_id) : null;
    return <article className="media-result">
        <div className="result-heading"><strong>{humanize(result.media_type)}</strong>
            <span className={`job-status ${result.status}`}>{humanize(result.status)}</span></div>
        {url && result.artifact ? <ArtifactPreviewImage key={result.artifact.artifact_id}
            artifactId={result.artifact.artifact_id} previewUrl={result.artifact.preview?.url}
            originalUrl={url} className="media-preview" alt={`${result.media_type} preview: ${result.prompt}`} />
            : <div className="media-placeholder">{result.status === "completed"
                ? "Output is not available yet." : result.status === "failed" ? "Generation failed"
                : result.status === "canceled" ? "Generation canceled" : result.status === "cancel_requested"
                ? "Cancellation requested" : "Generation in progress"}</div>}
        <p className="result-prompt">{result.prompt}</p>
        <dl className="result-meta">
            <div><dt>Provider</dt><dd>{result.provider ?? "Auto · awaiting resolution"}</dd></div>
            <div><dt>Model</dt><dd>{result.model ?? "—"}</dd></div>
            {result.purpose && <div><dt>Purpose</dt><dd>{humanize(result.purpose)}</dd></div>}
            <div><dt>Created</dt><dd><time dateTime={result.created_at}>{time(result.created_at)}</time></dd></div>
            {result.completed_at && <div><dt>Finished</dt><dd><time dateTime={result.completed_at}>{time(result.completed_at)}</time></dd></div>}
        </dl>
        {result.error && <p className="error" role="alert">{safeMessage(result.error)}</p>}
        {url && <ArtifactLink href={url} target="_blank" rel="noreferrer">Open {result.media_type} output</ArtifactLink>}
    </article>;
});
