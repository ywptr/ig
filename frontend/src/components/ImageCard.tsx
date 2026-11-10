import { ArtifactPreviewImage } from "./ArtifactPreviewImage";

export interface Image {
    request_id: string;
    artifact_id?: string | null;
    created_at: string;
    prompt: string;
    model: string | null;
    status: string;
    filename: string | null;
    size_bytes: number | null;
    generation_time_ms: number | null;
}


interface ImageCardProps {
    image: Image;
}


export default function ImageCard({
    image,
}: ImageCardProps) {

    const imageUrl =
        `/v1/images/${image.request_id}/content`;


    return (
        <article className="card">

            {image.status === "completed" ? (

                image.artifact_id ? <ArtifactPreviewImage artifactId={image.artifact_id}
                    originalUrl={imageUrl} className="image-preview" alt={image.prompt} />
                    : <div className="image-placeholder">Preview unavailable for this legacy record.
                        <a href={imageUrl} target="_blank" rel="noreferrer">Open original image</a>
                    </div>

            ) : (

                <div className="image-placeholder">
                    {image.status}
                </div>

            )}


            <div className="card-info">
                {image.status === "completed" && image.artifact_id && <a href={imageUrl} target="_blank" rel="noreferrer">Open original image</a>}

                <div className="card-meta">

                    <span>
                        {new Date(
                            image.created_at
                        ).toLocaleString()}
                    </span>

                    {image.generation_time_ms && (
                        <span>
                            {(image.generation_time_ms / 1000)
                                .toFixed(1)}s
                        </span>
                    )}

                </div>


                <div className="card-prompt">
                    {image.prompt}
                </div>


                <div className="card-model">
                    {image.model}
                </div>

            </div>

        </article>
    );
}
