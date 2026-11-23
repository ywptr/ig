import { artifactContentUrl } from "../api/artifacts";

export function ArtifactAudio({ artifactId }: { artifactId: string }) {
    return <div>
        <p>AI-generated audio</p>
        <audio aria-label="AI-generated audio" controls preload="none" src={artifactContentUrl(artifactId)} />
    </div>;
}
