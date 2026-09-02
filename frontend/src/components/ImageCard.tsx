export interface Image {
    request_id: string;
    created_at: string;
    prompt: string;
    model: string;
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

                <img
                    src={imageUrl}
                    alt={image.prompt}
                    className="image-preview"
                    loading="lazy"
                    onClick={() =>
                        window.open(
                            imageUrl,
                            "_blank"
                        )
                    }
                />

            ) : (

                <div className="image-placeholder">
                    {image.status}
                </div>

            )}


            <div className="card-info">

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